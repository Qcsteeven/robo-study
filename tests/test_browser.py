"""Browser regressions with an isolated app and a generated Pages snapshot.

    python -m pip install playwright==1.60.0
    python -m playwright install chromium
    python -m pytest tests/test_browser.py -q
"""

from html import escape
from pathlib import Path
import re
import socket
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
import uvicorn

from conftest import demo_settings
from robot_op.catalog import LEVELS
from robot_op.export_pages import export_site
from robot_op.models import Document, JsonVersion, Program
from robot_op.robot.page import build
from robot_op.robot.site import program_key
from robot_op.web.app import create_app
from test_plan_tracks import curriculum_rows, row

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect

TRACKED_PROFILE = "Ёмкое моделирование"
TRACKED_ID = program_key("09.04.02", TRACKED_PROFILE, 2026, "Очная")
BACHELOR_PROFILE = "Вычислительные системы"
HISTORICAL_PROFILE = "Робототехника прошлых лет"
SAVED_ARCHIVE_PROFILE = "Архивная автоматика"
SOURCE = "http://demo.local/demo-site"
REMOTE_PROGRAMS = {
    ("bachelor", 2026): (BACHELOR_PROFILE, "09.03.01", "Информатика и вычислительная техника"),
    ("bachelor", 2023): (HISTORICAL_PROFILE, "15.03.06", "Мехатроника и робототехника"),
    ("master", 2026): (TRACKED_PROFILE, "09.04.02", "Информационные системы и технологии"),
}


def university_response(request):
    if request.url.path == "/demo-site/sveden/education/":
        rows = []
        for level in ("bachelor", "master"):
            links = "".join(
                f'<td><a href="eduop/?level={level}&amp;year={year}">{year}</a></td>'
                for candidate, year in REMOTE_PROGRAMS if candidate == level
            )
            rows.append(f"<tr><td>{LEVELS[level]}</td>{links}</tr>")
        return httpx.Response(200, text=f"<table>{''.join(rows)}</table>")
    if request.url.path == "/demo-site/sveden/education/eduop/":
        level, year = request.url.params["level"], int(request.url.params["year"])
        profile, code, direction = REMOTE_PROGRAMS[level, year]
        fields = {
            "Уровень": LEVELS[level],
            "Год": str(year),
            "Форма обучения": "Очная",
            "Код": f"{code} {direction}",
            "Профиль": profile,
        }
        rows = "".join(f"<tr><td>{key}</td><td>{escape(value)}</td></tr>" for key, value in fields.items())
        return httpx.Response(200, text=f"<h2>{escape(profile)}</h2><table>{rows}</table>")
    return httpx.Response(404)


@pytest.fixture
def app(tmp_path):
    app = create_app(demo_settings(tmp_path), transport=httpx.MockTransport(university_response), start_worker=False)
    with app.state.db.Session() as session:
        tracked = Program(id=TRACKED_ID, profile=TRACKED_PROFILE, level="master",
                          level_title=LEVELS["master"], year=2026, code="09.04.02",
                          direction="Информационные системы и технологии", form="Очная",
                          source_url=f"{SOURCE}/sveden/education/eduop/?level=master&year=2026")
        archive = Program(id="archive", profile=SAVED_ARCHIVE_PROFILE, level="bachelor",
                          level_title=LEVELS["bachelor"], year=2022, code="15.03.04",
                          direction="Автоматизация", form="Очная", source_url=f"{SOURCE}/archive")
        session.add_all([tracked, archive])
        session.flush()
        session.add_all([
            Document(id=1, program_id=tracked.id, doc_type="up", title="Учебный план", url=f"{SOURCE}/up.pdf"),
            Document(id=2, program_id=tracked.id, doc_type="rpd", title="Методы моделирования",
                     url=f"{SOURCE}/model-ai.pdf"),
            Document(id=3, program_id=tracked.id, doc_type="rpd", title="Методы моделирования",
                     url=f"{SOURCE}/model-iot.pdf"),
        ])
        session.flush()
        rows = [row("Б1.Б.02", "Математика", 20, semester=1), *curriculum_rows()]
        session.add(JsonVersion(document_id=1, kind="final", version=1, data={
            "document": {"pages": 1},
            "data": {"rows": rows, "semesters": [{"number": n, "weeks": 16} for n in (1, 2, 3)]},
        }))
        for doc_id, hours in ((2, 14), (3, 18)):
            session.add(JsonVersion(document_id=doc_id, kind="final", version=1, data={
                "document": {"pages": 1},
                "data": {"discipline": "Методы моделирования", "semesters": [3], "ze": 3,
                         "hours": {"total": 108, "aud": hours}},
            }))
        session.flush()
        tracked.page = build(session, tracked)
        archive.page = build(session, archive)
        session.commit()
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def serve():
    servers = []

    def start(app):
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning", ws="none"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        servers.append((server, thread, listener))
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "The isolated browser test server failed to start"
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread, listener in servers:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "The isolated browser test server failed to stop"


