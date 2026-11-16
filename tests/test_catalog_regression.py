from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from json import loads
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import demo_settings
from demo_site import render
from robot_op.models import Document, JsonVersion, Program, Run
from robot_op.robot.http import SiteError
from robot_op.web import app as web


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    app = web.create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    now = SimpleNamespace(value=1000.0)
    monkeypatch.setattr(web, "time", SimpleNamespace(monotonic=lambda: now.value))
    robot = app.state.robot
    calls = Mock(wraps=robot.catalog)
    monkeypatch.setattr(robot, "catalog", calls)
    monkeypatch.setattr(render, "PROGRAMS", [*render.PROGRAMS, replace(render.PROGRAMS[0], year=2024)])
    return app, calls, now


def catalog_html(response):
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def cache_hints(html):
    meta = html.select_one("[data-catalog-cache]")
    assert meta is not None, "The browser needs server cache state to avoid redundant fetches"
    return loads(meta.string)


def counts(app):
    with app.state.db.Session() as session:
        return {model.__name__: session.scalar(select(func.count()).select_from(model))
                for model in (Program, Document, JsonVersion, Run)}


def test_warm_reload_serves_snapshot_and_both_level_filters_locally(catalogue):
    app, calls, _ = catalogue
    with TestClient(app) as client:
        first = catalog_html(client.get("/programs"))
        assert not cache_hints(first)["ready"]
        calls.assert_not_called()
        assert client.get("/programs/catalog").status_code == 200
        initial = counts(app)
        for route in ("/", "/run", "/programs", "/programs?level=master", "/programs?year=2026"):
            page = catalog_html(client.get(route))
            hints = cache_hints(page)
            assert hints["ready"] and 2026 in hints["years"]
            assert page.select_one('[data-catalog-level="bachelor"]')
            assert page.select_one('[data-catalog-level="master"]')
        assert counts(app) == initial == {"Program": 0, "Document": 0, "JsonVersion": 0, "Run": 0}
        calls.assert_called_once_with(None)


def test_latest_cohort_shares_initial_catalogue_work(catalogue):
    app, calls, _ = catalogue
    with TestClient(app) as client:
        assert client.get("/programs/catalog").status_code == 200
        for _ in range(3):
            response = catalog_html(client.get("/programs/catalog?year=2026"))
            assert {g["data-catalog-year"] for g in response.select(".app-cards-group")} == {"2026"}
        calls.assert_called_once_with(None)


def test_historical_cohort_fetches_once_and_cumulative_view_keeps_loaded_years(catalogue):
    app, calls, _ = catalogue
    with TestClient(app) as client:
        assert client.get("/programs/catalog").status_code == 200
        assert 2024 not in cache_hints(catalog_html(client.get("/programs")))["years"]
        route = "/programs/catalog?year=2024&all_years=true"
        for _ in range(4):
            response = catalog_html(client.get(route))
            assert {"2024", "2026"} <= {g["data-catalog-year"] for g in response.select(".app-cards-group")}
            assert {2024, 2026} <= set(cache_hints(response)["years"])
        assert [c.args for c in calls.call_args_list] == [(None,), (2024,)]


def test_default_snapshot_only_covers_years_with_all_levels_loaded(catalogue):
    app, calls, _ = catalogue
    original = calls._mock_wraps

    def mixed_years(cohort):
        rows, years, incomplete = original(cohort)
        return rows, {**years, "master": [2024, 2025]}, incomplete

    calls.side_effect = mixed_years
    with TestClient(app) as client:
        assert client.get("/programs/catalog").status_code == 200
        hints = cache_hints(catalog_html(client.get("/programs")))
        assert 2025 not in hints["years"]
        assert client.get("/programs/catalog?year=2025").status_code == 200
        assert 2025 in cache_hints(catalog_html(client.get("/programs")))["years"]
        assert [c.args for c in calls.call_args_list] == [(None,), (2025,)]


def test_concurrent_cold_requests_serialize_source_fetch(catalogue):
    app, calls, _ = catalogue
    entered, release = Event(), Event()
    start = Barrier(4)
    original = calls._mock_wraps

    def slow(cohort):
        entered.set()
        assert release.wait(5)
        return original(cohort)

    calls.side_effect = slow
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=4) as pool:
        def request_catalogue(_):
            start.wait(timeout=5)
            return client.get("/programs/catalog").status_code

        results = [pool.submit(request_catalogue, n) for n in range(4)]
        try:
            assert entered.wait(5)
        finally:
            release.set()
        assert [r.result(timeout=5) for r in results] == [200] * 4
        calls.assert_called_once_with(None)
        assert counts(app)["Run"] == 0


def test_stale_catalogue_remains_readable_and_retry_is_throttled(catalogue):
    app, calls, clock = catalogue
    with TestClient(app) as client:
        assert client.get("/programs/catalog").status_code == 200
        clock.value += 601
        page = catalog_html(client.get("/programs"))
        assert page.select_one("article.ir-card")
        assert cache_hints(page)["ready"], "A saved snapshot must render without a browser refresh request"
        assert 2026 in cache_hints(page)["years"]
        assert calls.call_count == 1
        calls.side_effect = SiteError("Источник временно недоступен")
        for _ in range(3):
            received = catalog_html(client.get("/programs/catalog"))
            assert received.select_one("article.ir-card")
            hints = cache_hints(received)
            assert hints["incomplete"]
            assert hints["ready"], "Refresh is throttled after a failed attempt"
        assert calls.call_count == 2
        clock.value += 31
        assert client.get("/programs/catalog").status_code == 200
        assert calls.call_count == 3


@pytest.mark.parametrize("route", ["/programs?year=2024", "/run?year=2024", "/programs?level=bachelor&year=2024"])
def test_cohort_deep_link_is_rendered_for_browser(route, catalogue):
    app, _, _ = catalogue
    with TestClient(app) as client:
        page = catalog_html(client.get(route))
        assert page.select_one("[data-catalog-url]")["data-selected-year"] == "2024"


def test_parallel_opening_of_same_program_creates_one_job(catalogue):
    app, _, _ = catalogue
    key = "09.03.01-isoiu-2026-och"
    streams = Barrier(4)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=4) as pool:
        assert client.get("/programs/catalog").status_code == 200

        def open_program(_):
            streams.wait(timeout=5)
            result = client.post("/run", data={"level": "bachelor", "program_id": key}, follow_redirects=False)
            assert result.status_code == 303
            return result.headers["location"]

        locations = list(pool.map(open_program, range(4)))
        assert len(set(locations)) == 1
        assert locations[0].startswith("/run/")
        assert counts(app) == {"Program": 0, "Document": 0, "JsonVersion": 0, "Run": 1}
