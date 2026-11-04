"""Export saved public results to a standalone GitHub Pages snapshot.

python -m robot_op.export_pages --output /tmp/robo-study-pages --base-path /robo-study/
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

STATIC = Path(__file__).parent / "web" / "static"
THEME_SCRIPT = """(function(){
function apply(theme){document.documentElement.dataset.theme=theme;
document.querySelectorAll('[data-theme-toggle]').forEach(function(a){
a.textContent=theme==='dark'?'Светлая тема':'Тёмная тема';});}
var theme='light';try{theme=localStorage.getItem('robot-op-theme')||'light';}catch(e){}
apply(theme==='dark'?'dark':'light');
document.addEventListener('DOMContentLoaded',function(){apply(document.documentElement.dataset.theme);
document.querySelectorAll('[data-theme-toggle]').forEach(function(a){a.addEventListener('click',function(e){
e.preventDefault();var t=document.documentElement.dataset.theme==='dark'?'light':'dark';apply(t);
try{localStorage.setItem('robot-op-theme',t);}catch(e){} });});});})();"""


def export_site(client, output: Path, base_path: str, json_urls=()) -> dict:
    """Crawl only public read routes; require an empty destination and fail on broken links."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty")
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*", base_path):
        raise ValueError("Base path must be / or /repository/")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, output / "static")
    (output / ".nojekyll").touch()
    origin = str(client.base_url)
    host = urlsplit(origin).netloc
    pending, seen = deque(["/", *json_urls]), set()
    json_urls = set(json_urls)
    html_count = json_count = 0

    def canonical(href: str, current: str) -> tuple[str | None, str]:
        if not href or href.startswith("#"):
            return None, href
        u = urlsplit(urljoin(origin.rstrip("/") + current, href))
        if u.netloc != host or u.scheme not in ("http", "https"):
            return None, href
        path = unquote(u.path)
        if ".." in Path(path).parts or "\\" in path:
            raise ValueError("Unsafe path")
        query = urlencode(sorted(parse_qsl(u.query, keep_blank_values=True)))
        return path + ("?" + query if query else ""), u.fragment

    def destination(key: str) -> tuple[Path, str]:
        u = urlsplit(key)
        path = u.path.lstrip("/")
        if path.startswith("static/"):
            rel = Path(path)
            return rel, base_path + rel.as_posix()
        if re.fullmatch(r"/api/documents/\d+/json", u.path):
            doc_id = u.path.split("/")[3]
            digest = hashlib.sha256(u.query.encode()).hexdigest()[:12]
            rel = Path("downloads") / f"doc-{doc_id}-{digest}.json"
            return rel, base_path + rel.as_posix()
        if not (u.path in ("/", "/programs", "/run") or u.path.startswith("/op/")):
            raise ValueError(f"Unsupported internal route: {key}")
        rel = Path(path)
        if u.query:
            rel /= "query-" + hashlib.sha256(u.query.encode()).hexdigest()[:12]
        url = base_path + (rel.as_posix().strip(".") + "/" if path else "")
        return rel / "index.html", url

    def link(href: str, current: str) -> str:
        key, fragment = canonical(href, current)
        if key is None:
            return fragment
        if key.startswith("/theme"):
            return "#theme"
        rel, url = destination(key)
        if not key.startswith("/static/"):
            pending.append(key)
        elif not (output / rel).is_file():
            raise ValueError(f"Missing asset: {key}")
        return url + ("#" + fragment if fragment else "")

    while pending:
        raw = pending.popleft()
        key, _ = canonical(raw, "/")
        if key in seen:
            continue
        seen.add(key)
        rel, _ = destination(key)
        if key == "/run":
            response = client.get("/programs")
        else:
            response = client.get(key)
        if response.status_code != 200:
            raise ValueError(f"Export request failed: {key}: {response.status_code}")
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if "application/json" in response.headers.get("content-type", ""):
            target.write_text(json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8")
            json_count += 1
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        if not soup.body or not soup.head:
            raise ValueError(f"Expected HTML page: {key}")
        if key == "/run":
            soup.title.string = "Обновление данных — Робот ОП"
            if soup.h1:
                soup.h1.string = "Обновление данных"
            main = soup.find("main")
            main.clear()
            section = soup.new_tag("section", attrs={"class": "app-section app-content"})
            section.append(BeautifulSoup('<h2>Просмотр сохранённых результатов</h2><p>На GitHub Pages размещена копия '
                'результатов обработки. Здесь можно изучать программы, дисциплины и JSON-файлы.</p>'
                '<p>Запуск робота, распознавание PDF и обновление данных доступны в локальном приложении. '
                'Для обновления опубликованной версии нужно заново выполнить экспорт.</p>'
                '<p><a href="/programs">Открыть образовательные программы</a></p>', "html.parser"))
            main.append(section)
        match = re.fullmatch(r"/op/[^/]+/doc/(\d+)", urlsplit(key).path)
        if match:
            aside = soup.select_one(".app-aside")
            if aside:
                for kind, title in (("primary", "Первичный JSON"), ("final", "Итоговый JSON")):
                    url = f"/api/documents/{match[1]}/json?kind={kind}"
                    if url in json_urls:
                        a = soup.new_tag("a", href=url, attrs={"class": "ir-btn ir-btn--outline app-aside__btn", "download": ""})
                        a.string = title
                        aside.append(a)
        note = soup.new_tag("div", attrs={"data-static-snapshot": "", "class": "app-footer__note", "style": "padding:12px 24px;text-align:center"})
        note.string = "Демонстрационная версия · сохранённые результаты обработки · обновление данных выполняется в локальном приложении"
        soup.body.insert(0, note)
        script = soup.new_tag("script")
        script.string = THEME_SCRIPT
        soup.head.insert(0, script)
        for a in soup.select("a.app-theme"):
            a["href"] = "#theme"
            a["data-theme-toggle"] = ""
        if soup.select("form"):
            raise ValueError(f"Unexpected live form: {key}")
        for el in soup.select("[href], [src]"):
            for attr in ("href", "src"):
                if el.has_attr(attr):
                    el[attr] = link(el[attr], key)
        target.write_text(str(soup), encoding="utf-8")
        html_count += 1

    # Validate rewritten local destinations without relying on server routing.
    for file in output.rglob("*.html"):
        soup = BeautifulSoup(file.read_text(encoding="utf-8"), "html.parser")
        for el in soup.select("[href], [src]"):
            for attr in ("href", "src"):
                href = el.get(attr, "")
                if href.startswith(base_path):
                    path = urlsplit(href).path
                    local = output / unquote(path[len(base_path):])
                    if path.endswith("/"):
                        local /= "index.html"
                    if not local.is_file():
                        raise ValueError(f"Broken exported link: {href}")
    report = {"pages": html_count, "json_files": json_count, "base_path": base_path,
              "exported_at": datetime.now(timezone.utc).isoformat()}
    (output / "snapshot.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from .config import load_settings
    from .models import Document, JsonVersion, Program
    from .web.app import create_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-path", default="/robo-study/")
    args = parser.parse_args()
    settings = load_settings()
    db_url = make_url(settings.app.database_url)
    if db_url.get_backend_name() != "sqlite" or not db_url.database:
        parser.error("This snapshot exporter requires an existing SQLite database")
    source = Path(db_url.database).resolve()
    if not source.is_file():
        parser.error("Source SQLite database does not exist")
    with tempfile.TemporaryDirectory(prefix="robo-pages-") as tmp:
        backup = Path(tmp) / "robot.db"
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
        settings.app.database_url = f"sqlite:///{backup}"
        settings.app.data_dir = tmp
        settings.app.demo = False
        settings.llm.provider = "none"
        app = create_app(settings, start_worker=False)
        with app.state.db.Session() as ses:
            programs = set(ses.scalars(select(Program.id).where(Program.page.is_not(None))))
            if not programs:
                parser.error("No saved program pages to publish")
            docs = set(ses.scalars(select(Document.id).where(Document.program_id.in_(programs))))
            versions = set(ses.execute(select(JsonVersion.document_id, JsonVersion.kind).where(JsonVersion.document_id.in_(docs))))
        urls = [f"/api/documents/{doc_id}/json?kind={kind}" for doc_id, kind in sorted(versions)]
        # Do not enter lifespan: no worker or interrupted-run updates are needed.
        client = TestClient(app, follow_redirects=True)
        try:
            report = export_site(client, args.output, args.base_path, urls)
        finally:
            client.close()
            app.state.db.engine.dispose()
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
