"""Веб-интерфейс робота ОП (FastAPI + Jinja2): перечень ОП, страницы ОП и документов, запуск робота."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse, urlencode

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        robot.mark_interrupted()      # запуски, оборванные остановкой приложения, можно продолжить
        if start_worker:
            worker.start()
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

    # ---------------------------------------------------------------- страницы

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/programs", status_code=302)

    @app.get("/theme", include_in_schema=False)
    def set_theme(mode: str = "light", next: str = "/"):
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        r = RedirectResponse(target, status_code=302)
        r.set_cookie("theme", "dark" if mode == "dark" else "light", max_age=365 * 86400, samesite="lax")
        return r

    @app.get("/run", response_class=HTMLResponse, include_in_schema=False)
    def run_page(request: Request, level: list[str] = Query(default=[]), message: str = ""):
        site_error = ""
        levels, groups = [], []
        try:
            levels = robot.levels()
        except SiteError as e:
            site_error = str(e)
        for lv in levels:
            year = max(lv.years) if lv.years else None
            programs = None
            error = ""
            if lv.level in level and year:
                try:
                    programs = [b.as_dict() for b in robot.programs(lv.level, year)]
                except SiteError as e:
                    error = str(e)
            groups.append({"level": lv.level, "title": lv.title, "year": year,
                           "selected": lv.level in level, "programs": programs, "error": error})
        with db.Session() as ses:
            last_run = ses.scalar(select(Run).order_by(Run.id.desc()).limit(1))
            last = run_summary(ses, last_run) if last_run else None
            on_site = {p.id: p.page_updated_at.strftime("%d.%m.%Y") for p in
                       ses.scalars(select(Program).where(Program.page.is_not(None))) if p.page_updated_at}
        return render(request, "run.html", page_title="Обновление данных", crumbs=crumbs(("Обновление данных", None)),
                      active_nav="run", groups=groups, site_error=site_error, message=message, last=last, on_site=on_site)

    @app.post("/run", include_in_schema=False)
    async def start_run(request: Request):
        form = await request.form()
        levels = list(dict.fromkeys(form.getlist("level")))
        if any(lv not in LEVELS for lv in levels):
            raise HTTPException(400, "Неизвестный уровень образования")
        target = "/run" + ("?" + urlencode({"level": levels}, doseq=True) if levels else "")
        selections = []
        try:
            for lv in levels:
                chosen = list(dict.fromkeys(form.getlist("programs__" + lv)))
                # Совместимость с прежней формой; принудительный разбор посетитель не выбирает.
                if len(levels) == 1 and not chosen:
                    chosen = ["*"] if form.get("all") else form.getlist("programs")
                if chosen:
                    selections.append({"level": lv, "year": robot.latest_year(lv), "programs": chosen})
        except SiteError as e:
            return RedirectResponse(target + ("&" if "?" in target else "?") + "message=" + quote(str(e)), status_code=303)
        if not selections:
            return RedirectResponse(target + ("&" if "?" in target else "?") + "message=Выберите+хотя+бы+одну+ОП", status_code=303)
        run_id = robot.create_batch_run(selections)
        worker.submit(run_id)
        return RedirectResponse(target, status_code=303)

    @app.post("/run/{run_id}/resume", include_in_schema=False)
    def resume(run_id: int):
        with db.Session() as ses:
            run = ses.get(Run, run_id)
            if run is None:
                raise HTTPException(404)
            if run.status in ("interrupted", "failed"):
                run.status = "queued"
                run.stage = "В очереди на продолжение"
                ses.commit()
                worker.submit(run_id)
        return RedirectResponse("/run", status_code=303)

    @app.post("/run/{run_id}/stop", include_in_schema=False)
    def stop(run_id: int):
        if worker.current == run_id:
            worker.stop_current()
        return RedirectResponse("/run", status_code=303)

    @app.get("/programs", response_class=HTMLResponse, include_in_schema=False)
    def programs_page(request: Request, level: str = ""):
        """Программы последнего года набора каждого уровня, у которых есть веб-страница."""
        with db.Session() as ses:
            q = select(Program).where(Program.page.is_not(None))
            if level:
                q = q.where(Program.level == level)
            progs = ses.scalars(q).all()
        latest = {}
        for p in progs:
            latest[p.level] = max(latest.get(p.level, 0), p.year)
        groups: list[dict] = []
        order = list(LEVELS)
        for p in sorted((p for p in progs if p.year == latest[p.level]),
                        key=lambda p: (order.index(p.level) if p.level in order else 9, p.code, p.profile or "")):
            title = f"{p.level_title}, набор {p.year}"
            if not groups or groups[-1]["title"] != title:
                groups.append({"title": title, "programs": []})
            groups[-1]["programs"].append(p)
        title = "Образовательные программы" + (f": {LEVELS[level]}" if level in LEVELS else "")
        return render(request, "programs.html", page_title=title,
                      crumbs=crumbs(("Программы", "/programs" if level else None)) + ([{"label": LEVELS[level], "href": None}] if level in LEVELS else []),
                      active_nav="programs", groups=groups, tones=LEVEL_TONE, level=level)

    @app.get("/op/{program_id}", response_class=HTMLResponse, include_in_schema=False)
    def op_page(request: Request, program_id: str):
        with db.Session() as ses:
            prog = ses.get(Program, program_id)
            if prog is None or not prog.page:
                return not_found(request, "Образовательная программа не найдена или её страница ещё не сформирована роботом.")
            ctx = dict(prog.page)
            ctx["op"] = dict(ctx["op"], updated_at=prog.page_updated_at.strftime("%d.%m.%Y") if prog.page_updated_at else "")
        return render(request, "op_page.html", active_nav="programs", **ctx)

    @app.get("/op/{program_id}/doc/{doc_id}", response_class=HTMLResponse, include_in_schema=False)
    def doc_page(request: Request, program_id: str, doc_id: int):
        with db.Session() as ses:
            doc = ses.get(Document, doc_id)
            if doc is None or doc.program_id != program_id:
                return not_found(request, "Документ не найден.")
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
            rows = (finals[source.id]["data"]["rows"] if source else [])
            row = next((r for r in rows if r.get("index") == index
                        and (not r.get("is_module") or plan_view.is_choice(index))), None)
            if row is None:
                return not_found(request, "Дисциплина не найдена в учебном плане.")
            links = pagebuilder.discipline_links(program_id, rows, docs, finals)
            options = [{"name": discipline_name(r["name"]), "url": links[pagebuilder._key(r["name"])]}
                       for r in rows if r.get("index", "").startswith(index + ".")]
            target = links.get(pagebuilder._key(row["name"]))
            if not options and target and target.startswith(f"/op/{program_id}/doc/"):
                return RedirectResponse(target, status_code=302)
            sems = sorted(set(int(s) for s in row.get("semesters", {})) |
                          {s for values in row.get("control", {}).values() for s in values})
            load = []
            for sem in sems:
                hours = row.get("semesters", {}).get(str(sem), {})
                pairs = {k: round((hours.get(k) or 0) / 2, 1) for k in ("lec", "pr", "lab")}
                load.append({"number": sem, **pairs, "pairs": round(sum(pairs.values()), 1),
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
    def api_site_programs(level: str, year: int):
        try:
            return [b.as_dict() for b in robot.programs(level, year)]
        except SiteError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/runs", tags=["Запуски"], summary="Запустить робота (последний год набора уровня)")
    def api_start(payload: dict):
        level = payload.get("level")
        if level not in LEVELS:
            raise HTTPException(400, "Нужен level: bachelor, specialist, master, spo или postgraduate")
        try:
            year = robot.latest_year(level)
        except SiteError as e:
            raise HTTPException(502, str(e))
        run_id = robot.create_run(level, year, payload.get("programs") or ["*"], bool(payload.get("force")))
        worker.submit(run_id)
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
