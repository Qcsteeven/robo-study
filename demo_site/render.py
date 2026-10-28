"""Генерация страниц и документов демо-сайта: HTML как на www.istu.edu, PDF с текстовым слоем и «сканы» УП/КУГ."""
from __future__ import annotations

import datetime as dt
import html
import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .data import LEVEL_YEARS, PROGRAMS, DemoProgram, Disc, Practice

# одинаковые PDF при каждой генерации: документ «на сайте» не меняется между запусками робота
rl_config.invariant = 1
FIXED_DATE = dt.datetime(2026, 10, 5, 8, 0, 0)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def _font_path(bold: bool = False, serif: bool = False) -> str:
    name = ("DejaVuSerif" if serif else "DejaVuSans") + ("-Bold" if bold else "") + ".ttf"
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return str(p)
    raise FileNotFoundError(f"Не найден шрифт {name} (установите fonts-dejavu-core)")


_registered = False


def _register_fonts() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont("DejaVu", _font_path()))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", _font_path(bold=True)))
        _registered = True


# ======================================================================== HTML

STYLE = """
body{font-family:Arial,sans-serif;margin:0;color:#333} .wrap{max-width:1100px;margin:0 auto;padding:20px}
h1{font-size:28px} table{border-collapse:collapse;width:100%;margin-bottom:24px} td,th{border:1px solid #ddd;padding:8px;vertical-align:top}
a{color:#337ab7;text-decoration:none} .sig{background:#e8e3a6;font-size:10px;padding:1px 4px;border-radius:3px;margin-left:4px}
.panel{border:1px solid #ddd;margin-bottom:12px}.panel-heading{background:#f5f5f5;padding:12px;font-size:18px}
.modal{display:none}.direction{font-size:20px;font-weight:bold;margin:28px 0 10px}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{STYLE}</style></head><body><div class=wrap><p><i>Демо-копия структуры раздела «Образование» "
            f"сайта ИРНИТУ. Данные условные.</i></p>{body}</div></body></html>")


def education_page() -> str:
    years = sorted({y for ys in LEVEL_YEARS.values() for y in ys})
    rows = []
    for level, ys in LEVEL_YEARS.items():
        cells = "".join(f"<td><a href='eduop/?level={level}&amp;year={y}'>{y}</a></td>" if y in ys else "<td></td>" for y in years)
        rows.append(f"<tr><td>{level}</td>{cells}</tr>")
    body = ("<h1>Образование</h1><p>Информация по образовательным программам, в том числе адаптированным</p>"
            f"<table>{''.join(rows)}</table>"
            "<p>Информация по образовательным программам, в том числе адаптированным (Филиал ФГБОУ ВО ИРНИТУ в г. Усолье-Сибирском)</p>"
            "<table><tr><td>СПО</td>" + "".join(f"<td><a href='eduop/?level=СПО-филиал&amp;year={y}'>{y}</a></td>" for y in (2022, 2023, 2024, 2025, 2026)) + "</tr></table>")
    return page("Образование", body)


def _sig(url: str) -> str:
    return f"<a class=sig href='{url}.sig'>sig</a>"


def _doc(url: str, title: str) -> str:
    return f"<a href='{url}'>{html.escape(title)}</a>{_sig(url)}"


def op_list_page(level: str, year: int) -> str:
    progs = [p for p in PROGRAMS if p.level == level and p.year == year]
    parts = [f"<h1>Образовательные программы: {html.escape(level)}, {year}</h1>"]
    if not progs:
        parts.append("<p>Для выбранного уровня и года набора в демо-данных нет программ.</p>")
    for p in progs:
        base = f"../docs/{p.slug}"
        parts.append(f"<div class=direction>{p.code} {html.escape(p.direction)}</div>")
        rpd_items = "".join(f"<li>{_doc(f'{base}/rpd/{d.index}.pdf', d.name)}</li>" for _, ds in p.groups for d in ds)
        if p.broken_rpd:
            rpd_items += f"<li>{_doc(f'{base}/rpd/missing.pdf', 'Основы проектной деятельности')}</li>"
        practice = "<br>".join(_doc(f"{base}/practice/{pr.index}.pdf", f"{pr.kind} практика: {pr.type}") for pr in p.practices)
        kug = "" if p.no_kug else _doc(f"{base}/kug.pdf", "Календарный учебный график")
        rows = [
            ("Уровень", p.level), ("Форма обучения", p.form), ("Год", str(p.year)),
            ("образовательный стандарт", _doc(f"{base}/fgos.pdf", "Образовательный стандарт ФГОС")),
            ("учебный план", _doc(f"{base}/up.pdf", "Учебный план")),
            ("адаптированный учебный план", _doc(f"{base}/aup.pdf", "Адаптированный учебный план")),
            ("календарный учебный график", kug),
            ("ООП", _doc(f"{base}/oop.pdf", "ООП")),
            ("АОП", _doc(f"{base}/aop.pdf", "АОП")),
            ("ФОС ГИА", _doc(f"{base}/fos_gia.pdf", "ФОС ГИА")),
            ("программа ГИА", _doc(f"{base}/gia.pdf", "Программа ГИА")),
            ("практика", practice),
            ("РПД", f"<a href='#' data-target='#rpd-{p.slug}' data-toggle='modal'>РПД</a>"),
            ("аннотации", _doc(f"{base}/annot.pdf", "Аннотации")),
            ("рабочая программа воспитания", _doc(f"{base}/vosp.pdf", "Программа воспитания")),
        ]
        trs = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
        parts.append(f"<div class='panel panel-default'><div class=panel-heading><a data-toggle=collapse href='#c-{p.slug}'>"
                     f"{html.escape(p.profile)} ({p.form})</a></div><div id='c-{p.slug}' class='panel-collapse collapse'>"
                     f"<table>{trs}</table></div></div>"
                     f"<div class=modal id='rpd-{p.slug}'><div class=modal-header>РПД</div><ul>{rpd_items}</ul></div>")
    return page(f"{level} {year}", "".join(parts))


# ======================================================================== PDF с текстовым слоем

def _styles():
    _register_fonts()
    return {
        "title": ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=14, leading=18, alignment=1, spaceAfter=8),
        "center": ParagraphStyle("c", fontName="DejaVu", fontSize=11, leading=14, alignment=1),
        "h": ParagraphStyle("h", fontName="DejaVu-Bold", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", fontName="DejaVu", fontSize=10.5, leading=14, spaceAfter=3),
    }


def _pdf(flow_builder) -> bytes:
    buf = io.BytesIO()
    st = _styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25 * mm, rightMargin=15 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                            title="Документ ОП", author="Демо-сайт")
    doc.build(flow_builder(st))
    return buf.getvalue()


def _titlepage(st, kind: str, name: str, p: DemoProgram) -> list:
    return [
        Paragraph("Министерство науки и высшего образования Российской Федерации", st["center"]),
        Paragraph("Федеральное государственное бюджетное образовательное учреждение высшего образования", st["center"]),
        Paragraph("«Иркутский национальный исследовательский технический университет»", st["center"]),
        Spacer(1, 30 * mm),
        Paragraph(kind, st["title"]),
        Paragraph(name, st["title"]) if name else Spacer(1, 1),
        Spacer(1, 6 * mm),
        Paragraph(f"Направление подготовки: {p.code} {p.direction}", st["center"]),
        Paragraph(f"Профиль: {p.profile}", st["center"]),
        Paragraph(f"Форма обучения: {p.form}. Год набора: {p.year}", st["center"]),
        Spacer(1, 40 * mm),
        Paragraph("Иркутск", st["center"]),
    ]


def _ctl(d: Disc) -> str:
    parts = []
    if d.exam:
        parts.append("экзамен (" + ", ".join(map(str, d.exam)) + " семестр)")
    if d.zach:
        parts.append("зачет (" + ", ".join(map(str, d.zach)) + " семестр)")
    if d.kp:
        parts.append("курсовой проект")
    if d.kr:
        parts.append("курсовая работа")
    return ", ".join(parts) or "зачет"


def rpd_pdf(p: DemoProgram, d: Disc) -> bytes:
    h = d.hours()
    sems = sorted(d.sems)

    def build(st):
        f = _titlepage(st, "РАБОЧАЯ ПРОГРАММА ДИСЦИПЛИНЫ", f"«{d.name}»", p)
        if d.template == "new":
            # новый шаблон: другие названия разделов — робот должен пометить «неизвестный шаблон»
            f += [Paragraph("Паспорт дисциплины", st["h"]),
                  Paragraph(f"Дисциплина «{d.name}» реализуется в {sems[0]} семестре.", st["p"]),
                  Paragraph("Карта формируемых умений", st["h"]),
                  Paragraph("; ".join(d.topics), st["p"]),
                  Paragraph("Учебные ресурсы", st["h"]),
                  Paragraph("Электронный курс в ЭИОС университета.", st["p"])]
            return f
        f += [Paragraph("1 Перечень планируемых результатов обучения по дисциплине, соотнесенных с планируемыми результатами освоения образовательной программы", st["h"]),
              Paragraph(f"Цель освоения дисциплины — {d.goal}.", st["p"])]
        for c in d.competencies:
            f.append(Paragraph(f"{c} — способен применять знания и умения, полученные при изучении дисциплины «{d.name}», в профессиональной деятельности; "
                               f"индикатор ИД-1.{c}: применяет методы дисциплины для решения прикладных задач.", st["p"]))
        f += [Paragraph("2 Место дисциплины в структуре ООП", st["h"]),
              Paragraph(f"Дисциплина «{d.name}» относится к блоку 1 «Дисциплины (модули)», индекс {d.index}. "
                        f"Изучается в {', '.join(map(str, sems))} семестре.", st["p"]),
              Paragraph("3 Объем дисциплины", st["h"]),
              Paragraph(f"Общая трудоемкость дисциплины составляет {h['ze']} зачетных единиц, {h['plan']} академических часов. "
                        f"Контактная работа — {h['aud']} ч, самостоятельная работа — {h['sr']} ч. Форма промежуточной аттестации: {_ctl(d)}.", st["p"])]
        rows = [["Семестр", "Лекции", "Лаб. работы", "Практ. занятия", "СРС"]]
        for s in sems:
            lec, lab, pr = d.sems[s]
            rows.append([str(s), str(lec), str(lab), str(pr), str(h["sr_sem"][s])])
        t = Table(rows)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 9), ("GRID", (0, 0), (-1, -1), 0.5, "#000000")]))
        f += [t, Paragraph("4 Содержание дисциплины", st["h"])]
        for i, topic in enumerate(d.topics, 1):
            f.append(Paragraph(f"Тема {i}. {topic}. Лекционные и практические занятия, самостоятельное изучение материала.", st["p"]))
        f += [Paragraph("5 Перечень учебно-методического обеспечения для самостоятельной работы обучающихся", st["h"]),
              Paragraph("Методические указания к лабораторным работам и самостоятельной работе размещены в ЭИОС.", st["p"]),
              Paragraph("6 Фонд оценочных средств для проведения промежуточной аттестации обучающихся по дисциплине", st["h"]),
              Paragraph(f"Оценочные средства: тесты, отчёты по лабораторным работам, вопросы к {('экзамену' if d.exam else 'зачету')}.", st["p"]),
              Paragraph("7 Основная и дополнительная литература", st["h"]),
              Paragraph(f"1. {d.name}: учебник / под ред. И. И. Иванова. — М.: Юрайт, 2022. — 350 с.", st["p"]),
              Paragraph(f"2. Петров П. П. Практикум по дисциплине «{d.name}». — Иркутск: Изд-во ИРНИТУ, 2021. — 120 с.", st["p"]),
              Paragraph("8 Перечень ресурсов информационно-телекоммуникационной сети «Интернет»", st["h"]),
              Paragraph("1. ЭБС «Лань» — https://e.lanbook.com", st["p"]),
              Paragraph("9 Перечень информационных технологий, используемых при осуществлении образовательного процесса", st["h"]),
              Paragraph("Операционная система Linux, офисный пакет, среда программирования.", st["p"]),
              Paragraph("10 Материально-техническое обеспечение дисциплины", st["h"]),
              Paragraph("Учебные аудитории с мультимедийным оборудованием, компьютерный класс.", st["p"])]
        return f
    return _pdf(build)


def practice_pdf(p: DemoProgram, pr: Practice) -> bytes:
    def build(st):
        if pr.annotation_only:
            return [Paragraph("АННОТАЦИЯ рабочей программы практики", st["title"]),
                    Paragraph(f"{pr.kind} практика: {pr.type}", st["title"]),
                    Paragraph(f"Практика проводится в {pr.semester} семестре, продолжительность {pr.weeks} недели.", st["p"])]
        f = _titlepage(st, "РАБОЧАЯ ПРОГРАММА ПРАКТИКИ", f"{pr.kind} практика: {pr.type}", p)
        f += [Paragraph("1 Вид практики, способ и форма ее проведения", st["h"]),
              Paragraph(f"Вид практики: {pr.kind.lower()}. Тип практики: {pr.type}. Способ проведения: стационарная. Форма проведения: дискретно.", st["p"]),
              Paragraph("2 Перечень планируемых результатов обучения при прохождении практики", st["h"]),
              Paragraph("УК-2 — способен определять круг задач в рамках поставленной цели; ПК-1 — способен разрабатывать программные модули.", st["p"]),
              Paragraph("3 Место практики в структуре образовательной программы", st["h"]),
              Paragraph(f"Практика относится к блоку 2 «Практика», индекс {pr.index}, проводится в {pr.semester} семестре.", st["p"]),
              Paragraph("4 Объем практики", st["h"]),
              Paragraph(f"Объем практики составляет {pr.weeks * 1.5:g} зачетных единиц, продолжительность {pr.weeks} недели.", st["p"]),
              Paragraph("5 Содержание практики", st["h"])]
        for i, s in enumerate(pr.stages, 1):
            f.append(Paragraph(f"Этап {i}. {s}.", st["p"]))
        f += [Paragraph("6 Формы отчетности по практике", st["h"]),
              Paragraph("Отчёт по практике, дневник практики, отзыв руководителя. Промежуточная аттестация — зачет с оценкой.", st["p"]),
              Paragraph("7 Фонд оценочных средств для проведения промежуточной аттестации по практике", st["h"]),
              Paragraph("Защита отчёта, собеседование по вопросам программы практики.", st["p"]),
              Paragraph("8 Учебная литература и ресурсы сети «Интернет»", st["h"]),
              Paragraph("1. Сидоров С. С. Организация практики студентов. — Иркутск: Изд-во ИРНИТУ, 2022. — 80 с.", st["p"]),
              Paragraph("9 Материально-техническая база для проведения практики", st["h"]),
              Paragraph("Компьютерные классы университета и рабочие места профильных организаций.", st["p"])]
        return f
    return _pdf(build)


def oop_pdf(p: DemoProgram) -> bytes:
    o = p.oop_text

    def build(st):
        f = _titlepage(st, "ОСНОВНАЯ ПРОФЕССИОНАЛЬНАЯ ОБРАЗОВАТЕЛЬНАЯ ПРОГРАММА", "", p)
        f += [Paragraph("1 Общая характеристика образовательной программы", st["h"]),
              Paragraph(o["summary_facts"], st["p"]),
              Paragraph(f"Квалификация, присваиваемая выпускникам: {'бакалавр' if p.level == 'бакалавриат' else 'магистр'}. "
                        f"Срок получения образования: {len(p.weeks) // 2} года. Объем программы: {len(p.weeks) * 30} зачетных единиц.", st["p"]),
              Paragraph("2 Характеристика профессиональной деятельности выпускников", st["h"]),
              Paragraph("Области профессиональной деятельности: " + "; ".join(o["areas"]) + ".", st["p"]),
              Paragraph("Типы задач профессиональной деятельности: " + ", ".join(o["tasks"]) + ".", st["p"]),
              Paragraph("3 Планируемые результаты освоения образовательной программы", st["h"])]
        for c in o["competencies"]:
            f.append(Paragraph(f"{c} — компетенция, формируемая дисциплинами и практиками программы.", st["p"]))
        f += [Paragraph("4 Структура и объем образовательной программы", st["h"]),
              Paragraph("Блок 1 «Дисциплины (модули)», блок 2 «Практика», блок 3 «Государственная итоговая аттестация».", st["p"]),
              Paragraph("5 Условия реализации образовательной программы", st["h"]),
              Paragraph(f"Практическая подготовка проводится на базе: {o['partners']}.", st["p"])]
        return f
    return _pdf(build)


def gia_pdf(p: DemoProgram) -> bytes:
    g = p.gia

    def build(st):
        f = _titlepage(st, "ПРОГРАММА ГОСУДАРСТВЕННОЙ ИТОГОВОЙ АТТЕСТАЦИИ", "", p)
        f += [Paragraph("1 Общие положения", st["h"]),
              Paragraph("Государственная итоговая аттестация проводится в целях определения соответствия результатов освоения программы требованиям ФГОС.", st["p"]),
              Paragraph("2 Формы государственной итоговой аттестации", st["h"])]
        for form in g["forms"]:
            f.append(Paragraph(f"— {form}.", st["p"]))
        f += [Paragraph("3 Объем государственной итоговой аттестации", st["h"]),
              Paragraph(f"Объем ГИА составляет {g['ze']} зачетных единиц, продолжительность {g['weeks']} недель.", st["p"]),
              Paragraph("4 Требования к выпускной квалификационной работе", st["h"]),
              Paragraph("ВКР выполняется в виде бакалаврской работы объемом 50–80 страниц и включает пояснительную записку и презентацию. Примерные темы: "
                        + "; ".join(g["vkr_topics"]) + ".", st["p"]),
              Paragraph("5 Порядок проведения государственной итоговой аттестации", st["h"]),
              Paragraph("Защита проводится на открытом заседании государственной экзаменационной комиссии; доклад — до 10 минут.", st["p"]),
              Paragraph("6 Критерии оценивания", st["h"]),
              Paragraph("«Отлично» — работа выполнена в полном объёме, выводы обоснованы; «хорошо» — имеются незначительные недочёты; "
                        "«удовлетворительно» — работа выполнена с существенными замечаниями.", st["p"])]
        return f
    return _pdf(build)


def fos_gia_pdf(p: DemoProgram) -> bytes:
    def build(st):
        f = _titlepage(st, "ФОНД ОЦЕНОЧНЫХ СРЕДСТВ ГОСУДАРСТВЕННОЙ ИТОГОВОЙ АТТЕСТАЦИИ", "", p)
        f += [Paragraph("1 Перечень компетенций, проверяемых на государственной итоговой аттестации", st["h"]),
              Paragraph(", ".join(p.oop_text["competencies"]) + ".", st["p"]),
              Paragraph("2 Описание показателей оценивания компетенций", st["h"]),
              Paragraph("Показатели: качество пояснительной записки, доклада и ответов на вопросы комиссии.", st["p"]),
              Paragraph("3 Типовые контрольные задания и примерная тематика ВКР", st["h"])]
        for t in p.gia["vkr_topics"]:
            f.append(Paragraph(f"— {t}.", st["p"]))
        if not p.fos_without_criteria:
            f += [Paragraph("4 Критерии оценивания", st["h"]),
                  Paragraph("Оценка «отлично», «хорошо», «удовлетворительно» или «неудовлетворительно» выставляется по совокупности показателей.", st["p"])]
        return f
    return _pdf(build)


def dummy_pdf(title: str) -> bytes:
    def build(st):
        return [Paragraph(title, st["title"]), Paragraph("Документ не обрабатывается роботом.", st["p"])]
    return _pdf(build)


# ======================================================================== «сканы» УП и КУГ

class _Canvas:
    def __init__(self, w: int, h: int):
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)
        self.fonts: dict = {}

    def font(self, size: int, bold: bool = False, serif: bool = False):
        key = (size, bold, serif)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(_font_path(bold, serif), size)
        return self.fonts[key]

    def hline(self, x0, x1, y, wdt=3):
        self.d.rectangle([x0, y - wdt // 2, x1, y + (wdt - 1) // 2], fill=0)

    def vline(self, x, y0, y1, wdt=3):
        self.d.rectangle([x - wdt // 2, y0, x + (wdt - 1) // 2, y1], fill=0)

    def text_in(self, box, text, size=26, bold=False, align="center", serif=False):
        if not text:
            return
        x0, y0, x1, y1 = box
        f = self.font(size, bold, serif)
        lines = _wrap(self.d, text, f, x1 - x0 - 12)
        lh = size + 6
        total = lh * len(lines)
        y = (y0 + y1) / 2 - total / 2
        for line in lines:
            w = self.d.textlength(line, font=f)
            x = x0 + 8 if align == "left" else (x0 + x1) / 2 - w / 2
            self.d.text((x, y), line, font=f, fill=0)
            y += lh

    def to_pdf(self, dpi: int = 300, noise: bool = True) -> bytes:
        img = self.img
        if noise:
            rnd = random.Random(42)
            img = img.filter(ImageFilter.GaussianBlur(0.6))
            px = img.load()
            for _ in range(img.width * img.height // 400):
                x, y = rnd.randrange(img.width), rnd.randrange(img.height)
                px[x, y] = rnd.choice((120, 170, 200))
        buf = io.BytesIO()
        img.save(buf, "PDF", resolution=dpi, creationDate=FIXED_DATE.timetuple(), modDate=FIXED_DATE.timetuple())
        return buf.getvalue()


def _wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if d.textlength(cand, font=font) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _digits(sems: list[int]) -> str:
    return "".join(str(s) if s < 10 else "ABC"[s - 10] for s in sems)


def up_scan_pdf(p: DemoProgram) -> bytes:
    S = len(p.weeks)
    fixed = [("Индекс", 230), ("Наименование", 700), ("Экза мен", 92), ("Зачет", 92), ("Зачет с оц.", 92), ("КП", 70), ("КР", 70),
             ("Факт", 86), ("По плану", 96), ("Конт. раб.", 96), ("Ауд.", 90), ("СР", 90), ("Конт роль", 92)]
    sub = ["Лек", "Лаб", "Пр", "СР"]
    sub_w = 82 if S > 4 else 110
    widths = [w for _, w in fixed] + [sub_w] * (4 * S)
    xs = [80]
    for w in widths:
        xs.append(xs[-1] + w)
    W = xs[-1] + 80
    # строки таблицы
    total_rows = 3 + 2 + sum(1 + len(ds) for _, ds in p.groups) + 1 + len(p.practices) + 1 + 1
    row_h, head_h = 58, [64, 96, 60]
    H = 260 + sum(head_h) + 2 * row_h * total_rows + 120
    c = _Canvas(W, H)
    c.text_in((xs[0], 60, xs[-1], 130), f"{p.code}  {p.profile}, год начала подготовки {p.year}", size=44, align="left")
    top = 180
    y = [top, top + head_h[0], top + head_h[0] + head_h[1], top + sum(head_h)]
    nf = len(fixed)
    # шапка: строка 1
    c.hline(xs[0], xs[-1], y[0])
    c.text_in((xs[0], y[0], xs[1], y[1]), "-")
    c.text_in((xs[1], y[0], xs[2], y[1]), "-")
    c.text_in((xs[2], y[0], xs[7], y[1]), "Форма контроля")
    c.text_in((xs[7], y[0], xs[8], y[1]), "з.е.")
    c.text_in((xs[8], y[0], xs[13], y[1]), "Итого акад.часов")
    for k in range((S + 1) // 2):
        a = nf + 8 * k
        b = min(nf + 8 * (k + 1), nf + 4 * S)
        c.text_in((xs[a], y[0], xs[b], y[1]), f"Курс {k + 1}")
    c.hline(xs[0], xs[-1], y[1])
    # строка 2: подписи фиксированных колонок (объединены со строкой 3) и семестры
    for i, (name, _) in enumerate(fixed):
        if i > 1:
            c.text_in((xs[i], y[1], xs[i + 1], y[3]), name, size=24)
        else:
            c.text_in((xs[i], y[1], xs[i + 1], y[3]), name, size=26)
    for s in range(S):
        a = nf + 4 * s
        c.text_in((xs[a], y[1], xs[a + 4], y[2]), f"Семестр {s + 1} [{p.weeks[s]} нед]", size=26)
        for j, name in enumerate(sub):
            c.text_in((xs[a + j], y[2], xs[a + j + 1], y[3]), name, size=24)
    c.hline(xs[nf], xs[-1], y[2])
    c.hline(xs[0], xs[-1], y[3])
    # вертикальные линии шапки
    for i, x in enumerate(xs):
        if i in (0, len(xs) - 1) or i <= nf:
            if i in (3, 4, 5, 6, 9, 10, 11, 12):   # внутри «Форма контроля» и «Итого» строка 1 объединена
                c.vline(x, y[1], y[3])
            else:
                c.vline(x, y[0], y[3])
        else:
            k = i - nf
            if k % 8 == 0:
                c.vline(x, y[0], y[3])
            elif k % 4 == 0:
                c.vline(x, y[1], y[3])
            else:
                c.vline(x, y[2], y[3])
    # тело
    yy = y[3]

    def group_row(title: str, bold=True):
        nonlocal yy
        c.d.rectangle([xs[0], yy, xs[-1], yy + row_h], fill=225)
        c.text_in((xs[0], yy, xs[7], yy + row_h), title, size=26, bold=bold, align="left")
        c.vline(xs[0], yy, yy + row_h)
        for i in range(7, len(xs)):
            c.vline(xs[i], yy, yy + row_h)
        yy += row_h
        c.hline(xs[0], xs[-1], yy)

    def data_row(vals: list[str], bold_name=False):
        nonlocal yy
        n_lines = len(_wrap(c.d, vals[1], c.font(25), xs[2] - xs[1] - 12))
        rh = max(row_h, 31 * n_lines + 18)      # строка растёт под перенос наименования, как в настоящем УП
        for i, v in enumerate(vals):
            if v:
                align = "left" if i in (0, 1) else "center"
                c.text_in((xs[i], yy, xs[i + 1], yy + rh), v, size=24 if i != 1 else 25, bold=bold_name and i == 1, align=align)
        for x in xs:
            c.vline(x, yy, yy + rh)
        yy += rh
        c.hline(xs[0], xs[-1], yy)

    group_row("Блок 1.Дисциплины (модули)")
    for part, discs in p.groups:
        group_row(part)
        for d in discs:
            h = d.hours()
            aud = h["aud"]
            if p.up_mismatch == d.index:
                aud += 10        # намеренное расхождение: «Ауд.» не равно сумме по семестрам
            vals = [d.index, d.name, _digits(d.exam), _digits(d.zach), _digits(d.zach_oc), _digits(d.kp), _digits(d.kr),
                    str(h["ze"]), str(h["plan"]), str(h["kont"]), str(aud), str(h["sr"]), str(h["control"] or "")]
            for s in range(1, S + 1):
                if s in d.sems:
                    lec, lab, pr = d.sems[s]
                    vals += [str(lec or ""), str(lab or ""), str(pr or ""), str(h["sr_sem"][s])]
                else:
                    vals += ["", "", "", ""]
            data_row(vals)
    group_row("Блок 2.Практика")
    for pr in p.practices:
        ze = int(pr.weeks * 1.5)
        vals = [pr.index, f"{pr.kind} практика: {pr.type}", "", "", _digits([pr.semester]), "", "", str(ze), str(ze * 36), "", "", str(ze * 36), ""]
        vals += [""] * (4 * S)
        data_row(vals)
    group_row("Блок 3.Государственная итоговая аттестация")
    vals = ["Б3.01(Д)", "Выполнение и защита выпускной квалификационной работы", "", "", "", "", "", str(p.gia["ze"]), str(p.gia["ze"] * 36), "", "", str(p.gia["ze"] * 36), ""]
    data_row(vals + [""] * (4 * S))
    c.img = c.img.crop((0, 0, W, yy + 100))
    return c.to_pdf()


MONTHS = ["Сентябрь", "Октябрь", "Ноябрь", "Декабрь", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август"]
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def kug_weeks(start_year: int) -> list[dt.date]:
    """Понедельники 52 недель учебного года (неделя 1 содержит 1 сентября)."""
    d = dt.date(start_year, 9, 1)
    monday = d - dt.timedelta(days=d.weekday())
    return [monday + dt.timedelta(weeks=i) for i in range(52)]


def kug_scan_pdf(p: DemoProgram) -> bytes:
    k = p.kug
    start = int(k["year"][:4])
    mondays = kug_weeks(start)
    cw, x0 = 56, 60
    left = [90, 210, 60]
    xs = [x0]
    for w in left + [cw] * 52:
        xs.append(xs[-1] + w)
    W = xs[-1] + 60
    rh = 46
    n_courses = len(k["courses"])
    H = 360 + rh * (1 + 7 + 1) + rh * 6 * n_courses + 120
    c = _Canvas(W, H)
    c.text_in((x0, 40, W - 60, 110), f"Календарный учебный график на {k['year']} учебный год", size=46, bold=True, serif=True)
    c.text_in((x0, 110, W - 60, 170), f"направление {p.code} \"{p.direction}\", профиль \"{p.profile}\"", size=32, serif=True)
    c.text_in((x0, 170, W - 60, 220), f"{p.form.capitalize()} форма обучения", size=32, serif=True)
    top = 260
    # строка месяцев: неделя относится к месяцу своего четверга
    month_of = [(m + dt.timedelta(days=3)).month for m in mondays]
    spans: list[tuple[int, int, int]] = []
    for i, m in enumerate(month_of):
        if spans and spans[-1][0] == m:
            spans[-1] = (m, spans[-1][1], i)
        else:
            spans.append((m, i, i))
    wx = 3   # первая недельная колонка в xs
    y = top
    c.hline(xs[0], xs[-1], y)
    for m, a, b in spans:
        c.text_in((xs[wx + a], y, xs[wx + b + 1], y + rh), MONTHS[(m - 9) % 12], size=26, bold=True, serif=True)
        c.vline(xs[wx + a], y, y + rh)
    y += rh
    c.hline(xs[wx], xs[-1], y)
    # строки дат
    for di, day in enumerate(DAYS):
        c.text_in((xs[2], y, xs[3], y + rh), day, size=24, serif=True)
        for i, m in enumerate(mondays):
            dte = m + dt.timedelta(days=di)
            c.text_in((xs[wx + i], y, xs[wx + i + 1], y + rh), str(dte.day), size=22, serif=True)
        y += rh
        c.hline(xs[2], xs[-1], y)
    # номера недель
    for i in range(52):
        c.text_in((xs[wx + i], y, xs[wx + i + 1], y + rh), str(i + 1), size=22, bold=True, serif=True)
    head_bottom = y + rh
    c.text_in((xs[0], top, xs[1], head_bottom), "Курс", size=26, serif=True)
    c.text_in((xs[1], top, xs[2], head_bottom), "Набор", size=26, serif=True)
    for x in xs:
        c.vline(x, top if x in (xs[0], xs[1], xs[2], xs[3], xs[-1]) else top + rh, head_bottom)
    y = head_bottom
    c.hline(xs[0], xs[-1], y, 5)
    holidays = {(start, 11, 4), (start + 1, 1, 7), (start + 1, 2, 23), (start + 1, 3, 8), (start + 1, 5, 1), (start + 1, 5, 9), (start + 1, 6, 12)}
    for course, group, codes in k["courses"]:
        y0 = y
        for di, day in enumerate(DAYS[:6]):
            c.text_in((xs[2], y, xs[3], y + rh), day, size=24, serif=True)
            for i, m in enumerate(mondays):
                dte = m + dt.timedelta(days=di)
                code = codes.get(i + 1, "")
                if not code and (dte.year, dte.month, dte.day) in holidays:
                    code = "*"
                if not code and i >= 44:
                    code = "К"       # летние каникулы
                c.text_in((xs[wx + i], y, xs[wx + i + 1], y + rh), code, size=26, serif=True)
            y += rh
            c.hline(xs[2], xs[-1], y)
        c.text_in((xs[0], y0, xs[1], y), course, size=28, serif=True)
        c.text_in((xs[1], y0, xs[2], y), group, size=26, serif=True)
        c.hline(xs[0], xs[-1], y, 5)
        for x in xs:
            c.vline(x, y0, y)
    c.img = c.img.crop((0, 0, W, y + 80))
    return c.to_pdf()


# ======================================================================== маршрутизация документов

def document(slug: str, path: str) -> bytes | None:
    p = next((x for x in PROGRAMS if x.slug == slug), None)
    if p is None:
        return None
    discs = {d.index: d for _, ds in p.groups for d in ds}
    pracs = {pr.index: pr for pr in p.practices}
    if path == "up.pdf":
        return up_scan_pdf(p)
    if path == "kug.pdf" and not p.no_kug:
        return kug_scan_pdf(p)
    if path == "oop.pdf":
        return oop_pdf(p)
    if path == "gia.pdf":
        return gia_pdf(p)
    if path == "fos_gia.pdf":
        return fos_gia_pdf(p)
    if path.startswith("rpd/") and path[4:-4] in discs:
        return rpd_pdf(p, discs[path[4:-4]])
    if path.startswith("practice/") and path[9:-4] in pracs:
        return practice_pdf(p, pracs[path[9:-4]])
    if path in ("fgos.pdf", "aup.pdf", "aop.pdf", "annot.pdf", "vosp.pdf"):
        return dummy_pdf(path)
    return None
