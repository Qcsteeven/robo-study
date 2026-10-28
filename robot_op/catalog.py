"""Справочники робота: уровни образования, типы документов, статусы."""
from __future__ import annotations

import re

# Уровни образования раздела «Образование» сайта ИРНИТУ (ТЗ, рисунок 1)
LEVELS = {
    "bachelor": "Бакалавриат",
    "specialist": "Специалитет",
    "master": "Магистратура",
    "spo": "СПО",
    "postgraduate": "Аспирантура",
}
LEVEL_TONE = {"bachelor": "blue", "specialist": "lilac", "master": "cyan", "spo": "green", "postgraduate": "outline"}

_LEVEL_WORDS = [
    ("bachelor", r"бакалавр"),
    ("specialist", r"специалит"),
    ("master", r"магистр"),
    ("spo", r"\bспо\b|среднее\s+профессиональн"),
    ("postgraduate", r"аспирант"),
]


def level_key(text: str) -> str | None:
    t = text.strip().lower()
    for key, pattern in _LEVEL_WORDS:
        if re.search(pattern, t):
            return key
    return None


# Обрабатываемые типы документов (ТЗ, раздел 8): УП, КУГ, РПД, практики, ООП, ФОС ГИА, программа ГИА
DOC_TYPES = {
    "up": "УП",
    "kug": "КУГ",
    "oop": "ООП",
    "rpd": "РПД",
    "practice": "Практика",
    "fos_gia": "ФОС ГИА",
    "gia": "ГИА",
}
DOC_TYPE_TITLES = {
    "up": "Учебный план",
    "kug": "Календарный учебный график",
    "oop": "Основная образовательная программа",
    "rpd": "Рабочая программа дисциплины",
    "practice": "Рабочая программа практики",
    "fos_gia": "Фонд оценочных средств ГИА",
    "gia": "Программа государственной итоговой аттестации",
}
DOC_ORDER = ["up", "kug", "oop", "rpd", "practice", "fos_gia", "gia"]
# Типы, для которых LLM формирует краткое описание (ТЗ, шаг 10)
SUMMARY_TYPES = {"oop", "rpd", "practice", "fos_gia", "gia"}
# Одиночные документы ОП — их отсутствие на сайте даёт предупреждение (ТЗ, шаг 3)
REQUIRED_TYPES = ["up", "kug", "oop", "rpd", "practice", "fos_gia", "gia"]

# Статусы документа: ключ → (вид плашки, подпись). Совпадают с макросом status() в ui.html.
STATUSES = {
    "processed": ("success", "Обработан"),
    "partial": ("warning", "Частично обработан"),
    "needs_review": ("warning", "Требует проверки"),
    "error": ("danger", "Ошибка"),
    "in_progress": ("info", "В обработке"),
    "queued": ("info", "В очереди"),
    "unknown_template": ("neutral", "Неизвестный шаблон"),
    "skipped": ("neutral", "Пропущен"),
}
# Документы, которые попадают в перечень «требуют проверки» отчёта о запуске
REVIEW_STATUSES = {"needs_review", "partial", "unknown_template"}

RUN_STATUSES = {
    "queued": ("info", "В очереди"),
    "running": ("info", "Выполняется"),
    "finished": ("success", "Завершён"),
    "failed": ("danger", "Сбой"),
    "interrupted": ("warning", "Прерван"),
}

FORMS = {"очная": "Очная", "заочная": "Заочная", "очно-заочная": "Очно-заочная"}
