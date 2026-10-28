"""Модуль на основе LLM: краткие описания по первичным JSON-файлам (ТЗ, шаг 10).

В LLM передаются только первичные JSON-файлы с общедоступными сведениями (ТЗ, раздел 7).
Поддерживаются локальная LLM через Ollama и любой OpenAI-совместимый API.
"""
from __future__ import annotations

import json

import httpx

from ..config import LlmSettings


class LlmError(Exception):
    pass


SYSTEM = (
    "Ты помогаешь абитуриентам и студентам ИРНИТУ разобраться в документах образовательных программ. "
    "Пиши по-русски, простым языком, без канцелярита и без списков. Используй только сведения из переданного JSON, "
    "ничего не придумывай; если сведений мало — напиши короче. Не упоминай JSON, поля и сам документ."
)

TASKS = {
    "oop": "Составь общую характеристику образовательной программы (3–5 предложений): кого и для какой деятельности "
           "готовит программа, что составляет основу обучения, где проходит практическая подготовка.",
    "rpd": "Опиши дисциплину в 2–3 предложениях: цель изучения и основное содержание (ключевые темы).",
    "practice": "Опиши практику в 2–3 предложениях: вид и тип практики, что студент делает на ней по этапам, чем она завершается.",
    "gia": "Опиши в 2–4 предложениях требования к государственной итоговой аттестации: формы аттестации, объём, "
           "что требуется от выпускной квалификационной работы и как проходит защита.",
    "fos_gia": "Опиши в 2–3 предложениях, как оценивается государственная итоговая аттестация: какие компетенции "
               "проверяются, по каким показателям и критериям выставляется оценка.",
}


def compact(primary: dict, limit: int) -> str:
    """Первичный JSON без служебных полей, обрезанный под окно модели."""
    data = {k: v for k, v in primary.items() if k not in ("template", "source", "robot")}
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
        self._client = httpx.Client(timeout=settings.timeout, transport=transport)

    @property
    def enabled(self) -> bool:
        return self.s.provider in ("ollama", "openai")

    def describe(self) -> str:
        return f"{self.s.provider}: {self.s.model}" if self.enabled else "не подключён"

    def summarize(self, doc_type: str, primary: dict) -> str:
        if not self.enabled:
            raise LlmError("Модуль LLM не подключён (llm.provider = none)")
        if doc_type not in TASKS:
            raise LlmError(f"Краткое описание для типа {doc_type} не формируется")
        user = TASKS[doc_type] + "\n\nСведения документа:\n" + compact(primary, self.s.max_input_chars)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        try:
            if self.s.provider == "ollama":
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
        text = " ".join((text or "").split())
        # у рассуждающих моделей ответ может начинаться с блока размышлений
        if "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        if not text:
            raise LlmError("Модуль LLM вернул пустой ответ")
        return text