@pytest.fixture(scope="session")
def browser():
    with playwright.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("Install Chromium with python -m playwright install chromium")
        instance = runtime.chromium.launch(channel="chromium")
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    browser_page = context.new_page()
    browser_page.set_default_timeout(5000)
    yield browser_page
    context.close()


def warm_latest_catalog(app):
    with TestClient(app) as client:
        response = client.get("/programs/catalog")
        assert response.is_success
        assert BACHELOR_PROFILE in response.text
        assert TRACKED_PROFILE in response.text


def observe_catalog_requests(page):
    years = []

    def record(request):
        url = urlsplit(request.url)
        if url.path == "/programs/catalog":
            years.append(parse_qs(url.query).get("year", [None])[0])

    page.on("request", record)
    return years


def catalog_search(page):
    return page.get_by_role("searchbox", name="Поиск по программам")


def catalog_cards(page):
    return page.locator("[data-program-search]:visible .ir-card__title")


def year_button(page, year):
    return page.get_by_role("group", name="Год набора").get_by_role("button", name=str(year), exact=True)


def level_button(page, name):
    return page.get_by_role("group", name="Уровень образования").get_by_role("button", name=name, exact=True)


@pytest.mark.parametrize("query", [
    pytest.param("ЁМКОЕ", id="uppercase"),
    pytest.param("емкое", id="yo-folding"),
    pytest.param("Е\u0308мкое", id="unicode-normalization"),
    pytest.param("  09.04.02     МОДЕЛИРОВАНИЕ  ", id="spaces-and-mixed-fields"),
    pytest.param("ИНФОРМАЦИОННЫЕ\u00a0технологии", id="direction-and-nonbreaking-space"),
    pytest.param("09.04.02", id="code"),
])
def test_search_matches_profile_code_and_direction_with_no_requests(page, app, serve, query):
    warm_latest_catalog(app)
    requests = observe_catalog_requests(page)
    assert page.goto(f"{serve(app)}/programs").ok
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    catalog_search(page).fill(query)
    expect(catalog_cards(page)).to_have_text([TRACKED_PROFILE])
    catalog_search(page).fill("Вычислительные Моделирование")
    expect(catalog_cards(page)).to_have_count(0)
    no_results = page.get_by_role("status").filter(has_text="Подходящие программы не найдены.")
    expect(no_results).to_be_visible()
    catalog_search(page).fill("")
    catalog_search(page).press_sequentially("80.00.44")
    expect(no_results).to_be_visible()
    catalog_search(page).fill("   ")
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    expect(no_results).to_be_hidden()
    assert requests == []


def test_cold_catalog_fetches_once_and_warm_reload_is_server_rendered(page, app, serve):
    requests = observe_catalog_requests(page)
    assert page.goto(f"{serve(app)}/programs").ok
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    assert len(requests) == 1
    assert page.reload().ok
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    assert len(requests) == 1


def test_cohort_and_level_buttons_update_together_and_reuse_visited_cohorts(page, app, serve):
    warm_latest_catalog(app)
    requests = observe_catalog_requests(page)
    assert page.goto(f"{serve(app)}/programs?level=master").ok
    expect(year_button(page, 2026)).to_have_attribute("aria-pressed", "true")
    expect(level_button(page, "Магистратура")).to_have_attribute("aria-pressed", "true")
    expect(catalog_cards(page)).to_have_text([TRACKED_PROFILE])

    level_button(page, "Бакалавриат").press("Space")
    expect(level_button(page, "Бакалавриат")).to_have_attribute("aria-pressed", "true")
    expect(level_button(page, "Магистратура")).to_have_attribute("aria-pressed", "false")
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE])
    assert requests == []

    level_button(page, "Магистратура").click()
    catalog_search(page).fill("робототехника")
    year_button(page, 2023).click()
    expect(year_button(page, 2023)).to_have_attribute("aria-pressed", "true")
    expect(year_button(page, 2026)).to_have_attribute("aria-pressed", "false")
    expect(level_button(page, "Магистратура")).to_have_count(0)
    expect(level_button(page, "Все уровни")).to_have_attribute("aria-pressed", "true")
    expect(catalog_search(page)).to_have_value("робототехника")
    expect(catalog_cards(page)).to_have_text([HISTORICAL_PROFILE])
    assert requests == ["2023"]

    catalog_search(page).fill("")
    year_button(page, 2026).click()
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    year_button(page, 2023).click()
    expect(catalog_cards(page)).to_have_text([HISTORICAL_PROFILE])
    assert requests == ["2023"]


