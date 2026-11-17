"""Сопоставление дисциплин с официальным справочником преподавателей."""
import httpx

from robot_op.config import Settings, SiteSettings
from robot_op.models import Database, Program
from robot_op.robot.http import SiteClient
from robot_op.robot.pipeline import Robot
from robot_op.robot.teachers import (
    load_level_directory,
    load_person_profiles,
    match_program_teachers,
    resolve_teacher_profiles,
    teacher_discipline_key,
)


BASE = "https://www.istu.edu"
BACHELOR = BASE + "/sveden/education/direction_prep?level=2"
PROFILE = "Интеллектуальные системы обработки информации и управления"


def test_matches_only_the_selected_direction_and_profile():
    html = f"""
        <h3>09.03.01 Информатика и вычислительная техника</h3>
        <h4>Автоматизированные системы обработки информации и управления</h4>
        <ul><li>Вычислительная математика (Иванов И.И.)</li></ul>
        <h4>{PROFILE}</h4>
        <div>
            <ul>
                <li>Базы данных (<a href="/sveden/employees/sostav_ped_rabotnikov?letter=Д#d">
                    Дорофеев А.С.</a>, Дорофеев А.С.)</li>
                <li>Технологии разработки программ-роботов (Кононенко Р.В., Смирнягин Р.В.)</li>
                <li>Теория вероятностей, математическая статистика (Петров А.В.)</li>
                <li>Теория вероятностей, математическая статистика (установочная) (Сидоров Д.Н.)</li>
                <li>Физика (Павлова Т.О.)</li>
                <li>Дополнительная дисциплина (установочная)</li>
            </ul>
        </div>
        <h3>09.03.02 Информационные системы и технологии</h3>
        <h4>{PROFILE}</h4>
        <ul><li>Физика (Кузнецов З.З.)</li></ul>
    """

    matches = match_program_teachers(html, BACHELOR, code="09.03.01", profile=PROFILE)

    assert matches[teacher_discipline_key("БАЗЫ\u00a0ДАННЫХ")] == [
        {"name": "Дорофеев А.С.", "url": BASE + "/sveden/employees/sostav_ped_rabotnikov?letter=Д#d"},
    ]
    assert matches[teacher_discipline_key("Технологии разработки программ‑роботов.")] == [
        {"name": "Кононенко Р.В.", "url": BACHELOR},
        {"name": "Смирнягин Р.В.", "url": BACHELOR},
    ]
    assert matches[teacher_discipline_key("Теория вероятностей, математическая статистика")] == [
        {"name": "Петров А.В.", "url": BACHELOR},
    ]
    assert matches[teacher_discipline_key("Физика")] == [{"name": "Павлова Т.О.", "url": BACHELOR}]
    assert teacher_discipline_key("Вычислительная математика") not in matches
    assert teacher_discipline_key("Дополнительная дисциплина") not in matches
    assert not match_program_teachers(html, BACHELOR, code="09.03.01", profile="Несуществующий профиль")


def test_matches_postgraduate_profile_with_code_in_title():
    html = """
        <h3>1.2. Компьютерные науки и информатика</h3>
        <h4>Математическое моделирование, численные методы и комплексы программ</h4>
        <ul><li>Кандидатский экзамен по специальности (Бучнев О.С.)</li></ul>
    """
    url = BASE + "/sveden/education/direction_prep?level=5"

    matches = match_program_teachers(
        html, url, code="", profile="1.2.2 Математическое моделирование, численные методы и комплексы программ",
    )

    assert matches[teacher_discipline_key("Кандидатский экзамен по специальности")] == [
        {"name": "Бучнев О.С.", "url": url},
    ]
    assert not match_program_teachers(
        html, url, code="", profile="1.1.2 Математическое моделирование, численные методы и комплексы программ",
    )


def test_loads_level_page_through_configured_site_client():
    requested = []

    def response(request):
        requested.append(str(request.url))
        return httpx.Response(200, text="<h2>бакалавриат</h2>")

    site = SiteClient(
        SiteSettings(base_url="https://directory.example", requests_per_second=0),
        transport=httpx.MockTransport(response),
    )
    try:
        page = load_level_directory(site, "bachelor")
        assert page is not None
        assert page.text == "<h2>бакалавриат</h2>"
        assert load_level_directory(site, "spo") is None
    finally:
        site.close()

    assert requested == ["https://directory.example/sveden/education/direction_prep?level=2"]


def test_uses_person_profile_link_supplied_by_istu():
    html = f'''
        <h3>09.03.01 Информатика и вычислительная техника</h3>
        <h4>{PROFILE}</h4>
        <ul><li>Математика (<a href="/person/19281">Власов В.Г.</a>)</li></ul>
    '''

    matches = match_program_teachers(html, BACHELOR, code="09.03.01", profile=PROFILE)

    assert matches["математика"] == [{"name": "Власов В.Г.", "url": BASE + "/person/19281"}]


