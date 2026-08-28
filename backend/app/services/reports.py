"""报表聚合与 Excel 导出。"""
import io
from datetime import datetime

from openpyxl import Workbook
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Invoice


def aggregate(db: Session, month: str | None = None, invoice_type: str | None = None):
    """汇总统计：总额、总笔数、按月/类型/商户分组。"""
    q = db.query(Invoice).filter(Invoice.status == "done")
    if month:
        q = q.filter(Invoice.issue_date.like(f"{month}%"))
    if invoice_type:
        q = q.filter(Invoice.invoice_type == invoice_type)

    invoices = q.all()
    total_amount = sum(i.amount_total or 0 for i in invoices)
    total_count = len(invoices)

    # 按月
    by_month: dict[str, float] = {}
    for i in invoices:
        m = i.month or "未知"
        by_month[m] = by_month.get(m, 0) + (i.amount_total or 0)
    by_month_list = [{"month": k, "amount": round(v, 2), "count": 0} for k, v in sorted(by_month.items())]
    # 补 count
    for item in by_month_list:
        item["count"] = sum(1 for i in invoices if (i.month or "未知") == item["month"])

    # 按类型
    by_type: dict[str, float] = {}
    for i in invoices:
        t = i.invoice_type or "未知"
        by_type[t] = by_type.get(t, 0) + (i.amount_total or 0)
    by_type_list = [{"type": k, "amount": round(v, 2)} for k, v in sorted(by_type.items(), key=lambda x: -x[1])]

    # 按商户
    by_seller: dict[str, float] = {}
    for i in invoices:
        s = i.seller_name or "未知"
        by_seller[s] = by_seller.get(s, 0) + (i.amount_total or 0)
    by_seller_list = [{"seller": k, "amount": round(v, 2)} for k, v in sorted(by_seller.items(), key=lambda x: -x[1])][:20]

    return {
        "total_amount": round(total_amount, 2),
        "total_count": total_count,
        "by_month": by_month_list,
        "by_type": by_type_list,
        "by_seller": by_seller_list,
    }


def export_excel(db: Session, month: str | None = None, invoice_type: str | None = None) -> bytes:
    """导出 Excel（汇总 + 明细两个 sheet）。"""
    q = db.query(Invoice).filter(Invoice.status == "done")
    if month:
        q = q.filter(Invoice.issue_date.like(f"{month}%"))
    if invoice_type:
        q = q.filter(Invoice.invoice_type == invoice_type)
    invoices = q.order_by(Invoice.issue_date.desc()).all()

    wb = Workbook()

    # 汇总 sheet
    ws_summary = wb.active
    ws_summary.title = "汇总"
    ws_summary.append(["发票汇总报表"])
    ws_summary.append([f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
    if month:
        ws_summary.append([f"筛选月份: {month}"])
    if invoice_type:
        ws_summary.append([f"筛选类型: {invoice_type}"])
    ws_summary.append([])

    total = sum(i.amount_total or 0 for i in invoices)
    ws_summary.append(["总金额", round(total, 2)])
    ws_summary.append(["总笔数", len(invoices)])
    ws_summary.append([])

    # 按月汇总
    ws_summary.append(["月份", "金额", "笔数"])
    by_month: dict[str, list] = {}
    for i in invoices:
        m = i.month or "未知"
        if m not in by_month:
            by_month[m] = {"amount": 0, "count": 0}
        by_month[m]["amount"] += i.amount_total or 0
        by_month[m]["count"] += 1
    for m in sorted(by_month):
        ws_summary.append([m, round(by_month[m]["amount"], 2), by_month[m]["count"]])

    # 明细 sheet
    ws_detail = wb.create_sheet("明细")
    headers = [
        "序号", "文件名", "类型", "发票代码", "发票号码", "开票日期",
        "不含税金额", "税额", "价税合计", "销售方", "购买方", "备注",
    ]
    ws_detail.append(headers)
    for idx, i in enumerate(invoices, 1):
        ws_detail.append([
            idx,
            i.file_name,
            i.invoice_type or "",
            i.invoice_code or "",
            i.invoice_number or "",
            i.issue_date or "",
            i.amount_ex_tax or 0,
            i.tax_amount or 0,
            i.amount_total or 0,
            i.seller_name or "",
            i.buyer_name or "",
            i.remark or "",
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
