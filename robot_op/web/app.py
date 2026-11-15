"""Веб-интерфейс робота ОП (FastAPI + Jinja2): перечень ОП, страницы ОП и документов, запуск робота."""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import JSON, select
from sqlalchemy.orm import load_only
from starlette.concurrency import run_in_threadpool

from ..catalog import DOC_TYPE_TITLES, DOC_TYPES, LEVEL_TONE, LEVELS, REVIEW_STATUSES
from ..config import Settings, get_settings
from ..models import Database, Document, JsonVersion, Program, Run, RunItem
from ..robot.http import SiteError
from ..robot import doc_view, plan_view, page as pagebuilder
from ..extract import curriculum
from ..extract.util import discipline_name
from ..robot.page import latest_final
from ..robot.pipeline import Robot
from ..robot.worker import Worker

HERE = Path(__file__).parent
log = logging.getLogger(__name__)


def duration(seconds: float | None) -> str:
    s = int(seconds or 0)
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин {s % 60:02d} с" if s < 600 else f"{s // 60} мин"
    return f"{s // 3600} ч {s % 3600 // 60:02d} мин"


def clock_hours(hours: float | None) -> str:
    """Обычное время занятий; округление только при показе, до целой минуты."""
    minutes = round((hours or 0) * 60)
    h, m = divmod(minutes, 60)
    return " ".join(x for x in (f"{h} ч" if h or not m else "", f"{m} мин" if m else "") if x)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Окончание по числу: 1 дисциплинА, 2 дисциплинЫ, 5 дисциплин."""
    n = abs(int(n or 0))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
               llm_transport: httpx.BaseTransport | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.data_path.mkdir(parents=True, exist_ok=True)
    if settings.app.demo:
        transport = transport or _setup_demo(settings)
    db = Database(settings.app.database_url)
    db.create_all()
    robot = Robot(settings, db, transport=transport, llm_transport=llm_transport)
    worker = Worker(robot)
    submission_lock = Lock()

    catalog_lock = Lock()
    catalog_path = settings.data_path / "program-catalog.json"
    catalog_state: tuple[list[dict], dict[str, list[int]]] = ([], {})
    catalog_checks: dict[int | None, tuple[float, bool]] = {}

    def refresh_catalog(cohort: int | None = None) -> tuple[list[dict], dict[str, list[int]], bool]:
        nonlocal catalog_state
        with catalog_lock:
            previous, previous_years = catalog_state
            checked_at, incomplete = catalog_checks.get(cohort, (float("-inf"), True))
            if time.monotonic() - checked_at < (30 if incomplete else 600):
                return previous, previous_years, incomplete
            try:
                remote, years, incomplete = robot.catalog(cohort)
            except SiteError as exc:
                log.warning("Каталог ИРНИТУ недоступен: %s", exc)
                remote, years, incomplete = [], {}, True
            cohorts = {(level, cohort if cohort is not None else max(available))
                       for level, available in years.items() if available and (cohort is None or cohort in available)}
            rows = {row["key"]: row for row in previous
                    if incomplete or (row["level"], row["year"]) not in cohorts}
            for level, year, block in remote:
                rows[block.key] = dict(block.as_dict(), level=level, level_title=block.level_title, year=block.year or year)
            updated = list(rows.values())
            years = years or previous_years
            if (updated, years) != catalog_state:
                temporary = catalog_path.with_suffix(".tmp")
                try:
                    temporary.write_text(json.dumps({"rows": updated, "years": years}, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(catalog_path)
                except OSError:
                    log.warning("Не удалось сохранить каталог ИРНИТУ", exc_info=True)
            catalog_state = (updated, years)
            catalog_checks[cohort] = (time.monotonic(), incomplete)
            return updated, years, incomplete

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal catalog_state
        robot.mark_interrupted()      # запуски, оборванные остановкой приложения, можно продолжить
        if start_worker:
            try:
                saved = json.loads(catalog_path.read_text(encoding="utf-8"))
                rows = saved.get("rows") if isinstance(saved, dict) else saved
                years = saved.get("years", {}) if isinstance(saved, dict) else {}
                required = {"key", "title", "level", "level_title", "year", "code", "direction", "form"}
                if isinstance(rows, list) and all(isinstance(row, dict) and required <= row.keys() for row in rows):
                    catalog_state = (rows, years if isinstance(years, dict) else {})
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                log.warning("Не удалось прочитать сохранённый каталог ИРНИТУ", exc_info=True)
            Thread(target=refresh_catalog, name="robot-catalog", daemon=True).start()
            worker.start()
            with db.Session() as ses:
                queued = ses.scalars(select(Run.id).where(
                    Run.status == "queued", Run.params["only_new"].as_boolean().is_(True)).order_by(Run.id))
                for run_id in queued:
                    worker.submit(run_id)
        yield

    app = FastAPI(title="Робот ОП ИРНИТУ", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.settings, app.state.db, app.state.robot, app.state.worker = settings, db, robot, worker
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["plural"] = plural
    templates.env.filters["clock_hours"] = clock_hours
    # версия стилей в адресе — после обновления приложения браузер не берёт старый CSS из кэша
    templates.env.globals["asset_v"] = int(max(p.stat().st_mtime for p in (HERE / "static" / "css").glob("*.css")))
    if settings.app.demo:
        from demo_site.site import router as demo_router
        app.include_router(demo_router())

    def theme_of(request: Request) -> str:
        t = request.query_params.get("theme") or request.cookies.get("theme") or "light"
        return t if t in ("light", "dark") else "light"

    def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("active_nav", "")
        ctx.setdefault("meta", [])
        ctx.update(theme=theme_of(request), demo=settings.app.demo, request=request)
        return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)

    def crumbs(*items) -> list[dict]:
        return [{"label": "Робот ОП", "href": "/"}] + [{"label": label, "href": href} for label, href in items]

    def not_found(request: Request, message: str) -> HTMLResponse:
        return render(request, "error.html", 404, page_title="Не найдено", crumbs=crumbs(("Не найдено", None)),
                      message=message)

    def run_summary(ses, run: Run) -> dict:
        return {"run": run, "counts": Robot.counts(ses, run.id), "duration": duration(run.seconds)}

    def requested_keys(params: dict) -> list[str]:
        return [key for selection in (params.get("selections") or [params])
                for key in selection.get("programs", []) if key != "*"]

    def unavailable_programs() -> set[str]:
        with db.Session() as ses:
            active = ses.scalars(select(Run.params).where(Run.status.in_(("queued", "running"))))
            pending = {key for params in active for key in requested_keys(params)}
            saved = ses.scalars(select(Program.id).where(Program.page.is_not(None), Program.page != JSON.NULL))
            return pending.union(saved)

    def submit_new_programs(selections: list[dict]) -> int | None:
        candidates = []
        for selection in selections:
            level, year = selection["level"], selection["year"]
            requested = set(selection["programs"])
            keys = [block.key for block in robot.programs(level, year) if "*" in requested or block.key in requested]
            candidates.append(dict(selection, programs=keys))
        return enqueue_new_programs(candidates)

    def enqueue_new_programs(candidates: list[dict]) -> int | None:
        with submission_lock:
            unavailable = unavailable_programs()
            fresh = []
            for selection in candidates:
                keys = [key for key in selection["programs"] if key not in unavailable]
                if keys:
                    fresh.append(dict(selection, programs=keys))
                    unavailable.update(keys)
            if not fresh:
                return None
            run_id = robot.create_batch_run(fresh, only_new=True)
            worker.submit(run_id)
            return run_id

    def start_selection(requested: dict[str, list[str]]) -> int | None:
        return submit_new_programs([
            {"level": level, "year": robot.latest_year(level), "programs": keys}
            for level, keys in requested.items()
        ])

    # ---------------------------------------------------------------- страницы

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/run", status_code=302)

    @app.get("/theme", include_in_schema=False)
    def set_theme(mode: str = "light", next: str = "/"):
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        r = RedirectResponse(target, status_code=302)
        r.set_cookie("theme", "dark" if mode == "dark" else "light", max_age=365 * 86400, samesite="lax")
        return r

    @app.get("/run/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def run_details(request: Request, run_id: int):
        with db.Session() as ses:
            run = ses.get(Run, run_id)
            if run is None or not run.params.get("only_new"):
                return not_found(request, "Загрузка не найдена.")
            keys = requested_keys(run.params)
            program = ses.get(Program, keys[0]) if len(keys) == 1 else None
            if program and program.page:
                return RedirectResponse(f"/op/{program.id}", status_code=302)
            details = run_summary(ses, run)
        return render(request, "run.html", page_title="Образовательные программы",
                      crumbs=crumbs(("Программы", "/run"), ("Ход обработки", None)),
                      active_nav="programs", details=details, program=program)

    @app.post("/run", include_in_schema=False)
    async def start_run(request: Request):
        form = await request.form()
        if "program_id" in form:
            level, program_id = form.get("level"), form.get("program_id")
            if not isinstance(level, str) or not isinstance(program_id, str):
                raise HTTPException(400, "Нужны уровень образования и программа")
            return await run_in_threadpool(load_program, level, program_id)
        levels = list(dict.fromkeys(form.getlist("level")))
        if any(lv not in LEVELS for lv in levels):
            raise HTTPException(400, "Неизвестный уровень образования")
        requested = {}
        for lv in levels:
            chosen = form.getlist("programs__" + lv)
            if len(levels) == 1 and not chosen:
                chosen = ["*"] if form.get("all") else form.getlist("programs")
            if chosen:
                requested[lv] = chosen
        message = ""
        if requested:
            try:
                run_id = await run_in_threadpool(start_selection, requested)
                if run_id is not None:
                    return RedirectResponse(f"/run/{run_id}", status_code=303)
                message = "Нет выбранных программ, доступных для загрузки."
            except SiteError as e:
                message = str(e)
        else:
            message = "Выберите хотя бы одну программу."
        return RedirectResponse("/run?" + urlencode({"message": message}), status_code=303)

    @app.post("/run/{run_id}/resume", include_in_schema=False)
    def resume(run_id: int):
        with submission_lock, db.Session() as ses:
            run = ses.get(Run, run_id)
            if run is None or not run.params.get("only_new"):
                raise HTTPException(404)
            if run.status in ("interrupted", "failed"):
                run.status = "queued"
                run.stage = "В очереди на продолжение"
                ses.commit()
                worker.submit(run_id)
        return RedirectResponse(f"/run/{run_id}", status_code=303)

    @app.post("/run/{run_id}/stop", include_in_schema=False)
    def stop(run_id: int):
        if worker.current == run_id:
            worker.stop_current()
        return RedirectResponse(f"/run/{run_id}", status_code=303)

    def catalog_groups(level: str, remote: list[dict], cohort: int | None = None) -> list[dict]:
        entries = {
            row["key"]: {"id": row["key"], "name": row["title"], "level": row["level"],
                         "level_title": row["level_title"], "year": row["year"], "code": row["code"],
                         "direction": row["direction"], "form": row["form"], "ready": False}
            for row in remote if (not level or row["level"] == level) and (cohort is None or row["year"] == cohort)
        }
        with db.Session() as ses:
            query = select(Program).where(Program.page.is_not(None), Program.page != JSON.NULL).options(
                load_only(Program.id, Program.profile, Program.block_title, Program.level, Program.level_title,
                          Program.year, Program.code, Program.direction, Program.form, Program.page_updated_at))
            if level:
                query = query.where(Program.level == level)
            if cohort is not None:
                query = query.where(Program.year == cohort)
            for p in ses.scalars(query):
                entries[p.id] = {"id": p.id, "name": p.profile or p.block_title, "level": p.level,
                                 "level_title": p.level_title, "year": p.year, "code": p.code,
                                 "direction": p.direction, "form": p.form, "ready": True,
                                 "updated_at": p.page_updated_at}
        order = {key: index for index, key in enumerate(LEVELS)}
        groups = {}
        for entry in sorted(entries.values(), key=lambda p: (order.get(p["level"], len(order)), -p["year"],
                                                              p["code"], p["name"].casefold())):
            key = entry["level"], entry["year"]
            title = f'{entry["level_title"]}, набор {entry["year"]}'
            group = groups.setdefault(key, {"title": title, "year": entry["year"], "level": entry["level"],
                                            "level_title": entry["level_title"], "programs": []})
            group["programs"].append(entry)
        return list(groups.values())

    def catalog_availability(remote: list[dict], years: dict[str, list[int]]) -> dict[int, dict[str, str]]:
        by_year: dict[int, dict[str, str]] = {}
        for level, title in LEVELS.items():
            for year in years.get(level, []):
                by_year.setdefault(year, {})[level] = title
        for row in remote:
            by_year.setdefault(row["year"], {})[row["level"]] = row["level_title"]
        with db.Session() as ses:
            saved = ses.execute(select(Program.year, Program.level, Program.level_title).where(
                Program.page.is_not(None), Program.page != JSON.NULL).distinct())
            for year, level, title in saved:
                by_year.setdefault(year, {})[level] = title
        return by_year

    @app.get("/run", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/programs", response_class=HTMLResponse, include_in_schema=False)
    def programs_page(request: Request, level: str = "", message: str = ""):
        remote, years = catalog_state
        return render(request, "programs.html", page_title="Образовательные программы",
                      crumbs=crumbs(("Программы", "/programs" if level else None)) +
                      ([{"label": LEVELS[level], "href": None}] if level in LEVELS else []),
                      active_nav="programs", groups=catalog_groups(level, remote), tones=LEVEL_TONE,
                      level=level, level_title=LEVELS.get(level, ""), message=message,
                      catalog_years=catalog_availability(remote, years))

    @app.get("/programs/catalog", response_class=HTMLResponse, include_in_schema=False)
    def programs_catalog(request: Request, level: str = "", year: int | None = None):
        remote, years, incomplete = refresh_catalog(year)
        return render(request, "program_cards.html", groups=catalog_groups(level, remote, year), tones=LEVEL_TONE,
                      catalog_years=catalog_availability(remote, years),
                      catalog_empty="Список программ за этот год пока недоступен." if year is not None and incomplete else "")

    def program_destination(program_id: str) -> str | None:
        with db.Session() as ses:
            runs = ses.scalars(select(Run).where(Run.status.in_(("queued", "running")),
                                                  Run.params["only_new"].as_boolean().is_(True))).all()
            program = ses.get(Program, program_id)
            if program and program.page:
                return f"/op/{program.id}"
            return next((f"/run/{r.id}" for r in runs if program_id in requested_keys(r.params)), None)

    @app.post("/programs/load", include_in_schema=False)
    def load_program(level: str = Form(), program_id: str = Form()):
        if level not in LEVELS:
            raise HTTPException(400, "Неизвестный уровень образования")
        if target := program_destination(program_id):
            return RedirectResponse(target, status_code=303)
        try:
            entry = next((row for row in catalog_state[0] if row["key"] == program_id and row["level"] == level), None)
            if entry is None:
                run_id = start_selection({level: [program_id]})
            else:
                run_id = enqueue_new_programs([{"level": level, "year": entry["year"], "programs": [program_id]}])
        except SiteError:
            message = "Сайт ИРНИТУ не ответил. Попробуйте открыть программу позже."
        else:
            if run_id is not None:
                return RedirectResponse(f"/run/{run_id}", status_code=303)
            if target := program_destination(program_id):
                return RedirectResponse(target, status_code=303)
            message = "Программа недоступна для загрузки."
        return RedirectResponse("/run?" + urlencode({"message": message}), status_code=303)

    @app.get("/op/{program_id}", response_class=HTMLResponse, include_in_schema=False)
    def op_page(request: Request, program_id: str):
        with db.Session() as ses:
            prog = ses.get(Program, program_id)
            if prog is None or not prog.page:
                return not_found(request, "Образовательная программа не найдена или её страница ещё не сформирована роботом.")
            ctx = dict(prog.page)
            ctx["op"] = dict(ctx["op"], updated_at=prog.page_updated_at.strftime("%d.%m.%Y") if prog.page_updated_at else "")
            gia = ctx.get("gia") or {}
            ctx["gia"] = dict(gia, summary=pagebuilder.gia_characteristic(gia.get("summary")))
        return render(request, "op_page.html", active_nav="programs", **ctx)

    @app.get("/op/{program_id}/doc/{doc_id}", response_class=HTMLResponse, include_in_schema=False)
    def doc_page(request: Request, program_id: str, doc_id: int):
        with db.Session() as ses:
            doc = ses.get(Document, doc_id)
            if doc is None or doc.program_id != program_id:
                return not_found(request, "Документ не найден.")
            if doc.doc_type in ("gia", "fos_gia"):
                return RedirectResponse(f"/op/{program_id}#gia", status_code=302)
            prog = ses.get(Program, program_id)
            final = latest_final(ses, doc_id)
        view = doc_view.build(doc.doc_type, (final or {}).get("data") or {})
        return render(request, "doc.html", page_title=doc.title,
                      crumbs=crumbs(("Программы", "/programs"), (prog.code, f"/op/{prog.id}"), (DOC_TYPES[doc.doc_type], None)),
                      meta=[DOC_TYPE_TITLES[doc.doc_type], f"{prog.code} {prog.profile}"],
                      active_nav="programs", doc=doc, program=prog, final=final, view=view,
                      type_title=DOC_TYPE_TITLES[doc.doc_type])

    @app.get("/op/{program_id}/discipline", response_class=HTMLResponse, include_in_schema=False)
    def discipline_page(request: Request, program_id: str, index: str):
        with db.Session() as ses:
            prog = ses.get(Program, program_id)
            if prog is None:
                return not_found(request, "Образовательная программа не найдена.")
            docs = ses.scalars(select(Document).where(Document.program_id == program_id)).all()
            finals = {doc.id: latest_final(ses, doc.id) for doc in docs}
            source = next((doc for doc in docs if doc.doc_type == "up"
                           and ((finals.get(doc.id) or {}).get("data") or {}).get("rows")), None)
            up_data = finals[source.id]["data"] if source else {}
            rows = up_data.get("rows", [])
            weeks = {s["number"]: s["weeks"] for s in up_data.get("semesters", [])}
            row = next((r for r in rows if r.get("index") == index
                        and (not r.get("is_module") or plan_view.is_choice(index))), None)
            if row is None:
                return not_found(request, "Дисциплина не найдена в учебном плане.")
            links = pagebuilder.discipline_links(program_id, rows, docs, finals)
            options = [{"name": discipline_name(r["name"]), "url": links[pagebuilder._key(r["name"])]}
                       for r in rows if r.get("index", "").startswith(index + ".")
                       and (not r.get("is_module") or plan_view.is_choice(r["index"]))]
            target = links.get(pagebuilder._key(row["name"]))
            if not options and target and target.startswith(f"/op/{program_id}/doc/"):
                return RedirectResponse(target, status_code=302)
            sems = sorted(set(int(s) for s in row.get("semesters", {})) |
                          {s for values in row.get("control", {}).values() for s in values})
            load = []
            for sem in sems:
                hours = row.get("semesters", {}).get(str(sem), {})
                pairs = {k: round((hours.get(k) or 0) / 2, 1) for k in ("lec", "pr", "lab")}
                load.append({"number": sem, "weeks": weeks.get(sem), **pairs, "pairs": round(sum(pairs.values()), 1),
                             "control": curriculum.control_label(row.get("control", {}), sem)})
            hours = {"total": row.get("hours", {}).get("plan"), "sr": row.get("hours", {}).get("sr")}
            view = doc_view.build("rpd", {"ze": row.get("ze"), "hours": hours, "semesters": sems})
        return render(request, "discipline.html", page_title=discipline_name(row["name"]),
                      crumbs=crumbs(("Программы", "/programs"), (prog.code, f"/op/{prog.id}"), (discipline_name(row["name"]), None)),
                      meta=[prog.profile, "Из учебного плана"], active_nav="programs",
                      program=prog, source=source, facts=view["facts"], load=load, options=options)

    @app.get("/documents/{doc_id}/pdf", include_in_schema=False)
    def doc_pdf(doc_id: int):
        with db.Session() as ses:
            doc = ses.get(Document, doc_id)
        if doc is None or not doc.file_path or not Path(doc.file_path).exists():
            raise HTTPException(404, "Документ не скачан")
        return FileResponse(doc.file_path, media_type="application/pdf", filename=f"{doc.doc_type}-{doc.id}.pdf")

    # ---------------------------------------------------------------- API

    @app.get("/api/site/levels", tags=["Сайт ИРНИТУ"], summary="Уровни образования и годы набора")
    def api_levels():
        try:
            return [{"level": lv.level, "title": lv.title, "years": sorted(lv.years)} for lv in robot.levels()]
        except SiteError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/site/programs", tags=["Сайт ИРНИТУ"], summary="Перечень ОП уровня и года набора")
    def api_site_programs(level: str, year: int | None = None, new_only: bool = False):
        try:
            blocks = robot.programs(level, year if year is not None else robot.latest_year(level))
        except SiteError as e:
            raise HTTPException(502, str(e))
        unavailable = unavailable_programs() if new_only else set()
        return [b.as_dict() for b in blocks if b.key not in unavailable]

    @app.post("/api/runs", tags=["Запуски"], summary="Загрузить новые программы последнего года набора")
    def api_start(payload: dict):
        level = payload.get("level")
        if not isinstance(level, str) or level not in LEVELS:
            raise HTTPException(400, "Нужен level: bachelor, specialist, master, spo или postgraduate")
        requested = payload.get("programs", ["*"])
        if not isinstance(requested, list) or any(not isinstance(key, str) for key in requested):
            raise HTTPException(400, "programs должен быть списком идентификаторов программ")
        try:
            year = robot.latest_year(level)
            run_id = submit_new_programs([{"level": level, "year": year, "programs": requested or ["*"]}])
        except SiteError as e:
            raise HTTPException(502, str(e))
        if run_id is None:
            raise HTTPException(409, "Нет выбранных программ, доступных для загрузки.")
        return {"id": run_id, "year": year}

    @app.get("/api/runs/{run_id}", tags=["Запуски"], summary="Состояние запуска")
    def api_run(run_id: int):
        with db.Session() as ses:
            run = ses.get(Run, run_id)
            if run is None:
                raise HTTPException(404)
            return {"id": run.id, "status": run.status, "stage": run.stage, "error": run.error, "params": run.params,
                    "counts": Robot.counts(ses, run_id), "seconds": round(run.seconds, 1)}

    @app.get("/api/runs/{run_id}/report", tags=["Запуски"], summary="Отчёт о запуске")
    def api_report(run_id: int):
        with db.Session() as ses:
            run = ses.get(Run, run_id)
            if run is None:
                raise HTTPException(404)
            items = ses.scalars(select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.id)).all()
            data = {
                "run": run.id, "status": run.status, "params": run.params, "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_seconds": round(run.seconds, 1), "counts": Robot.counts(ses, run_id),
                "needs_review": [{"program": i.program_id, "type": DOC_TYPES[i.doc_type], "title": i.title, "status": i.status,
                                  "note": i.note, "url": i.url} for i in items if i.status in REVIEW_STATUSES],
                "documents": [{"program": i.program_id, "type": DOC_TYPES[i.doc_type], "title": i.title, "status": i.status,
                               "note": i.note, "url": i.url, "seconds": round(i.seconds, 1)} for i in items],
            }
        return _json_download(data, f"report-{run_id}.json")

    @app.get("/api/programs/{program_id}", tags=["ОП"], summary="Контекст веб-страницы ОП")
    def api_program(program_id: str):
        with db.Session() as ses:
            prog = ses.get(Program, program_id)
            if prog is None or not prog.page:
                raise HTTPException(404)
            return prog.page

    @app.get("/api/programs/{program_id}/documents", tags=["ОП"], summary="Итоговые JSON-файлы документов ОП")
    def api_program_docs(program_id: str, type: str = ""):
        with db.Session() as ses:
            q = select(Document).where(Document.program_id == program_id)
            if type:
                q = q.where(Document.doc_type == type)
            docs = ses.scalars(q.order_by(Document.id)).all()
            return [{"id": d.id, "type": d.doc_type, "title": d.title, "status": d.status, "note": d.note, "url": d.url,
                     "final": latest_final(ses, d.id)} for d in docs]

    @app.get("/api/documents/{doc_id}/json", tags=["ОП"], summary="Первичный или итоговый JSON-файл документа")
    def api_doc_json(doc_id: int, kind: str = "final", version: int | None = None):
        with db.Session() as ses:
            q = select(JsonVersion).where(JsonVersion.document_id == doc_id, JsonVersion.kind == kind)
            q = q.where(JsonVersion.version == version) if version else q.order_by(JsonVersion.version.desc())
            v = ses.scalar(q.limit(1))
            if v is None:
                raise HTTPException(404, "JSON-файл не найден")
            return _json_download(v.data, f"doc-{doc_id}-{kind}-v{v.version}.json", inline=True)

    return app


def _json_download(data, filename: str, inline: bool = False) -> Response:
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    disp = "inline" if inline else "attachment"
    return Response(body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'{disp}; filename="{filename}"'})


def _setup_demo(settings: Settings) -> httpx.BaseTransport:
    """Демо-режим: робот обращается к встроенному демо-сайту без сети, ссылки на документы открываются в браузере."""
    from demo_site.site import mock_transport

    if "istu.edu" in settings.site.base_url:
        settings.site.base_url = "http://localhost:8000/demo-site"
    host = urlparse(settings.site.base_url).hostname or "localhost"
    settings.site.allowed_hosts = list({*settings.site.allowed_hosts, host})
    settings.site.allow_http_hosts = list({*settings.site.allow_http_hosts, host})
    settings.site.requests_per_second = max(settings.site.requests_per_second, 20.0)
    return mock_transport()


app = None


def get_app() -> FastAPI:
    """Точка входа uvicorn: uvicorn robot_op.web.app:get_app --factory"""
    return create_app()
