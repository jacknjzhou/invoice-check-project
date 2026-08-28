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
    seller_tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 销售方纳税人识别号
    buyer_tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 购买方纳税人识别号
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


class ExtractionRun(Base):
    """提取历史：每次识别的模型/原始输出/字段快照。

    用途：
    - 更换模型后重跑（reprocess）不覆盖历史，可对比不同模型表现
    - 审计追溯：字段来源与模型原始输出可查
    """
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="")  # ocr_llm / vision / ocr_llm+vision
    model: Mapped[str] = mapped_column(String(128), default="")
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    issues: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 自检问题列表
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
