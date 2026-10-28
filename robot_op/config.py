"""Настройки робота: config/robot.toml + переопределение переменными окружения ROBOT_<РАЗДЕЛ>_<ПАРАМЕТР>."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "robot.toml"


@dataclass
class AppSettings:
    data_dir: str = "data"
    database_url: str = "sqlite:///data/robot.db"
    demo: bool = False


@dataclass
class SiteSettings:
    base_url: str = "https://www.istu.edu"
    education_path: str = "/sveden/education/"
    op_list_url_template: str = ""
    https_only: bool = True
    allow_http_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    allowed_hosts: list[str] = field(default_factory=lambda: ["www.istu.edu", "istu.edu"])
    requests_per_second: float = 2.0
    retries: int = 4
    backoff_base: float = 2.0
    timeout: float = 60.0
    user_agent: str = "RobotOP-IRNITU/1.0"
    max_document_mb: int = 80


@dataclass
class OcrSettings:
    tesseract_cmd: str = "tesseract"
    lang: str = "rus+eng"
    dpi: int = 300
    min_text_chars_per_page: int = 80


@dataclass
class LlmSettings:
    provider: str = "none"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    api_key: str = ""
    timeout: float = 120.0
    temperature: float = 0.2
    max_input_chars: int = 12000


@dataclass
class HistorySettings:
    keep_versions: int = 10
    keep_log_runs: int = 20


@dataclass
class Settings:
    app: AppSettings = field(default_factory=AppSettings)
    site: SiteSettings = field(default_factory=SiteSettings)
    ocr: OcrSettings = field(default_factory=OcrSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    history: HistorySettings = field(default_factory=HistorySettings)

    @property
    def data_path(self) -> Path:
        p = Path(self.app.data_dir)
        return p if p.is_absolute() else ROOT / p


def _coerce(value: str, current):
    if isinstance(current, bool):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list):
        value = value.strip()
        if value.startswith("["):
            return json.loads(value)
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def load_settings(path: Path | None = None, env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    path = Path(env.get("ROBOT_CONFIG", path or DEFAULT_CONFIG))
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    settings = Settings()
    for section in fields(settings):
        obj = getattr(settings, section.name)
        if not is_dataclass(obj):
            continue
        values = raw.get(section.name, {})
        for f in fields(obj):
            if f.name in values:
                setattr(obj, f.name, values[f.name])
            key = f"ROBOT_{section.name}_{f.name}".upper()
            if key in env:
                setattr(obj, f.name, _coerce(env[key], getattr(obj, f.name)))
    # Короткие синонимы для docker-compose
    if "DATABASE_URL" in env and "ROBOT_APP_DATABASE_URL" not in env:
        settings.app.database_url = env["DATABASE_URL"]
    settings.history.keep_versions = max(5, settings.history.keep_versions)
    settings.history.keep_log_runs = max(10, settings.history.keep_log_runs)
    return settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()
