"""报表路由：汇总查询 + Excel 导出。"""
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ReportSummary
from ..services.reports import aggregate, export_excel

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/summary", response_model=ReportSummary)
def get_summary(
    month: str | None = None,
    invoice_type: str | None = None,
    db: Session = Depends(get_db),
):
    """汇总统计：总额、总笔数、按月/类型/商户分组。"""
    return aggregate(db, month=month, invoice_type=invoice_type)


@router.get("/export")
def export_report(
    month: str | None = None,
    invoice_type: str | None = None,
    db: Session = Depends(get_db),
):
    """导出 Excel 报表。"""
    data = export_excel(db, month=month, invoice_type=invoice_type)
    filename = "发票报表.xlsx"
    if month:
        filename = f"发票报表_{month}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