def test_direct_catalog_link_selects_the_requested_cohort(page, app, serve):
    warm_latest_catalog(app)
    assert page.goto(f"{serve(app)}/programs?year=2023").ok
    expect(year_button(page, 2023)).to_have_attribute("aria-pressed", "true")
    expect(catalog_cards(page)).to_have_text([HISTORICAL_PROFILE])


def test_cold_deep_link_keeps_the_initial_level_until_cohort_loads(page, app, serve):
    assert page.goto(f"{serve(app)}/programs?year=2026&level=bachelor").ok
    expect(page.locator('[data-level-choices] button[data-value="bachelor"]')).to_have_attribute("aria-pressed", "true")
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE])


def test_pending_cohort_is_not_duplicated_and_does_not_block_cached_results(page, app, serve):
    warm_latest_catalog(app)
    requests = observe_catalog_requests(page)
    assert page.goto(f"{serve(app)}/programs").ok
    pending_routes = []
    page.route(re.compile(r"/programs/catalog\?.*\byear=2023(?:&|$)"), lambda route: pending_routes.append(route))

    old_year = year_button(page, 2023)
    old_year.click()
    status = page.get_by_role("status").filter(has_text="Загружаем программы набора 2023")
    expect(status).to_be_visible()
    expect(catalog_cards(page)).to_have_count(0)
    old_year.click()
    assert len(pending_routes) == 1
    assert requests == ["2023"]

    with page.expect_event("requestfailed") as aborted:
        year_button(page, 2026).click()
    assert parse_qs(urlsplit(aborted.value.url).query).get("year") == ["2023"]
    expect(status).to_be_hidden()
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    assert requests == ["2023"]

def test_catalog_can_retry_a_failed_cohort_fetch(page, app, serve):
    warm_latest_catalog(app)
    base_url = serve(app)
    requests = observe_catalog_requests(page)
    assert page.goto(f"{base_url}/programs").ok

    def is_archive_request(url):
        return url.path == "/programs/catalog" and parse_qs(url.query).get("year") == ["2023"]

    archive_request = re.compile(r"/programs/catalog\?.*\byear=2023(?:&|$)")
    page.route(archive_request, lambda route: route.fulfill(status=503), times=1)
    with page.expect_response(lambda response: is_archive_request(urlsplit(response.url))) as failed:
        year_button(page, 2023).click()
    assert failed.value.status == 503
    expect(page.get_by_role("status").filter(has_text="Не удалось")).to_be_visible()
    year_button(page, 2026).click()
    expect(catalog_cards(page)).to_have_text([BACHELOR_PROFILE, TRACKED_PROFILE])
    with page.expect_response(lambda response: is_archive_request(urlsplit(response.url))) as retried:
        year_button(page, 2023).click()
    assert retried.value.ok
    expect(catalog_cards(page)).to_have_text([HISTORICAL_PROFILE])
    assert requests == ["2023", "2023"]


def test_live_catalog_button_submits_the_selected_historical_program(page, app, serve):
    warm_latest_catalog(app)
    origin = serve(app)
    assert page.goto(f"{origin}/programs").ok
    year_button(page, 2023).click()
    button = page.get_by_role("button", name=HISTORICAL_PROFILE, exact=True)
    expect(button).to_be_visible()
    button.press("Enter")
    expect(page).to_have_url(re.compile(r"/run/\d+$"))
    expect(page.get_by_role("status").filter(has_text="Программа в очереди на загрузку.")).to_be_visible()
    load_url = page.url
    run_id = urlsplit(load_url).path.rsplit("/", 1)[-1]
    run = page.request.get(f"{origin}/api/runs/{run_id}")
    assert run.ok
    assert run.json()["params"]["year"] == 2023

    page.get_by_role("link", name="К списку программ").click()
    year_button(page, 2023).click()
    button.click()
    expect(page).to_have_url(load_url)

