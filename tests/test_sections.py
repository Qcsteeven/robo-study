from demo_site import render
from demo_site.data import ISOIU, PIE
from robot_op.extract import sections
from robot_op.extract.pdf import page_texts


def text(pdf: bytes) -> str:
    return "\n".join(page_texts(pdf))


def test_rpd_extraction():
    disc = ISOIU.groups[0][1][0]
    d = sections.extract("rpd", text(render.rpd_pdf(ISOIU, disc)))
    assert d["template"]["missing_required"] == [] and d["template"]["score"] == 1.0
    assert d["discipline"] == "Иностранный язык"
    assert d["ze"] == 7 and d["semesters"] == [1, 2, 3, 4]
    assert d["competencies"][0]["code"] == "УК-4"
    assert len(d["topics"]) == 4 and d["topics"][0] == "Грамматика профессионального текста"
    assert len(d["literature"]) == 2


def test_annotation_instead_of_practice_is_rejected():
    ok, why = sections.type_check("practice", text(render.practice_pdf(ISOIU, ISOIU.practices[1])))
    assert not ok and "аннотация" in why


def test_unknown_template_and_partial():
    new = [d for _, ds in ISOIU.groups for d in ds if d.template == "new"][0]
    assert sections.extract("rpd", text(render.rpd_pdf(ISOIU, new)))["template"]["score"] < 0.5
    fos = sections.extract("fos_gia", text(render.fos_gia_pdf(ISOIU)))
    assert fos["template"]["missing_required"] == ["Критерии оценивания"]
    assert sections.extract("fos_gia", text(render.fos_gia_pdf(PIE)))["template"]["missing_required"] == []


def test_practice_and_gia_and_oop():
    p = sections.extract("practice", text(render.practice_pdf(ISOIU, ISOIU.practices[0])))
    assert (p["practice_kind"], p["weeks"], p["semesters"]) == ("учебная", 2, [2]) and len(p["stages"]) == 4
    g = sections.extract("gia", text(render.gia_pdf(ISOIU)))
    assert (g["ze"], g["weeks"]) == (9, 6) and g["criteria"]
    o = sections.extract("oop", text(render.oop_pdf(ISOIU)))
    assert o["qualification"] == "бакалавр" and o["duration"] == "4 года" and len(o["areas"]) == 2
