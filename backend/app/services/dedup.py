"""发票查重逻辑。"""
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Invoice


def file_sha256(path: Path) -> str:
    """计算文件 SHA256 哈希。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(
    db: Session,
    invoice_code: str | None,
    invoice_number: str | None,
    file_hash: str,
    exclude_id: int | None = None,
) -> Invoice | None:
    """查重：三级 key 逐级匹配。

    1. 发票代码 + 发票号码（最精确）
    2. 仅发票号码（全电发票无代码，号码 20 位/8 位全局唯一）
    3. 文件 SHA256 哈希（同文件直接重传）

    返回重复的发票对象，或 None 表示不重复。
    """
    q = db.query(Invoice)
    if exclude_id is not None:
        q = q.filter(Invoice.id != exclude_id)

    # 1. 优先发票代码+号码
    if invoice_code and invoice_number:
        dup = q.filter(
            Invoice.invoice_code == invoice_code,
            Invoice.invoice_number == invoice_number,
        ).first()
        if dup:
            return dup

    # 2. 仅发票号码（位数 >= 8 才可信，避免 OCR 误识别的小片段误判；
    #    中国发票号码 8 位/20 位全局唯一）
    if invoice_number and len(invoice_number) >= 8:
        dup = q.filter(Invoice.invoice_number == invoice_number).first()
        if dup:
            return dup

    # 3. 退化为文件哈希
    if file_hash:
        dup = q.filter(Invoice.file_hash == file_hash).first()
        if dup:
            return dup

    return None
