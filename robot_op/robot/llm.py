"""Модуль на основе LLM: краткие описания по первичным JSON-файлам (ТЗ, шаг 10).

В LLM передаются только первичные JSON-файлы с общедоступными сведениями (ТЗ, раздел 7).
Поддерживаются локальная LLM через Ollama, любой OpenAI-совместимый API и GigaChat (Сбер): ключ авторизации
обменивается на токен доступа (действует 30 минут), TLS — с корневым сертификатом Минцифры.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import time
import uuid
from pathlib import Path

import certifi
import httpx

from ..config import ROOT, LlmSettings
from . import program_summary

PROVIDERS = ("ollama", "openai", "gigachat")
GIGACHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1"


class LlmError(Exception):
    pass


class _SummaryQualityError(LlmError):
    pass


SYSTEM = (
    "Ты помогаешь абитуриентам и студентам ИРНИТУ разобраться в документах образовательных программ. "
    "Пиши по-русски, простым языком, без канцелярита и без списков. Используй только сведения из переданного JSON, "
    "ничего не придумывай; если сведений мало — напиши короче. Не упоминай JSON, поля и сам документ."
)

TASKS = {
    "oop": "Составь общую характеристику образовательной программы в трёх коротких предложениях, до 600 знаков. "
           "Начни с названия профиля и точного названия квалификации выпускника. "
           "Укажи срок обучения и объём в зачётных единицах, если они есть. "
           "Кратко опиши профессиональную деятельность по указанным областям и типам задач. "
           "Пиши в настоящем времени. Не включай реквизиты приказов, ФГОС и перечень документов.",
    "rpd": "Опиши дисциплину в 2–3 предложениях: чему она учит и основное содержание (ключевые темы, "
           "лабораторные и практические занятия).",
    "practice": "Опиши практику в 2–3 предложениях: вид и тип практики, что студент делает на ней по этапам, чем она завершается.",
    "gia": "Опиши государственную итоговую аттестацию в 3–4 коротких предложениях, до 650 знаков. "
           "Назови форму аттестации и трудоёмкость ровно как в исходных данных. "
           "Не пересчитывай часы в недели, месяцы или зачётные единицы, не добавляй других чисел. "
           "Кратко назови одно требование к работе и опиши её защиту перед комиссией. Не используй примеры работ.",
    "fos_gia": "Опиши в 2–3 предложениях, как оценивается государственная итоговая аттестация: какие компетенции "
               "проверяются, по каким показателям и критериям выставляется оценка. "
               "Назови основные критерии и средства оценки, не перечисляй коды компетенций. "
               "Если указана пятибалльная шкала, используй её название без диапазона баллов.",
}


RETRY_TASKS = {
    "oop": (
        "Напиши ровно три коротких предложения одним абзацем. Сначала назови профиль и точную квалификацию. "
        "Затем укажи срок обучения и объём, если они известны. Закончить нужно кратким описанием профессиональных задач. "
        "Используй только приведённые сведения. Ответ без заголовка и списка."
    ),
    "gia": (
        "Кратко опиши ГИА в трёх предложениях одним абзацем. Сначала назови форму аттестации. "
        "Затем укажи трудоёмкость с точными числами и единицами, если она приведена. "
        "В конце опиши защиту перед комиссией. Избегай примеров, номеров документов и всех чисел кроме трудоёмкости."
    ),
}


# поля, которых достаточно для описания; сплошной текст разделов тогда не передаётся
STRUCTURED = {
    "rpd": ("discipline", "goal", "semesters", "ze", "hours", "control", "topics", "labs", "practicals", "results"),
    "practice": ("practice_kind", "practice_type", "method", "form", "semesters", "ze", "weeks", "stages", "content", "results", "control"),
}
ATTESTATION_FIELDS = {
    "gia": ("forms", "hours", "ze", "weeks", "vkr_kind", "vkr_requirements", "procedure"),
    "fos_gia": ("criteria", "competencies", "indicators"),
}
REFUSAL = re.compile(
    r"^(?:недостаточно\s+(?:данных|сведений|информации)|"
    r"(?:в\s+документе\s+)?нет\s+достаточных\s+(?:сведений|данных))\b|"
    r"генеративные языковые модели не обладают собственным мнением|"
    r"разговоры на чувствительные темы могут быть ограничены", re.I,
)
GIA_QUANTITY = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(?:"
    r"(?P<hours>(?:академическ\w+\s+)?час(?:а|ов)?\b|ч\b\.?)|"
    r"(?P<weeks>недел\w*\b|нед\b\.?)|"
    r"(?P<ze>зач[её]тн\w+\s+единиц\w*\b|з\.\s*е\b\.?|зет\b)|"
    r"(?P<months>месяц\w*\b)|(?P<years>год(?:а|ов)?\b|лет\b)|(?P<days>дня?\b|дней\b))",
    re.I,
)
GIA_EXAMPLE = re.compile(r"^\s*(?:пример(?:ы|ная|ный|ные)?|образец|шаблон)\b", re.I | re.M)
# Лимиты общего числа абзацев и предложений.
PARAGRAPHS = {"gia": 2, "oop": 1}
SENTENCES = {"oop": 5, "gia": 4}
FORMAT = " Ответ — {p}, не больше {n} предложений, без списков, перечислений через тире и заголовков."


def limit_text(doc_type: str, text: str) -> str:
    """Не больше PARAGRAPHS абзацев и SENTENCES предложений."""
    out = []
    remaining = SENTENCES.get(doc_type, 3)
    for p in [p for p in re.split(r"\n\s*\n", text or "") if p.strip()][:PARAGRAPHS.get(doc_type, 1)]:
        p = re.sub(r"\s*[—–-]\s+(?=[А-ЯЁA-Z])", " ", " ".join(p.split()))          # «: — Основы …; — Угрозы …»
        sentences = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«])", p)
        out.append(" ".join(sentences[:remaining]))
        remaining -= len(sentences)
        if remaining <= 0:
            break
    return "\n\n".join(out)


def too_long(doc_type: str, text: str) -> bool:
    return bool(text) and limit_text(doc_type, text) != text


def gia_characteristic(summary: str | list[str] | None) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", summary) if isinstance(summary, str) else (summary or [])
    return [p.strip() for p in paragraphs if p.strip() and not REFUSAL.search(" ".join(p.split()))][:2]


def _bounded_json(data: dict, limit: int) -> str:
    text = json.dumps(data, ensure_ascii=False)
    while len(text) > limit and data:
        data.pop(next(reversed(data)))
        text = json.dumps(data, ensure_ascii=False)
    return text


def compact(primary: dict, limit: int, doc_type: str = "") -> str:
    """Первичный JSON без служебных полей, обрезанный под окно модели."""
    if doc_type == "oop":
        data = {}
        if isinstance(primary.get("program"), dict):
            data["program"] = {k: v for k, v in primary["program"].items()
                               if k in ("profile", "code", "direction", "level", "form", "year")}
        data.update(program_summary.facts(primary))
        return _bounded_json(data, limit)
    if fields := ATTESTATION_FIELDS.get(doc_type):
        data = {key: primary[key] for key in fields if primary.get(key)}
        if doc_type == "gia":
            for key in ("vkr_requirements", "procedure"):
                if key in data:
                    excerpt = GIA_EXAMPLE.split(data[key], maxsplit=1)[0].strip()
                    if excerpt:
                        data[key] = excerpt
                    else:
                        del data[key]
        return _bounded_json(data, limit)
    data = {k: v for k, v in primary.items() if k not in ("template", "source", "robot")}
    keys = STRUCTURED.get(doc_type)
    if keys and any(primary.get(k) for k in keys):
        data = {k: primary[k] for k in keys if primary.get(k)}
        data.update({k: primary[k] for k in ("program", "document") if k in primary})
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= limit:
        return text
    secs = data.get("sections", {})
    for key in list(secs):
        secs[key] = {"title": secs[key].get("title"), "text": secs[key].get("text", "")[:600]}
    text = json.dumps(data, ensure_ascii=False)
    return text[:limit]


class LlmClient:
    def __init__(self, settings: LlmSettings, transport: httpx.BaseTransport | None = None):
        self.s = settings
        verify: ssl.SSLContext | bool = True
        ca = Path(settings.ca_bundle) if settings.ca_bundle else None
        if settings.provider == "gigachat" and ca is not None:
            ca = ca if ca.is_absolute() else ROOT / ca
            if ca.exists():
                verify = ssl.create_default_context(cafile=certifi.where())
                verify.load_verify_locations(str(ca))
        self._client = httpx.Client(timeout=settings.timeout, transport=transport, verify=verify)
        self._token, self._token_until = "", 0.0

    @property
    def enabled(self) -> bool:
        # GigaChat без ключа авторизации не подключается — краткие описания просто не формируются
        return self.s.provider in PROVIDERS and (self.s.provider != "gigachat" or bool(self.s.api_key))

    def _gigachat_token(self) -> str:
        if self._token and time.time() < self._token_until - 60:
            return self._token
        key = self.s.api_key.strip()
        if ":" in key:   # client_id:client_secret вместо готового ключа авторизации
            key = base64.b64encode(key.encode()).decode()
        r = self._client.post(self.s.auth_url, data={"scope": self.s.scope}, headers={
            "Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()), "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_until = data.get("expires_at", 0) / 1000 or time.time() + 1500
        return self._token

    def describe(self) -> str:
        return f"{self.s.provider}: {self.s.model}" if self.enabled else "не подключён"

    def summarize(self, doc_type: str, primary: dict) -> str:
        if not self.enabled:
            raise LlmError("Модуль LLM не подключён (llm.provider = none или не задан ключ GigaChat)")
        if doc_type not in TASKS:
            raise LlmError(f"Краткое описание для типа {doc_type} не формируется")
        paragraphs = PARAGRAPHS.get(doc_type, 1)
        task = TASKS[doc_type] + FORMAT.format(p="один абзац" if paragraphs == 1 else f"не больше {paragraphs} абзацев",
                                               n=SENTENCES.get(doc_type, 3))
        evidence = "\n\nСведения документа:\n" + compact(primary, self.s.max_input_chars, doc_type)
        reply = self._complete(task + evidence)
        try:
            return self._validate(doc_type, reply, primary)
        except _SummaryQualityError:
            if doc_type not in RETRY_TASKS:
                raise
        reply = self._complete(RETRY_TASKS[doc_type] + evidence)
        return self._validate(doc_type, reply, primary)

    def _complete(self, user: str) -> str | None:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        try:
            if self.s.provider == "gigachat":
                base = self.s.base_url.rstrip("/") if "gigachat" in self.s.base_url else GIGACHAT_URL
                body = {"model": self.s.model, "messages": messages, "temperature": self.s.temperature}
                r = self._client.post(base + "/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {self._gigachat_token()}"})
                if r.status_code == 401:   # токен отозван раньше срока — один раз получаем новый
                    self._token = ""
                    r = self._client.post(base + "/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self._gigachat_token()}"})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
            elif self.s.provider == "ollama":
                r = self._client.post(self.s.base_url.rstrip("/") + "/api/chat", json={
                    "model": self.s.model, "messages": messages, "stream": False,
                    "options": {"temperature": self.s.temperature}})
                r.raise_for_status()
                text = r.json().get("message", {}).get("content", "")
            else:
                headers = {"Authorization": f"Bearer {self.s.api_key}"} if self.s.api_key else {}
                base = self.s.base_url.rstrip("/")
                url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
                r = self._client.post(url, headers=headers, json={
                    "model": self.s.model, "messages": messages, "temperature": self.s.temperature})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            raise LlmError(f"Модуль LLM недоступен или вернул ошибку: {type(e).__name__}: {e}") from e
        if text is not None and not isinstance(text, str):
            raise LlmError(f"Модуль LLM вернул ответ в неожиданном формате: {type(text).__name__}")
        return text

    @staticmethod
    def _validate(doc_type: str, text: str | None, primary: dict) -> str:
        # у рассуждающих моделей ответ может начинаться с блока размышлений
        if "</think>" in (text or ""):
            text = text.split("</think>", 1)[1]
        if PARAGRAPHS.get(doc_type, 1) == 1:
            text = " ".join((text or "").split())
        text = limit_text(doc_type, text)
        if doc_type == "oop" and not program_summary.clean_summary(text):
            raise _SummaryQualityError("Краткая характеристика ОП не прошла проверку качества")
        if doc_type in ATTESTATION_FIELDS and text:
            text = "\n\n".join(gia_characteristic(text))
            if not text:
                raise _SummaryQualityError("Описание ГИА не прошло проверку качества")
        if doc_type == "gia":
            if len(text) > 650:
                raise _SummaryQualityError("Описание ГИА превышает 650 знаков")
            for match in GIA_QUANTITY.finditer(text):
                if float(match[1].replace(",", ".")) != primary.get(match.lastgroup):
                    raise _SummaryQualityError("Описание ГИА содержит неподтверждённую трудоёмкость или продолжительность")
            if re.search(r"\d", GIA_QUANTITY.sub("", text)):
                raise _SummaryQualityError("Описание ГИА содержит числа помимо трудоёмкости и продолжительности")
        if not text:
            raise _SummaryQualityError("Модуль LLM вернул пустой ответ")
        return text
