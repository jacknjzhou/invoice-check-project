"""对话问答服务：从问题中提取筛选条件 → 聚合发票 → 交给 LLM 回答。"""
import re

from sqlalchemy.orm import Session

from ..llm.client import chat_completion
from ..models import Invoice


def _parse_question(question: str) -> dict:
    """从自然语言问题中提取筛选条件（简单规则）。"""
    filters = {}

    # 月份：如 "这个月" / "上个月" / "2026年8月" / "8月"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", question)
    if m:
        filters["month"] = f"{m.group(1)}-{int(m.group(2)):02d}"
    else:
        m = re.search(r"(\d{1,2})\s*月", question)
        if m:
            from datetime import datetime
            year = datetime.now().year
            filters["month"] = f"{year}-{int(m.group(1)):02d}"

    # 类型关键词
    type_map = {
        "交通": ["火车票", "航空行程单", "出租车票", "网约车行程单", "出租车发票"],
        "差旅": ["火车票", "航空行程单", "出租车票", "网约车行程单", "出租车发票", "酒店水单"],
        "餐饮": ["餐饮发票"],
        "住宿": ["酒店水单"],
        "办公": ["增值税电子普通发票", "增值税专用发票"],
    }
    for keyword, types in type_map.items():
        if keyword in question:
            filters["invoice_types"] = types
            break

    return filters


def _build_context(db: Session, filters: dict) -> tuple[list[dict], str]:
    """根据筛选条件查询发票，构建上下文。"""
    q = db.query(Invoice).filter(Invoice.status == "done")

    if "month" in filters:
        q = q.filter(Invoice.issue_date.like(f"{filters['month']}%"))
    if "invoice_types" in filters:
        q = q.filter(Invoice.invoice_type.in_(filters["invoice_types"]))

    invoices = q.order_by(Invoice.issue_date.desc()).limit(50).all()

    if not invoices:
        return [], "没有找到匹配的发票记录。"

    # 聚合
    total = sum(i.amount_total or 0 for i in invoices)
    count = len(invoices)

    # 明细
    details = []
    for i in invoices[:20]:
        details.append({
            "日期": i.issue_date,
            "类型": i.invoice_type,
            "金额": i.amount_total,
            "商户": i.seller_name,
        })

    note_parts = [f"共 {count} 笔，合计 {round(total, 2)} 元。"]
    if "month" in filters:
        note_parts.append(f"月份: {filters['month']}")
    if "invoice_types" in filters:
        note_parts.append(f"类型: {', '.join(filters['invoice_types'])}")

    return details, "；".join(note_parts)


async def answer_question(db: Session, question: str, history: list[dict] | None = None) -> tuple[str, str]:
    """回答用户问题，返回 (answer, context_note)。"""
    filters = _parse_question(question)
    details, note = _build_context(db, filters)

    # 构建 prompt
    system = "你是一个发票报销助手，根据用户提供的发票数据回答问题。请用中文简洁回答，并附上计算口径。"
    user_msg = f"用户问题：{question}\n\n数据上下文：{note}\n"
    if details:
        user_msg += f"\n相关发票明细（最多 20 条）：\n{details}"

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-6:])  # 最近 3 轮
    messages.append({"role": "user", "content": user_msg})

    answer = await chat_completion(messages, temperature=0.3)
    return answer, note
