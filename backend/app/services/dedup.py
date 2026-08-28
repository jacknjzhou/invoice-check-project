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
    """查重：优先按发票代码+号码，否则按文件哈希。

    返回重复的发票对象，或 None 表示不重复。
    """
    q = db.query(Invoice)
    if exclude_id is not None:
        q = q.filter(Invoice.id != exclude_id)

    # 优先发票代码+号码
    if invoice_code and invoice_number:
        dup = q.filter(
            Invoice.invoice_code == invoice_code,
            Invoice.invoice_number == invoice_number,
        ).first()
        if dup:
            return dup

    # 退化为文件哈希
    if file_hash:
        dup = q.filter(Invoice.file_hash == file_hash).first()
        if dup:
            return dup

    return None