def test_plan_semester_track_filter_and_document_navigation(page, app, serve):
    assert page.goto(f"{serve(app)}/programs").ok
    page.get_by_role("link", name=TRACKED_PROFILE, exact=True).click()
    expect(page.get_by_role("heading", level=1, name=TRACKED_PROFILE)).to_be_visible()
    plan = page.get_by_role("region", name="Учебный план по семестрам")
    previous = plan.get_by_role("button", name="Предыдущий семестр")
    next_button = plan.get_by_role("button", name="Следующий семестр")
    first = plan.get_by_role("button", name="1 семестр", exact=True)
    second = plan.get_by_role("button", name="2 семестр", exact=True)
    third = plan.get_by_role("button", name="3 семестр", exact=True)
    math = plan.get_by_role("link", name="Математика", exact=True)
    expect(first).to_have_attribute("aria-current", "true")
    expect(previous).to_be_disabled()
    expect(math).to_be_visible()

    next_button.click()
    expect(second).to_have_attribute("aria-current", "true")
    expect(math).to_be_hidden()
    previous.click()
    expect(first).to_have_attribute("aria-current", "true")
    plan.locator("[data-track]").press("ArrowRight")
    expect(second).to_have_attribute("aria-current", "true")
    third.click()
    expect(third).to_have_attribute("aria-current", "true")
    expect(next_button).to_be_disabled()
    expect(first).not_to_have_attribute("aria-current", "true")

    ai = plan.get_by_role("button", name="Прикладной ИИ", exact=True)
    iot = plan.get_by_role("button", name="Промышленный интернет вещей", exact=True)
    ai_row = plan.get_by_role("link", name="Анализ данных", exact=True)
    networks = plan.get_by_role("link", name="Промышленные сети", exact=True)
    expect(ai).to_have_attribute("aria-pressed", "true")
    expect(ai_row).to_be_visible()
    expect(networks).to_be_hidden()
    ai_filter = page.get_by_role("button", name="ИИ и анализ данных", exact=True)
    ai_filter.click()
    expect(ai_filter).to_have_attribute("aria-pressed", "false")
    expect(ai_row).to_be_hidden()
    iot.click()
    expect(iot).to_have_attribute("aria-pressed", "true")
    expect(ai).to_have_attribute("aria-pressed", "false")
    expect(third).to_have_attribute("aria-current", "true")
    expect(networks).to_be_visible()
    expect(page.locator("[data-track-shelves]:visible")).to_have_count(1)
    expect(page.locator("[data-track-shelves]:visible")).to_have_attribute("data-track-shelves", "1")
    modeling = plan.get_by_role("link", name="Методы моделирования", exact=True)
    expect(modeling).to_be_hidden()
    ai_filter.click()
    expect(ai_filter).to_have_attribute("aria-pressed", "true")
    expect(modeling).to_be_visible()
    expect(modeling).to_have_attribute("href", re.compile(r"/doc/3$"))

    modeling.click()
    expect(page).to_have_url(re.compile(r"/doc/3$"))
    expect(page.get_by_role("heading", level=1, name="Методы моделирования")).to_be_visible()
    expect(page.get_by_role("link", name="PDF на сайте ИРНИТУ", exact=True)).to_have_attribute("href", f"{SOURCE}/model-iot.pdf")


def test_mobile_snapshot_filters_cohorts_and_opens_documents_without_live_actions(browser, app, serve, tmp_path):
    warm_latest_catalog(app)
    output = tmp_path / "pages"
    with TestClient(app, follow_redirects=True) as client:
        export_site(client, output, "/robo-study/", program_ids=(TRACKED_ID, "archive"))
    snapshot = FastAPI()
    snapshot.mount("/robo-study", StaticFiles(directory=output, html=True))
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = context.new_page()
        page.set_default_timeout(5000)
        requests = []
        page.on("request", lambda request: requests.append(urlsplit(request.url).path))
        assert page.goto(f"{serve(snapshot)}/robo-study/").ok
        expect(page.locator("[data-static-snapshot]")).to_be_visible()
        expect(catalog_cards(page)).to_have_text([TRACKED_PROFILE])
        expect(page.get_by_text(BACHELOR_PROFILE, exact=True)).to_have_count(0)
        expect(page.locator("form, a[href*=\"/run\"], a[href*=\"/api/\"], a[download]")).to_have_count(0)
        year_button(page, 2022).tap()
        expect(year_button(page, 2022)).to_have_attribute("aria-pressed", "true")
        expect(catalog_cards(page)).to_have_text([SAVED_ARCHIVE_PROFILE])
        year_button(page, 2026).tap()
        catalog_search(page).fill("емкое")
        expect(catalog_cards(page)).to_have_text([TRACKED_PROFILE])
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("link", name="Тёмная тема", exact=True).tap()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")

        page.get_by_role("link", name=TRACKED_PROFILE, exact=True).tap()
        plan = page.get_by_role("region", name="Учебный план по семестрам")
        plan.get_by_role("button", name="3 семестр", exact=True).tap()
        plan.get_by_role("button", name="Промышленный интернет вещей", exact=True).tap()
        plan.get_by_role("link", name="Методы моделирования", exact=True).tap()
        expect(page).to_have_url(re.compile(r"/robo-study/op/[^/]+/doc/3/$"))
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        expect(page.get_by_role("heading", level=1, name="Методы моделирования")).to_be_visible()
        expect(page.get_by_role("link", name="PDF на сайте ИРНИТУ", exact=True)).to_have_attribute(
            "href", f"{SOURCE}/model-iot.pdf",
        )
        expect(page.locator("form, a[href*=\"/run\"], a[href*=\"/api/\"], a[download]")).to_have_count(0)
        assert not any(re.search(r"/(?:programs/catalog|api|run|theme)(?:/|$)", path) for path in requests)
    finally:
        context.close()
