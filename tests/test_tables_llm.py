import json

import httpx

from robot_op.config import LlmSettings, dotenv
from robot_op.extract import sections, tables
from robot_op.robot import doc_view
from robot_op.robot.llm import LlmClient

# таблицы РПД ИРНИТУ в том виде, в каком их отдаёт pdf.page_tables: (страница, строки × ячейки)
RPD_TABLES = [
    (2, [["Код, наименование компетенции", "Код индикатора компетенции"],
         ["ОПК ОС-6 Способность проводить измерения и научные эксперименты", "ОПК ОС-6.1"]]),
    (2, [["Код индикатора", "Содержание индикатора", "Результат обучения"],
         ["ОПК ОС-6.1", "Выбирает приборы", "Знать выходные параметры Уметь выбирать приборы Владеть навыками измерения"]]),
    (2, [["Вид учебной работы", "Трудоемкость в академических часах"]]),
    # продолжение на следующей странице — другое число колонок из-за объединённой ячейки шапки
    (3, [["", "Всего", "Семестр № 2"], ["Общая трудоемкость дисциплины", "144", "144"],
         ["Аудиторные занятия, в том числе:", "48", "48"], ["лекции", "16", "16"], ["лабораторные работы", "16", "16"],
         ["практические/семинарские занятия", "16", "16"], ["Самостоятельная работа (в т.ч. курсовое проектирование)", "96", "96"],
         ["Трудоемкость промежуточной аттестации", "0", "0"],
         ["Вид промежуточной аттестации (итогового контроля по дисциплине)", "Зачет с оценкой", "Зачет с оценкой"]]),
    (3, [["№", "Тема", "Краткое содержание"],
         ["1", "Роль научных исследований в современном производстве", "Особенности цифрового производства."],
         ["2", "Параметры качества поверхности", "Методы измерения качества поверхности"]]),
    (4, [["№", "Наименование лабораторной работы", "Кол-во академических часов"], ["1", "Измерение микрогеометрии", "6"]]),
    (4, [["№", "Вид СРС", "Кол-во академических часов"], ["1", "Подготовка к практическим занятиям", "32"]]),
    (4, [["№", "Темы практических (семинарских) занятий", "Кол-во академических часов"], ["1", "Обработка результатов", "4"]]),
    (5, [["Индикатор достижения компетенции", "Критерии оценивания", "Средства"], ["ОПК ОС-6.1", "…", "Собеседование"]]),
]


def test_rpd_from_tables():
    text = "Рабочая программа дисциплины\n«ОСНОВЫ НАУЧНЫХ ИССЛЕДОВАНИЙ»\n1.1 Дисциплина «Основы научных исследований» обеспечивает…"
    d = sections.extract("rpd", text, RPD_TABLES)
    assert d["discipline"] == "Основы научных исследований"            # не прописными, как на титуле
    assert d["hours"] == {"total": 144, "aud": 48, "lec": 16, "lab": 16, "pr": 16, "sr": 96, "control": 0}
    assert d["semesters"] == [2] and d["control"] == ["Зачет с оценкой"]
    assert d["topics"][0] == {"title": "Роль научных исследований в современном производстве",
                              "summary": "Особенности цифрового производства"}
    assert d["labs"] == [{"title": "Измерение микрогеометрии", "hours": 6}]
    assert d["self_study"][0]["hours"] == 32 and d["practicals"][0]["title"] == "Обработка результатов"
    assert d["competencies"] == [{"code": "ОПК ОС-6", "description": "Способность проводить измерения и научные эксперименты"}]
    assert d["results"] == ["Знать выходные параметры", "Уметь выбирать приборы", "Владеть навыками измерения"]


def test_table_of_contents_is_not_a_section():
    text = ("Содержание\n2.2. Требования к выпускной квалификационной работе ........................ 4\n"
            "1 Общие положения\nТекст.\n2 Требования к выпускной квалификационной работе\nВКР выполняется…")
    sec = sections.split_sections(text, sections.TEMPLATES["gia"])
    assert sec["vkr"]["text"] == "ВКР выполняется…"


def test_practice_period_and_continuation_guard():
    rows = [(4, [["Форма обучения", "Период проведения (курс/семестр)", "Объём практики (ЗЕТ)",
                  "Продолжительность практики (количество недель)", "Форма промежуточной аттестации"],
                 ["очная", "4 курс / 8 семестр", "6", "4 недели / 216 часов", "Зачет с оценкой"]]),
            (4, [["№ п/п", "Этап", "Содержание работ"]]),
            # таблица оценивания с тем же числом колонок — не продолжение этапов
            (5, [["Индикатор достижения компетенции", "Критерии оценивания", "Средства"], ["ПКС-2.6", "…", "Защита отчета"]])]
    g = tables.group(rows)
    assert tables.practice_period(g["practice_period"]) == {"semesters": [8], "ze": 6, "weeks": 4, "control": ["Зачет с оценкой"]}
    assert tables.titled(g["stages"]) == []


