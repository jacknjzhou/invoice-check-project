"""数据库表模型。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1024))
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    file_type: Mapped[str] = mapped_column(String(16), default="")  # image / pdf

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending / processing / done / error / duplicate

    invoice_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_date: Mapped[str | None] = mapped_column(String(16), nullable=True)  # YYYY-MM-DD
    amount_ex_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 商品明细（list[dict]）
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("invoice_code", "invoice_number", name="uq_invoice_no"),
    )

    @property
    def month(self) -> str | None:
        """YYYY-MM，用于按月聚合。"""
        return self.issue_date[:7] if self.issue_date else None


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
