"""Хранилище данных робота (PostgreSQL / SQLite): ОП, документы, версии JSON-файлов, запуски, журнал."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, event)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

JsonType = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Program(Base):
    """Образовательная программа. Сведения берутся со страницы сайта, а не из документов (ТЗ, раздел 3)."""
    __tablename__ = "programs"
    __table_args__ = (UniqueConstraint("level", "year", "code", "profile", "form", name="uq_program"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    level: Mapped[str] = mapped_column(String(32))           # bachelor, specialist, master, spo, postgraduate
    level_title: Mapped[str] = mapped_column(String(64))     # «Бакалавриат»
    year: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(16), default="")
    direction: Mapped[str] = mapped_column(String(255), default="")
    profile: Mapped[str] = mapped_column(String(400), default="")
    form: Mapped[str] = mapped_column(String(32), default="")
    block_title: Mapped[str] = mapped_column(String(500), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    page: Mapped[dict | None] = mapped_column(JsonType, nullable=True)   # контекст веб-страницы ОП
    page_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    documents: Mapped[list[Document]] = relationship(back_populates="program", cascade="all, delete-orphan",
                                                      order_by="Document.id")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("program_id", "url", name="uq_document_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[str] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    doc_type: Mapped[str] = mapped_column(String(16))        # up, kug, rpd, practice, oop, fos_gia, gia
    title: Mapped[str] = mapped_column(String(1000), default="")
    url: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)   # text | ocr
    status: Mapped[str] = mapped_column(String(32), default="queued")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    program: Mapped[Program] = relationship(back_populates="documents")
    versions: Mapped[list[JsonVersion]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                        order_by="JsonVersion.version")


class JsonVersion(Base):
    """История версий первичных и итоговых JSON-файлов документа."""
    __tablename__ = "json_versions"
    __table_args__ = (UniqueConstraint("document_id", "kind", "version", name="uq_json_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))             # primary | final
    version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    document: Mapped[Document] = relationship(back_populates="versions")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued, running, finished, failed, interrupted
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    stage: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)          # накопленное время работы (с учётом возобновлений)

    items: Mapped[list[RunItem]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                order_by="RunItem.id")


class RunItem(Base):
    """Документ в составе запуска: позволяет продолжить прерванный запуск с места остановки."""
    __tablename__ = "run_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    program_id: Mapped[str] = mapped_column(String(160))
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(1000), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    note: Mapped[str] = mapped_column(Text, default="")
    done: Mapped[bool] = mapped_column(default=False)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped[Run] = relationship(back_populates="items")


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    level: Mapped[str] = mapped_column(String(8))              # INFO, WARNING, ERROR
    program_id: Mapped[str] = mapped_column(String(160), default="")
    document: Mapped[str] = mapped_column(String(1000), default="")
    message: Mapped[str] = mapped_column(Text)


class Database:
    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, future=True, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(conn, _):
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        self.Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
