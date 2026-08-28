"""Pydantic 请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, Field


class InvoiceOut(BaseModel):
    id: int
    file_name: str
    file_hash: str
    file_type: str
    status: str
    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    amount_ex_tax: float | None = None
    tax_amount: float | None = None
    amount_total: float | None = None
    seller_name: str | None = None
    buyer_name: str | None = None
    seller_tax_id: str | None = None
    buyer_tax_id: str | None = None
    items: list | None = None
    remark: str | None = None
    duplicate_of_id: int | None = None
    error_msg: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class InvoicePage(BaseModel):
    total: int
    items: list[InvoiceOut]


class InvoiceUpdate(BaseModel):
    """手动修正提取字段，仅接受非 None 值。"""
    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    amount_ex_tax: float | None = None
    tax_amount: float | None = None
    amount_total: float | None = None
    seller_name: str | None = None
    buyer_name: str | None = None
    seller_tax_id: str | None = None
    buyer_tax_id: str | None = None
    remark: str | None = None


class ChatMessage(BaseModel):
    role: str = "user"  # user / assistant
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    question: str = ""


class ChatResponse(BaseModel):
    answer: str
    context_note: str | None = None


class SettingOut(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    extract_model: str = "qwen2.5vl:7b"
    chat_model: str = "qwen2.5vl:7b"


class SettingTestResponse(BaseModel):
    ok: bool
    message: str = ""


class ReportSummary(BaseModel):
    total_amount: float
    total_count: int
    by_month: list[dict]
    by_type: list[dict]
    by_seller: list[dict]
