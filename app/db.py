"""SQLite 数据库：引擎、会话与建表。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATA_DIR, DATABASE_URL, UPLOAD_DIR


class Base(DeclarativeBase):
    pass


DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（幂等）。"""
    from app import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """轻量迁移：为已存在的旧表补充新增列（SQLite 不支持 ALTER 多列，逐列判断）。"""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(submissions)")}
        additions = {
            "status": "VARCHAR(16) DEFAULT 'approved' NOT NULL",
            "reviewed_by": "INTEGER",
            "reviewed_at": "DATETIME",
            "review_note": "VARCHAR(512)",
            "withdrawn_by": "INTEGER",
            "withdrawn_at": "DATETIME",
            "withdraw_reason": "VARCHAR(512)",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.exec_driver_sql(f"ALTER TABLE submissions ADD COLUMN {name} {ddl}")

        # contests.version
        ccols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(contests)")}
        if "version" not in ccols:
            conn.exec_driver_sql("ALTER TABLE contests ADD COLUMN version VARCHAR(16)")
        if "seal_reveal_time" not in ccols:
            conn.exec_driver_sql("ALTER TABLE contests ADD COLUMN seal_reveal_time DATETIME")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
