"""发票管理路由：上传、列表、详情、更新、删除、重新识别。"""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Invoice
from ..schemas import InvoiceOut, InvoicePage, InvoiceUpdate
from ..services.dedup import file_sha256

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


@router.post("", response_model=list[InvoiceOut])
async def upload_invoices(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """批量上传发票文件（图片/PDF），返回创建的发票记录。"""
    created_ids = []
    results = []

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            file_type = "image"
        elif ext == ".pdf":
            file_type = "pdf"
        else:
            raise HTTPException(400, f"不支持的文件类型: {f.filename}")

        # 生成唯一文件名
        new_name = f"{uuid.uuid4().hex}{ext}"
        save_path = UPLOAD_DIR / new_name
        with open(save_path, "wb") as out:
            shutil.copyfileobj(f.file, out)

        file_hash = file_sha256(save_path)

        inv = Invoice(
            file_name=f.filename,
            file_path=str(save_path),
            file_hash=file_hash,
            file_type=file_type,
            status="pending",
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        created_ids.append(inv.id)
        results.append(inv)

    # 后台串行处理
    background_tasks.add_task(_process_batch, created_ids)

    return results


async def _process_batch(ids: list[int]):
    """批量处理入口（避免阻塞主线程）。"""
    from ..services.processing import process_invoice_queue

    await process_invoice_queue(ids)


@router.get("", response_model=InvoicePage)
def list_invoices(
    status: str | None = None,
    invoice_type: str | None = None,
    month: str | None = None,
    seller: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    """分页列表，支持按状态/类型/月份/商户筛选。"""
    q = db.query(Invoice)
    if status:
        q = q.filter(Invoice.status == status)
    if invoice_type:
        q = q.filter(Invoice.invoice_type == invoice_type)
    if month:
        q = q.filter(Invoice.issue_date.like(f"{month}%"))
    if seller:
        q = q.filter(Invoice.seller_name.contains(seller))

    total = q.count()
    items = q.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return InvoicePage(total=total, items=items)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "发票不存在")
    return inv


@router.patch("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(invoice_id: int, update: InvoiceUpdate, db: Session = Depends(get_db)):
    """手动修正提取字段。"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "发票不存在")

    data = update.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(inv, k, v)
    db.commit()
    db.refresh(inv)
    return inv


@router.delete("/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "发票不存在")

    # 删除文件
    try:
        Path(inv.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.delete(inv)
    db.commit()
    return {"ok": True}


@router.get("/{invoice_id}/file")
def get_invoice_file(invoice_id: int, db: Session = Depends(get_db)):
    """返回发票原件文件（图片/PDF）。"""
    from fastapi.responses import FileResponse

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "发票不存在")
    p = Path(inv.file_path)
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    media = "image/png" if p.suffix.lower() == ".png" else "application/pdf"
    return FileResponse(p, media_type=media)


@router.post("/{invoice_id}/reprocess", response_model=InvoiceOut)
async def reprocess_invoice(
    invoice_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """重新识别（更换模型后重跑）。"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "发票不存在")

    inv.status = "pending"
    inv.error_msg = None
    db.commit()

    background_tasks.add_task(_process_batch, [invoice_id])
    return inv
