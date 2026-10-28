import httpx
import pytest

from demo_site import render
from robot_op.config import SiteSettings
from robot_op.robot.http import ForbiddenUrl, SiteClient, SiteError
from robot_op.robot.site import parse_education, parse_op_list, program_key, row_doc_type

EDU = "https://www.istu.edu/sveden/education/"


def test_education_levels_and_years():
    levels = parse_education(render.education_page(), EDU)
    by = {lv.level: lv for lv in levels}
    assert list(by) == ["bachelor", "specialist", "master", "spo", "postgraduate"]
    assert sorted(by["bachelor"].years) == [2022, 2023, 2024, 2025, 2026]
    assert by["bachelor"].years[2023].startswith("https://www.istu.edu/sveden/education/eduop/")
    # таблица филиала не смешивается с основной
    assert sorted(by["spo"].years) == [2021, 2022, 2023, 2024, 2025, 2026]


def test_op_list_blocks_and_documents():
    url = "https://www.istu.edu/sveden/education/eduop/?level=бакалавриат&year=2023"
    blocks = parse_op_list(render.op_list_page("бакалавриат", 2023), url, "Бакалавриат", 2023)
    assert [b.key for b in blocks] == ["09.03.01-isoiu-2023-och", "09.03.03-pie-2023-och"]
    b = blocks[0]
    assert b.code == "09.03.01" and b.direction == "Информатика и вычислительная техника"
    assert b.profile == "Интеллектуальные системы обработки информации и управления" and b.form == "Очная"
    types = [d.doc_type for d in b.docs]
    assert types.count("up") == 1 and types.count("kug") == 1 and types.count("oop") == 1
    assert types.count("practice") == 4 and types.count("rpd") == 13     # РПД берутся из модального окна
    assert all(not d.url.endswith(".sig") for d in b.docs)
    assert {"адаптированный учебный план", "аоп", "аннотации"} <= set(b.excluded)
    assert "kug" not in [d.doc_type for d in blocks[1].docs]


@pytest.mark.parametrize("label,expected", [
    ("учебный план", "up"), ("адаптированный учебный план", None), ("календарный учебный график", "kug"),
    ("ООП", "oop"), ("АОП", None), ("ФОС ГИА", "fos_gia"), ("программа ГИА", "gia"), ("практика", "practice"),
    ("РПД", "rpd"), ("аннотации", None), ("образовательный стандарт", None), ("Уровень", False),
])
def test_row_types(label, expected):
    assert row_doc_type(label) == expected


def test_program_key_like_design():
    assert program_key("09.03.01", "Интеллектуальные системы обработки информации и управления", 2023, "Очная") == "09.03.01-isoiu-2023-och"


def _client(handler, **kw):
    s = SiteSettings(requests_per_second=0, retries=kw.pop("retries", 3), backoff_base=2.0)
    for k, v in kw.items():
        setattr(s, k, v)
    sleeps = []
    return SiteClient(s, transport=httpx.MockTransport(handler), sleep=sleeps.append), sleeps


def test_https_only_and_allowed_hosts():
    c, _ = _client(lambda r: httpx.Response(200, text="ok"))
    with pytest.raises(ForbiddenUrl):
        c.get("http://www.istu.edu/sveden/education/")
    with pytest.raises(ForbiddenUrl):
        c.get("https://evil.example.com/x.pdf")
    assert c.get("https://www.istu.edu/sveden/education/").text == "ok"


def test_retries_with_growing_pause_and_no_retry_on_404():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/missing.pdf":
            return httpx.Response(404)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")
    c, sleeps = _client(handler)
    assert c.get("https://www.istu.edu/page").text == "ok"
    assert sleeps == [2.0, 4.0]                       # возрастающая пауза между повторами
    calls.clear()
    with pytest.raises(SiteError):
        c.get("https://www.istu.edu/missing.pdf")
    assert calls == ["/missing.pdf"]                  # 404 не повторяется
    c2, sleeps2 = _client(lambda r: httpx.Response(500), retries=4)
    with pytest.raises(SiteError):
        c2.get("https://www.istu.edu/page")
    assert sleeps2 == [2.0, 4.0, 8.0, 16.0]


def test_rate_limit():
    s = SiteSettings(requests_per_second=2.0, retries=0)
    sleeps = []
    c = SiteClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200)), sleep=sleeps.append)
    for _ in range(3):
        c.get("https://www.istu.edu/")
    assert len(sleeps) >= 2 and all(0 < x <= 0.5 for x in sleeps)