def test_doc_view_schema_drops_glued_tables():
    old = {"discipline": "ОСНОВЫ", "semesters": [2], "control": ["курсовой проект"], "hours": None,
           "topics": ["1 Сводные данные по содержанию дисциплины " + "Семестр № 2 Лекции ЛР ПЗ " * 30, "Измерения"]}
    v = doc_view.build("rpd", old)
    assert ("Семестр", "2") in v["facts"] and ("Промежуточная аттестация", "Курсовой проект") in v["facts"]
    assert v["blocks"][0]["items"] == [{"title": "Измерения", "summary": ""}]       # склеенная таблица отброшена
    new = doc_view.build("rpd", {"semesters": [2], "ze": 4, "hours": {"total": 144, "lec": 16, "lab": 16, "pr": 16, "sr": 96},
                                 "labs": [{"title": "Измерение", "hours": 6}]})
    assert ("Трудоёмкость", "4 з. е. · 144 ч") in new["facts"]
    assert ("Аудиторные занятия", "лекции 16 ч, практические 16 ч, лабораторные 16 ч") in new["facts"]
    assert new["blocks"] == [{"title": "Лабораторные работы", "kind": "hours", "items": [{"title": "Измерение", "hours": "6 ч"}]}]


def test_gigachat_token_and_chat():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "ngw.devices.sberbank.ru":
            assert request.headers["Authorization"] == "Basic a2V5"
            assert request.headers["RqUID"] and b"scope=GIGACHAT_API_PERS" in request.content
            return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_at": 4102444800000})
        if request.headers["Authorization"] == "Bearer tok1" and len(calls) == 2:
            return httpx.Response(401)                      # токен отозван — берётся новый
        body = json.loads(request.content)
        assert body["model"] == "GigaChat" and body["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"choices": [{"message": {"content": "Первый абзац.\n\nВторой.\n\nТретий."}}]})
    s = LlmSettings(provider="gigachat", model="GigaChat", api_key="a2V5", ca_bundle="")
    llm = LlmClient(s, transport=httpx.MockTransport(handler))
    assert llm.enabled and llm.summarize("rpd", {"discipline": "Физика"}) == "Первый абзац. Второй. Третий."
    assert llm.summarize("gia", {"forms": ["ВКР"]}) == "Первый абзац.\n\nВторой."   # у ГИА — не больше двух абзацев
    assert [c.url.host for c in calls].count("ngw.devices.sberbank.ru") == 2         # токен кэшируется
    assert not LlmClient(LlmSettings(provider="gigachat", api_key="")).enabled       # без ключа LLM не подключён


def test_dotenv(tmp_path):
    (tmp_path / ".env").write_text("# комментарий\nROBOT_LLM_PROVIDER=gigachat\nROBOT_LLM_API_KEY=\"abc=\"\n\n", encoding="utf-8")
    assert dotenv(tmp_path / ".env") == {"ROBOT_LLM_PROVIDER": "gigachat", "ROBOT_LLM_API_KEY": "abc="}
    assert dotenv(tmp_path / "missing") == {}


def test_summary_length_limit():
    from robot_op.robot.llm import limit_text, too_long
    long = ("Курс обучает основам защиты. Он охватывает угрозы. Ключевые темы: — Основы безопасности; — Угрозы. "
            "Практика посвящена методикам. Итог — зачёт.")
    short = limit_text("rpd", long)
    assert short == "Курс обучает основам защиты. Он охватывает угрозы. Ключевые темы: Основы безопасности; Угрозы."
    assert too_long("rpd", long) and not too_long("rpd", short)
    assert limit_text("gia", "А. Б.\n\nВ.\n\nГ.") == "А. Б.\n\nВ."


def test_vkr_topics_paragraph_is_not_a_list():
    para = [["Темы выпускных квалификационных работ должны отвечать современному"],
            ["уровень развития техники и технологии, соответствовать заказу общества, иметь"],
            ["максимально конкретно отражалась основная идея работы."]]
    assert tables.vkr_topics(para) == []
    topics = [["№", "Темы выпускных квалификационных работ"], ["1", "Автоматизация участка механообработки"],
              ["2", "Система управления насосной станцией"]]
    assert tables.vkr_topics(topics) == ["Автоматизация участка механообработки", "Система управления насосной станцией"]
