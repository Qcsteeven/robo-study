"""Сопоставление дисциплин с официальным справочником преподавателей."""
import httpx

from robot_op.config import SiteSettings
from robot_op.robot.http import SiteClient
from robot_op.robot.teachers import (
    load_level_directory,
    match_program_teachers,
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
