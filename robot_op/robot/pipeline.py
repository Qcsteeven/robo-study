"""Робот ОП: последовательность действий из таблицы 1 ТЗ.

документ → первичный JSON-файл → модуль на основе LLM → итоговый JSON-файл → веб-страница ОП.
Ошибка при обработке одного документа не прерывает запуск; прерванный запуск продолжается
с места остановки без повторного скачивания уже обработанных документов.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime
from email.utils import formatdate
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urljoin

import httpx
from sqlalchemy import delete, func, select

from ..catalog import DOC_TYPES, LEVELS, REQUIRED_TYPES, REVIEW_STATUSES, SUMMARY_TYPES
from ..config import Settings
from ..extract import curriculum, schedule, sections
from ..extract import pdf as pdfmod
from ..extract.ocr import OcrConfig, OcrUnavailable, ocr_page_text
from ..models import Database, Document, JsonVersion, LogEntry, Program, Run, RunItem, now
from . import page as pagebuilder
from .http import SiteClient, SiteError
from .llm import LlmClient, LlmError
from .site import LevelYears, ProgramBlock, parse_education, parse_op_list

PARSER_VERSION = "1.0"
log = logging.getLogger("robot_op")


class RunStopped(Exception):
    pass


class Robot:
    def __init__(self, settings: Settings, db: Database, transport: httpx.BaseTransport | None = None,
                 llm_transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.s = settings
        self.db = db
        self.transport = transport
        self.llm = LlmClient(settings.llm, transport=llm_transport)
        self.ocr = OcrConfig(settings.ocr.tesseract_cmd, settings.ocr.lang)
        self.sleep = sleep
        self.docs_dir = settings.data_path / "docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, object]] = {}
        self._stop = threading.Event()
        self._setup_file_log()

    # ------------------------------------------------------------------ журнал

    def _setup_file_log(self) -> None:
        logs = self.s.data_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = str(logs / "robot.log")
        if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == path for h in log.handlers):
            h = RotatingFileHandler(path, maxBytes=5 * 2**20, backupCount=5, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
            log.setLevel(logging.INFO)

    def journal(self, run_id: int | None, level: str, message: str, program_id: str = "", document: str = "",
                ses=None) -> None:
        """Запись в журнал (таблица log_entries и файл data/logs/robot.log). ses — текущая транзакция, если она открыта."""
        log.log(getattr(logging, level), "[run %s] %s%s", run_id, f"{document}: " if document else "", message)
        entry = LogEntry(run_id=run_id, level=level, message=message, program_id=program_id, document=document[:1000])
        if ses is not None:
            ses.add(entry)
            return
        with self.db.Session() as own:
            own.add(entry)
            own.commit()

    # ------------------------------------------------------------------ сайт

    def site(self, run_id: int | None = None) -> SiteClient:
        return SiteClient(self.s.site, transport=self.transport, sleep=self.sleep,
                          log=lambda level, msg: self.journal(run_id, level, msg))

    def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (time.monotonic(), value)
        return value

    def education_url(self) -> str:
        return urljoin(self.s.site.base_url.rstrip("/") + "/", self.s.site.education_path.lstrip("/"))

    def levels(self) -> list[LevelYears]:
        """Уровни образования и годы набора — из раздела «Образование» сайта (ТЗ, шаг 1)."""
        def load():
            client = self.site()
            try:
                page = client.get(self.education_url())
                return parse_education(page.text, page.url)
            finally:
                client.close()
        return self._cached("levels", 600, load)

    def op_list_url(self, level: str, year: int) -> str:
        tpl = self.s.site.op_list_url_template
        if tpl:
            return urljoin(self.s.site.base_url, tpl.format(level=level, level_title=LEVELS.get(level, level).lower(), year=year))
        for lv in self.levels():
            if lv.level == level and year in lv.years:
                return lv.years[year]
        raise SiteError(f"На странице «Образование» нет ссылки для уровня «{LEVELS.get(level, level)}» и года {year}")

    def programs(self, level: str, year: int, client: SiteClient | None = None) -> list[ProgramBlock]:
        def load():
            c = client or self.site()
            try:
                page = c.get(self.op_list_url(level, year))
                return parse_op_list(page.text, page.url, LEVELS.get(level, ""), year)
            finally:
                if client is None:
                    c.close()
        if client is not None:
            return load()
        return self._cached(f"programs:{level}:{year}", 600, load)

    # ------------------------------------------------------------------ запуски

    def create_run(self, level: str, year: int, programs: list[str], force: bool = False) -> int:
        with self.db.Session() as ses:
            run = Run(status="queued", params={"level": level, "level_title": LEVELS.get(level, level), "year": year,
                                               "programs": programs, "force": force})
            ses.add(run)
            ses.commit()
            return run.id

    def stop(self) -> None:
        self._stop.set()

    def mark_interrupted(self) -> None:
        """При старте приложения: запуски, оборванные остановкой, помечаются как прерванные."""
        with self.db.Session() as ses:
            for run in ses.scalars(select(Run).where(Run.status.in_(["running"]))):
                run.status = "interrupted"
                run.stage = "Прерван: приложение было остановлено. Запуск можно продолжить."
            ses.commit()

    def execute(self, run_id: int) -> None:
        self._stop.clear()
        t0 = time.monotonic()
        with self.db.Session() as ses:
            run = ses.get(Run, run_id)
            resumed = bool(run.items)
            run.status, run.started_at, run.error = "running", run.started_at or now(), ""
            ses.commit()
            params = dict(run.params)
        self.journal(run_id, "INFO", ("Продолжение запуска" if resumed else "Запуск робота") +
                     f": {params.get('level_title')}, набор {params.get('year')}, ОП: "
                     f"{'все' if params.get('programs') == ['*'] else len(params.get('programs', []))}; "
                     f"LLM: {self.llm.describe()}")
        client = self.site(run_id)
        try:
            if not resumed:
                self._plan(run_id, params, client)
            self._process(run_id, params, client)
            self._finish(run_id, "finished", "", t0)
        except RunStopped:
            self._finish(run_id, "interrupted", "Запуск остановлен пользователем, его можно продолжить", t0)
        except SiteError as e:
            self.journal(run_id, "ERROR", f"Работа завершена: {e}")
            self._finish(run_id, "failed", str(e), t0)
        except Exception as e:   # noqa: BLE001 — сбой робота фиксируется в журнале и отчёте
            log.exception("run %s failed", run_id)
            self.journal(run_id, "ERROR", f"Сбой робота: {type(e).__name__}: {e}")
            self._finish(run_id, "failed", f"{type(e).__name__}: {e}", t0)
        finally:
            client.close()

    def _stage(self, run_id: int, text: str) -> None:
        with self.db.Session() as ses:
            ses.get(Run, run_id).stage = text
            ses.commit()

    def _finish(self, run_id: int, status: str, error: str, t0: float) -> None:
        with self.db.Session() as ses:
            run = ses.get(Run, run_id)
            run.status = status
            run.error = error
            run.finished_at = now()
            run.seconds = (run.seconds or 0) + (time.monotonic() - t0)
            counts = self.counts(ses, run_id)
            run.stage = "Завершён" if status == "finished" else run.stage
            ses.commit()
        self.journal(run_id, "INFO" if status == "finished" else "WARNING",
                     f"Запуск {'завершён' if status == 'finished' else 'остановлен'}: документов {counts['total']}, "
                     f"обработано {counts['processed']}, требуют проверки {counts['review']}, ошибок {counts['error']}")
        self._cleanup()

    @staticmethod
    def counts(ses, run_id: int) -> dict:
        rows = ses.execute(select(RunItem.status, func.count()).where(RunItem.run_id == run_id).group_by(RunItem.status)).all()
        by = {k: v for k, v in rows}
        done = ses.scalar(select(func.count()).select_from(RunItem).where(RunItem.run_id == run_id, RunItem.done.is_(True))) or 0
        return {"by_status": by, "total": sum(by.values()), "done": done,
                "processed": by.get("processed", 0), "review": sum(by.get(s, 0) for s in REVIEW_STATUSES),
                "error": by.get("error", 0), "skipped": by.get("skipped", 0)}

    def _cleanup(self) -> None:
        """Журнал — не менее чем за keep_log_runs последних запусков; версии JSON — keep_versions последних."""
        keep_runs, keep_versions = self.s.history.keep_log_runs, self.s.history.keep_versions
        with self.db.Session() as ses:
            ids = ses.scalars(select(Run.id).order_by(Run.id.desc()).offset(keep_runs)).all()
            if ids:
                ses.execute(delete(LogEntry).where(LogEntry.run_id.in_(ids)))
            for doc_id, kind, n in ses.execute(select(JsonVersion.document_id, JsonVersion.kind, func.count())
                                                  .group_by(JsonVersion.document_id, JsonVersion.kind)).all():
                if n > keep_versions:
                    old = ses.scalars(select(JsonVersion.id).where(JsonVersion.document_id == doc_id, JsonVersion.kind == kind)
                                      .order_by(JsonVersion.version.desc()).offset(keep_versions)).all()
                    ses.execute(delete(JsonVersion).where(JsonVersion.id.in_(old)))
            ses.commit()

    # ------------------------------------------------------------------ шаги 2–3: перечень ОП и документы

    def _plan(self, run_id: int, params: dict, client: SiteClient) -> None:
        level, year = params["level"], int(params["year"])
        self._stage(run_id, "Открытие страницы перечня ОП")
        url = self.op_list_url(level, year)
        self.journal(run_id, "INFO", f"Открытие страницы перечня ОП: {url}")
        blocks = self.programs(level, year, client=client)
        self.journal(run_id, "INFO", f"На странице найдено ОП: {len(blocks)}")
        wanted = params.get("programs") or ["*"]
        selected = blocks if wanted == ["*"] else [b for b in blocks if b.key in wanted]
        for key in set(wanted) - {b.key for b in blocks} - {"*"}:
            self.journal(run_id, "WARNING", f"ОП {key} не найдена на странице перечня")
        if not selected:
            raise SiteError("Выбранные ОП не найдены на странице перечня")
        self._stage(run_id, "Поиск документов ОП")
        with self.db.Session() as ses:
            for b in selected:
                prog = ses.get(Program, b.key) or Program(id=b.key)
                prog.level, prog.level_title, prog.year = level, LEVELS.get(level, b.level_title), b.year or year
                prog.code, prog.direction, prog.profile, prog.form = b.code, b.direction, b.profile, b.form
                prog.block_title, prog.source_url = b.title, url
                ses.merge(prog)
                ses.flush()
                present = {d.doc_type for d in b.docs}
                for t in REQUIRED_TYPES:
                    if t not in present:
                        self.journal(run_id, "WARNING", f"У ОП нет ссылки на документ типа «{DOC_TYPES[t]}» — тип пропущен", b.key, ses=ses)
                for link in b.docs:
                    doc = ses.scalar(select(Document).where(Document.program_id == b.key, Document.url == link.url))
                    if doc is None:
                        doc = Document(program_id=b.key, doc_type=link.doc_type, title=link.title, url=link.url, status="queued")
                        ses.add(doc)
                        ses.flush()
                    else:
                        doc.title, doc.doc_type = link.title, link.doc_type
                    ses.add(RunItem(run_id=run_id, program_id=b.key, document_id=doc.id, doc_type=link.doc_type,
                                    title=link.title, url=link.url, status="queued"))
                self.journal(run_id, "INFO", f"Найдено документов: {len(b.docs)}"
                             + (f"; не обрабатываются: {', '.join(sorted(set(b.excluded)))}" if b.excluded else ""), b.key, ses=ses)
            ses.commit()

    # ------------------------------------------------------------------ шаги 4–12: документы

    def _process(self, run_id: int, params: dict, client: SiteClient) -> None:
        force = bool(params.get("force"))
        with self.db.Session() as ses:
            items = ses.scalars(select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.id)).all()
            todo = [(i.id, i.program_id) for i in items if not i.done]
            programs = list(dict.fromkeys(i.program_id for i in items))
            total = len(items)
        done_before = total - len(todo)
        for n, (item_id, program_id) in enumerate(todo, 1):
            if self._stop.is_set():
                raise RunStopped()
            self._stage(run_id, f"Обработка документов: {done_before + n} из {total}")
            t = time.monotonic()
            try:
                status, note = self._document(run_id, item_id, client, force)
            except Exception as e:   # noqa: BLE001 — ошибка документа не прерывает запуск
                log.exception("document failed")
                status, note = "error", f"Сбой обработки: {type(e).__name__}: {e}"
                self._set_doc(item_id, status, note)
            with self.db.Session() as ses:
                item = ses.get(RunItem, item_id)
                item.status, item.note, item.done = status, note, True
                item.seconds = time.monotonic() - t
                ses.commit()
                last_of_program = not ses.scalar(select(func.count()).select_from(RunItem).where(
                    RunItem.run_id == run_id, RunItem.program_id == program_id, RunItem.done.is_(False)))
            if last_of_program:
                self._update_page(run_id, program_id)
        for program_id in programs:
            with self.db.Session() as ses:
                prog = ses.get(Program, program_id)
                if prog is not None and prog.page is None:
                    self._update_page(run_id, program_id)

    def _set_doc(self, item_id: int, status: str, note: str) -> None:
        with self.db.Session() as ses:
            item = ses.get(RunItem, item_id)
            if item.document_id:
                doc = ses.get(Document, item.document_id)
                doc.status, doc.note = status, note
            ses.commit()

    def _download(self, run_id: int, doc: Document, client: SiteClient) -> tuple[bytes, bool]:
        """Шаг 4. Возвращает (содержимое, изменился ли документ). Неизменившийся документ повторно не скачивается."""
        path = Path(doc.file_path) if doc.file_path else None
        headers = {}
        if path and path.exists():
            if doc.etag:
                headers["If-None-Match"] = doc.etag
            if doc.last_modified:
                headers["If-Modified-Since"] = doc.last_modified
        r = client.get(doc.url, headers=headers, max_bytes=self.s.site.max_document_mb * 2**20)
        if r.not_modified and path and path.exists():
            return path.read_bytes(), False
        pdfmod.check_pdf(r.content)
        sha = hashlib.sha256(r.content).hexdigest()
        target = self.docs_dir / f"{sha}.pdf"
        if not target.exists():
            target.write_bytes(r.content)
        changed = sha != doc.sha256
        doc.sha256, doc.file_path, doc.size = sha, str(target), len(r.content)
        doc.etag = r.headers.get("etag")
        doc.last_modified = r.headers.get("last-modified") or formatdate(usegmt=True)
        doc.downloaded_at = now()
        return r.content, changed

    def _document(self, run_id: int, item_id: int, client: SiteClient, force: bool) -> tuple[str, str]:
        with self.db.Session() as ses:
            item = ses.get(RunItem, item_id)
            doc = ses.get(Document, item.document_id)
            prog = ses.get(Program, item.program_id)
            label = f"{DOC_TYPES[doc.doc_type]} «{doc.title}»"
            doc.status = "in_progress"
            item.status = "in_progress"
            ses.commit()
            # шаг 4 — скачивание
            try:
                data, changed = self._download(run_id, doc, client)
            except SiteError as e:
                doc.status, doc.note = "error", f"Документ недоступен: {e}"
                self.journal(run_id, "ERROR", f"Документ не удалось скачать, помечен как недоступный: {e}", prog.id, label, ses=ses)
                ses.commit()
                return doc.status, doc.note
            except pdfmod.NotPdf as e:
                doc.status, doc.note = "error", str(e)
                self.journal(run_id, "ERROR", f"{e} — файл не обрабатывается", prog.id, label, ses=ses)
                ses.commit()
                return doc.status, doc.note
            has_final = ses.scalar(select(func.count()).select_from(JsonVersion).where(
                JsonVersion.document_id == doc.id, JsonVersion.kind == "final")) > 0
            if (not changed and not force and has_final and doc.processed_sha256 == doc.sha256
                    and doc.parser_version == PARSER_VERSION):
                last = ses.scalar(select(RunItem).where(RunItem.document_id == doc.id, RunItem.done.is_(True),
                                                        RunItem.status != "in_progress").order_by(RunItem.id.desc()))
                doc.status = last.status if last else "processed"
                doc.note = (last.note if last and last.note else "Не изменился с прошлого запуска")
                self.journal(run_id, "INFO", "Документ не изменился — повторная обработка не требуется", prog.id, label, ses=ses)
                ses.commit()
                return doc.status, doc.note
            ses.commit()
            program_info = {"id": prog.id, "level": prog.level_title, "code": prog.code, "direction": prog.direction,
                            "profile": prog.profile, "form": prog.form, "year": prog.year, "page_url": prog.source_url}

        # шаги 5–8 — способ извлечения, извлечение, проверка
        status, note, method, extracted, pages = self._extract(run_id, doc, data, program_info, label)
        # шаг 9 — первичный JSON-файл
        primary = {
            "schema": "robot-op/primary/1",
            "document": {"type": doc.doc_type, "type_title": DOC_TYPES[doc.doc_type], "title": doc.title, "url": doc.url,
                         "sha256": doc.sha256, "pages": pages, "method": method},
            "program": program_info,
            "status": status,
            "note": note,
            "data": extracted,
            "robot": {"parser_version": PARSER_VERSION, "extracted_at": datetime.now().isoformat(timespec="seconds"), "run": run_id},
        }
        with self.db.Session() as ses:
            self._save_version(ses, doc.id, "primary", primary, run_id)
            ses.commit()
        # шаг 10 — краткое описание
        summary, model = None, None
        if doc.doc_type in SUMMARY_TYPES and extracted and status not in ("unknown_template", "skipped", "error"):
            if self.llm.enabled:
                try:
                    summary = self.llm.summarize(doc.doc_type, {"program": program_info, "document": primary["document"], **extracted})
                    model = self.llm.describe()
                except LlmError as e:
                    self.journal(run_id, "ERROR", f"{e}. Итоговый JSON-файл сформирован без краткого описания", program_info["id"], label)
                    note = (note + "; " if note else "") + "краткое описание не сформировано"
            else:
                note = note or ""
        # шаг 11 — итоговый JSON-файл
        final = dict(primary, schema="robot-op/final/1", summary=summary, summary_model=model,
                     final_at=datetime.now().isoformat(timespec="seconds"))
        with self.db.Session() as ses:
            self._save_version(ses, doc.id, "final", final, run_id)
            d = ses.get(Document, doc.id)
            d.status, d.note, d.method = status, note, method
            if status != "error":
                d.processed_sha256, d.parser_version = d.sha256, PARSER_VERSION
            ses.commit()
        level = {"processed": "INFO", "error": "ERROR"}.get(status, "WARNING")
        self.journal(run_id, level, f"Статус: {status}" + (f" — {note}" if note else ""), program_info["id"], label)
        return status, note

    def _extract(self, run_id: int, doc: Document, data: bytes, program: dict, label: str):
        pages = pdfmod.page_count(data)
        texts = pdfmod.page_texts(data, max_pages=min(pages, 3))
        avg = sum(len(t.strip()) for t in texts) / max(1, len(texts))
        scanned = avg < self.s.ocr.min_text_chars_per_page
        method = "ocr" if scanned else "text"
        dpi = self.s.ocr.dpi
        try:
            if doc.doc_type in ("up", "kug"):
                if not scanned:
                    ok, why = sections.type_check(doc.doc_type, "\n".join(texts))
                    if not ok:
                        self.journal(run_id, "WARNING", f"{why} — документ не обрабатывается", program["id"], label)
                        return "skipped", why, method, {}, pages
                if doc.doc_type == "up":
                    extracted = curriculum.extract(data, scanned, self.ocr, dpi)
                    failed = extracted["checks"]["failed"]
                    if failed:
                        return ("needs_review", f"{failed} строк(и) не прошли проверку сумм часов", method, extracted, pages)
                    return "processed", f"{'Скан, OCR' if scanned else 'Текстовый слой'}; дисциплин: {len(extracted['rows'])}", method, extracted, pages
                extracted = schedule.extract(data, scanned, self.ocr, dpi)
                if extracted["messages"]:
                    return "needs_review", "; ".join(extracted["messages"])[:500], method, extracted, pages
                return "processed", f"{'Скан, OCR' if scanned else 'Текстовый слой'}; курсов: {len(extracted['courses'])}", method, extracted, pages
            # текстовые документы; отсканированные распознаются OCR постранично
            if scanned:
                full = "\n".join(ocr_page_text(pdfmod.render_page(data, i, dpi), self.ocr) for i in range(pages))
            else:
                full = "\n".join(pdfmod.page_texts(data))
            ok, why = sections.type_check(doc.doc_type, full[:4000])
            if not ok:
                self.journal(run_id, "WARNING", f"Содержимое не соответствует типу ({why}) — документ помечен и не обрабатывается",
                             program["id"], label)
                return "skipped", why[:1].upper() + why[1:], method, {}, pages
            extracted = sections.extract(doc.doc_type, full)
            tpl = extracted["template"]
            if tpl["score"] < 0.5:
                return "unknown_template", "Разделы документа не соответствуют известному шаблону", method, extracted, pages
            if tpl["missing_required"]:
                missing = ", ".join(f"«{m}»" for m in tpl["missing_required"])
                return "partial", f"Не найден раздел {missing}", method, extracted, pages
            return "processed", "Скан, OCR" if scanned else "Текстовый слой", method, extracted, pages
        except (curriculum.NoTable, schedule.NoSchedule) as e:
            return "unknown_template", f"{e} — структура таблицы не распознана", method, {}, pages
        except OcrUnavailable as e:
            self.journal(run_id, "ERROR", str(e), program["id"], label)
            return "error", str(e), method, {}, pages

    def _save_version(self, ses, doc_id: int, kind: str, data: dict, run_id: int) -> None:
        last = ses.scalar(select(JsonVersion).where(JsonVersion.document_id == doc_id, JsonVersion.kind == kind)
                          .order_by(JsonVersion.version.desc()))
        strip = lambda d: {k: v for k, v in d.items() if k not in ("robot", "final_at")}
        if last is not None and strip(last.data) == strip(data):
            return   # содержимое не изменилось — новая версия не нужна
        ses.add(JsonVersion(document_id=doc_id, kind=kind, version=(last.version + 1) if last else 1, run_id=run_id, data=data))

    # ------------------------------------------------------------------ шаг 12 — веб-страница ОП

    def _update_page(self, run_id: int, program_id: str) -> None:
        with self.db.Session() as ses:
            prog = ses.get(Program, program_id)
            if prog is None:
                return
            prog.page = pagebuilder.build(ses, prog)
            prog.page_updated_at = now()
            ses.commit()
        self.journal(run_id, "INFO", "Веб-страница ОП обновлена", program_id)
