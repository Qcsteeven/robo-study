from robot_op.extract.schedule import _fix_days
from robot_op.robot.page import calendar_grid


def test_calendar_grid_like_document():
    # 2026/2027: 1 сентября — вторник, первая колонка начинается с понедельника 31 августа;
    # 31 августа 2027 — вторник, поэтому колонок 53, у последней нет номера недели
    weeks = ["Т"] * 16 + ["Э"] * 5 + ["К"] + ["Т"] * 16 + ["Э"] * 3 + ["У"] * 2 + ["К"] * 9
    days = {d: list(weeks) + ["К"] for d in ("пн", "вт", "ср", "чт", "пт", "сб")}
    days["ср"][9] = "*"                                  # 4 ноября
    kd = {"academic_year": "2026/2027", "weeks": 52,
          "courses": [{"course": "1", "group": "ИСИб-26", "weeks": weeks, "days": days}]}
    g = calendar_grid(kd, 2026)
    assert len(g["week_numbers"]) == 53 and g["week_numbers"][0] == 1 and g["week_numbers"][51] == 52
    assert g["week_numbers"][52] is None
    assert [m[0] for m in g["months"]][:3] == ["Сентябрь", "Октябрь", "Ноябрь"] and g["months"][0][1] == 5
    assert g["months"][-1] == ["Август", 5] and sum(m[1] for m in g["months"]) == 53
    mon = g["header"][0]
    assert mon["day"] == "пн" and mon["cells"][0] == {"n": 31, "out": True} and mon["cells"][1] == {"n": 7, "out": False}
    rows = {r["day"]: r["cells"] for r in g["courses"][0]["rows"]}
    assert list(rows) == ["пн", "вт", "ср", "чт", "пт", "сб"]
    assert rows["пн"][0] == "out" and rows["вт"][0] == ""            # 31 августа — вне учебного года, 1 сентября — учёба
    assert rows["ср"][9] == "*" and rows["чт"][9] == ""
    assert rows["пн"][52] == "К" and rows["ср"][52] == "out"          # 30–31 августа — каникулы, с 1 сентября — штриховка
    labels = [l["label"] for l in g["legend"]]
    assert "нерабочие праздничные дни" in labels and labels[-1] == "обучение не ведётся"


def test_calendar_grid_from_weeks_only():
    """Старый JSON-файл КУГ без обозначений по дням: каждый день недели получает обозначение недели."""
    kd = {"academic_year": "2026/2027", "weeks": 52, "courses": [{"course": "1", "weeks": ["Э"] * 52}]}
    rows = calendar_grid(kd, 2026)["courses"][0]["rows"]
    assert rows[2]["cells"][0] == "Э" and rows[0]["cells"][0] == "out"


def test_fix_days_corrects_single_ocr_errors():
    def course(over=None):
        days = {d: ["Э", "Э", "П"] for d in ("пн", "вт", "ср", "чт", "пт", "сб")}
        days["пт"][1] = "*"
        for (d, i), v in (over or {}).items():
            days[d][i] = v
        return {"days": days}
    a, b, c = course(), course({("пт", 1): ""}), course({("чт", 2): "Н", ("сб", 0): ""})
    _fix_days([a, b, c])
    assert b["days"]["пт"][1] == "*"          # праздник общий для всех курсов
    assert c["days"]["чт"][2] == "П"          # один день недели разошёлся с остальными
    assert c["days"]["сб"][0] == "Э"
    assert a["days"] == course()["days"]