def test_refreshes_cached_teacher_links_and_uses_first_profile_without_course_evidence():
    legacy = BASE + "/sveden/employees/sostav_ped_rabotnikov?letter=В#svedprep-item-217"
    html = '''
        <a href="/person/19281">Власов Валерий Георгиевич</a>
        <a href="/person/19281">Власов Валерий Георгиевич</a>
        <a href="/person/other-vlasov">Власов Антон Андреевич</a>
        <a href="https://elsewhere.example/person/13">Власов Валерий Георгиевич</a>
        <a href="/person/1">Воронов Владимир Кириллович</a>
        <a href="/person/2">Воронов Виктор Константинович</a>
        <a href="/person/govorkov_aleksey_sergeevich">Говорков Алексей Сергеевич</a>
    '''
    with httpx.MockTransport(lambda request: httpx.Response(200, text=html)) as transport:
        site = SiteClient(SiteSettings(base_url=BASE, requests_per_second=0), transport=transport)
        try:
            profiles = load_person_profiles(site)
        finally:
            site.close()
    stored = {"математика": [
        {"name": "Власов В.Г.", "url": legacy},
        {"name": "Воронов В.К.", "url": BACHELOR},
        {"name": "Говорков А.С.", "url": BACHELOR},
    ]}

    resolved = resolve_teacher_profiles(stored, profiles)
    assert resolved["математика"] == [
        {"name": "Власов В.Г.", "url": BASE + "/person/19281"},
        {"name": "Воронов В.К.", "url": BASE + "/person/1"},
        {"name": "Говорков А.С.", "url": BASE + "/person/govorkov_aleksey_sergeevich"},
    ]
    assert resolve_teacher_profiles(stored, {}, previous=resolved) == resolved
    assert stored["математика"][0]["url"] == legacy


def test_preserves_verified_personal_links_if_university_lists_are_unavailable():
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    site = SiteClient(SiteSettings(base_url=BASE, requests_per_second=0, retries=0), transport=transport)
    try:
        profiles = load_person_profiles(site)
    finally:
        site.close()

    assert [item.url for item in profiles["власоввг"]] == [BASE + "/person/19281"]
    assert [item.url for item in profiles["наумовив"]] == [BASE + "/person/19306", BASE + "/person/52650"]


def test_program_build_discovers_teachers_and_disambiguates_by_discipline(tmp_path):
    settings = Settings()
    settings.app.data_dir = str(tmp_path)
    settings.site.requests_per_second = 0
    db = Database(f"sqlite:///{tmp_path / 'robot.db'}")
    db.create_all()
    profile = "Тестовая программа энергетического института"
    with db.Session() as session:
        session.add(Program(id="energy", code="13.03.01", level="bachelor",
                            level_title="Бакалавриат", year=2026, profile=profile))
        session.commit()
    pages = {
        "/sveden/education/direction_prep": f"""
            <h2>Бакалавриат</h2><h3>13.03.01 Теплоэнергетика и теплотехника</h3>
            <h4>{profile}</h4><ul>
                <li>Энергетика (Примерова Э.Д.)</li>
                <li>Автоматика (Примерова Э.Д.)</li>
                <li>Электроснабжение (Примерова Э.Д.)</li>
                <li>Электротехника (Тестов Т.Т.)</li>
            </ul>
        """,
        "/struktura/": """
            <main class="page-content">
                <ul><li class="multilist-item">
                    <a href="/struktura/example-energy/default">Институт энергетики</a>
                </li></ul>
            </main>
        """,
        "/struktura/example-energy/default": """
            <main class="page-content">
                <a href="/struktura/example-energy/team">Преподаватели и сотрудники</a>
            </main>
        """,
        "/struktura/example-energy/team": """
            <a href="/struktura/example-energy/team/department">По подразделениям</a>
        """,
        "/struktura/example-energy/team/department": """
            <a href="/person/example-other-lecturer">Примерова Элла Денисовна</a>
            <a href="/person/example-energy-lecturer">Примерова Энергия Дмитриевна</a>
        """,
        "/person/example-other-lecturer": """
            <h2>Дисциплины</h2><ul><li>Автоматика</li></ul>
            <h2>Научная работа</h2><ul><li>Энергетика</li></ul>
        """,
        "/person/example-energy-lecturer": """
            <p><strong>Читаемые дисциплины:</strong> «Энергетика»; «Автоматика».</p>
        """,
        "/raspisanie/poisk": """
            <main><form method="get" action="/raspisanie/poisk">
                <input type="text" name="search">
                <button>Искать</button>
            </form></main>
        """,
    }

    def respond(request):
        if request.url.path == "/sveden/education/direction_prep":
            assert request.url.params["level"] == "2"
        if request.url.path == "/raspisanie/poisk" and request.url.params.get("search") == "Тестов":
            return httpx.Response(200, text='<a href="/raspisanie/prepodavatel/812/">Тестов Т.Т.</a>')
        if html := pages.get(request.url.path):
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    robot = Robot(settings, db, transport=httpx.MockTransport(respond))
    robot._update_page(None, "energy")

    with db.Session() as session:
        teachers = session.get(Program, "energy").page["teacher_directory"]
        assert [x["url"] for x in teachers["энергетика"]] == [BASE + "/person/example-energy-lecturer"]
        assert [x["url"] for x in teachers["автоматика"]] == [
            BASE + "/person/example-other-lecturer", BASE + "/person/example-energy-lecturer",
        ]
        assert [x["url"] for x in teachers["электроснабжение"]] == [BASE + "/person/example-other-lecturer"]
        assert teachers["электротехника"] == [
            {"name": "Тестов Т.Т.", "url": BASE + "/raspisanie/prepodavatel/812/"},
        ]
