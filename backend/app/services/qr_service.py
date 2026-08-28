"""发票二维码解析：从发票左上角 QR 码确定性提取关键字段。

中国增值税发票 QR 码内容为逗号分隔字符串：
    01,10,144032409110,02203307,1030.80,20240114,58935074,...
字段顺序：版本, 发票类型, 发票代码, 发票号码, 价税合计, 开票日期(YYYYMMDD), 校验码...

QR 码是票据上最可靠的机器可读数据源，用于：
1. 交叉校验/修正 LLM 提取结果（锚点）
2. 作为查重的最高优先级 key
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_qr_engines: dict | None = None


def _load_engines() -> dict:
    """惰性加载二维码解码引擎（pyzbar 优先，cv2 兜底）。"""
    global _qr_engines
    if _qr_engines is not None:
        return _qr_engines

    engines = {"pyzbar": None, "cv2": None}
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        engines["pyzbar"] = pyzbar_decode
    except ImportError:
        logger.info("pyzbar 未安装，QR 解码将回退到 cv2")
    try:
        import cv2
        engines["cv2"] = cv2
    except ImportError:
        pass

    _qr_engines = engines
    return engines


def _decode_image(img) -> list[str]:
    """对单张 PIL 图片解码 QR，返回原始字符串列表。"""
    engines = _load_engines()
    texts: list[str] = []

    if engines["pyzbar"]:
        try:
            from pyzbar.pyzbar import ZBarSymbol
            results = engines["pyzbar"](img, symbols=[ZBarSymbol.QRCODE])
            texts.extend(r.data.decode("utf-8", errors="replace") for r in results)
        except Exception as e:
            logger.debug("pyzbar 解码失败: %s", e)

    if not texts and engines["cv2"]:
        try:
            import numpy as np
            detector = engines["cv2"].QRCodeDetector()
            ret, infos, _points, _ = detector.detectAndDecodeMulti(
                np.array(img.convert("RGB"))
            )
            if ret:
                texts.extend(s for s in infos if s)
        except Exception as e:
            logger.debug("cv2 解码失败: %s", e)

    return texts


def decode_invoice_qr(img_path: Path) -> Optional[Dict[str, Any]]:
    """解析发票图片中的 QR 码。

    尝试原图及 90/180/270 度旋转（拍照方向不正确时 QR 仍在）。

    Returns:
        {"invoice_code", "invoice_number", "amount_total", "issue_date", "raw"}
        解析失败返回 None。
    """
    try:
        from PIL import Image
        img = Image.open(img_path)
    except Exception as e:
        logger.warning("QR 解码无法打开图片 %s: %s", img_path, e)
        return None

    rotations = [img]
    try:
        rotations = [
            img,
            img.transpose(Image.ROTATE_90),
            img.transpose(Image.ROTATE_180),
            img.transpose(Image.ROTATE_270),
        ]
    except Exception:
        pass

    for rotated in rotations:
        for text in _decode_image(rotated):
            fields = parse_qr_text(text)
            if fields:
                logger.info("QR 解码成功: %s", fields)
                return fields
    return None


def parse_qr_text(text: str) -> Optional[Dict[str, Any]]:
    """解析 QR 码原始文本为发票字段。

    标准格式: 01,<类型>,<代码>,<号码>,<金额>,<YYYYMMDD>[,<校验码>...]
    全电发票可能无代码字段（代码位为空）。
    """
    if not text or "," not in text:
        return None

    parts = [p.strip() for p in text.split(",")]
    # 版本号固定为 01
    if parts[0] != "01" or len(parts) < 6:
        return None

    result: Dict[str, Any] = {
        "invoice_code": None,
        "invoice_number": None,
        "amount_total": None,
        "issue_date": None,
        "raw": text,
    }

    # 发票类型（parts[1]，2 位数字），仅供判断，不直接入库
    type_code = parts[1]

    # 位置解析：代码(10/12位) → 号码(8/20位) → 金额 → 日期
    idx = 2
    code = parts[idx] if idx < len(parts) else ""
    if code.isdigit() and len(code) in (10, 12):
        result["invoice_code"] = code
        idx += 1
    elif code == "":
        idx += 1  # 全电发票代码位为空

    number = parts[idx] if idx < len(parts) else ""
    if number.isdigit() and len(number) in (8, 20):
        result["invoice_number"] = number
        idx += 1
    else:
        return None

    amount = parts[idx] if idx < len(parts) else ""
    try:
        result["amount_total"] = round(float(amount), 2)
        idx += 1
    except (ValueError, TypeError):
        pass

    date = parts[idx] if idx < len(parts) else ""
    if len(date) == 8 and date.isdigit():
        result["issue_date"] = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    # 至少要有号码才算解析成功
    return result if result["invoice_number"] else None
