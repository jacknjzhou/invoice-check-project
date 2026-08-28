"""SQLAlchemy 引擎与会话管理（PostgreSQL）。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_invoice_columns()


def _ensure_invoice_columns():
    """轻量迁移：为已存在的 invoices 表补充新增列（create_all 不会做 ALTER）。"""
    from sqlalchemy import text

    needed = {
        "seller_tax_id": "VARCHAR(32)",
        "buyer_tax_id": "VARCHAR(32)",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'invoices' AND column_name = :col"
                ),
                {"col": col},
            ).first()
            if not exists:
                conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {col} {ddl}"))
