"""发票处理管线：PDF 转图 → 视觉提取 → QR 校正 → 查重 → 入库。"""
import asyncio
import logging
from pathlib import Path
from PIL import Image
from pdf2image import convert_from_path
from sqlalchemy.exc import IntegrityError

from ..database import SessionLocal
from ..llm.extractor import extract_invoice, to_b64
from ..llm.extractor_v2 import extract_with_ocr
from ..services.ocr_service import check_paddleocr
from ..services.qr_service import decode_invoice_qr
from ..models import Invoice, ExtractionRun
from .dedup import check_duplicate

logger = logging.getLogger(__name__)


def rotate_image(img: Image.Image) -> Image.Image:
    """只做基本的旋转校正。"""
    # 自动旋转校正（根据 EXIF 信息）
    try:
        from PIL import ExifTags
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    if value == 3:
                        img = img.rotate(180, expand=True)
                    elif value == 6:
                        img = img.rotate(270, expand=True)
                    elif value == 8:
                        img = img.rotate(90, expand=True)
                    break
    except (AttributeError, KeyError):
        pass  # 无 EXIF 信息，跳过
    
    return img


def pdf_to_images(pdf_path: Path, output_dir: Path) -> list[Path]:
    """将 PDF 每页转为 PNG 图片，返回图片路径列表。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    # 提高 DPI 到 300，提升小字识别率
    images = convert_from_path(pdf_path, dpi=300)
    paths = []
    for i, img in enumerate(images):
        # 只做旋转校正
        img = rotate_image(img)
        p = output_dir / f"page_{i+1}.png"
        img.save(p, "PNG", quality=95)
        paths.append(p)
    return paths


def image_to_b64(img_path: Path) -> tuple[str, str]:
    """读取图片文件，返回 (base64, mime_type)。
    
    优先直接读取原始字节（不经过 PIL 重编码），避免质量损失。
    只在需要旋转校正时才走 PIL 路径。
    """
    import io
    
    # 先检查是否需要旋转校正
    needs_rotation = False
    try:
        from PIL import ExifTags
        with Image.open(img_path) as img:
            exif = img._getexif()
            if exif:
                for tag, value in exif.items():
                    if ExifTags.TAGS.get(tag) == 'Orientation' and value in (3, 6, 8):
                        needs_rotation = True
                        break
    except Exception:
        pass
    
    if needs_rotation:
        # 需要旋转：走 PIL 路径
        img = Image.open(img_path)
        img = rotate_image(img)
        # 处理 RGBA → RGB（JPEG 不支持透明通道）
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return to_b64(buffer.getvalue()), 'image/png'
    else:
        # 不需要旋转：直接读取原始字节，保持最高质量
        data = img_path.read_bytes()
        suffix = img_path.suffix.lower()
        if suffix == '.png':
            mime = 'image/png'
        elif suffix in ('.jpg', '.jpeg'):
            mime = 'image/jpeg'
        else:
            mime = 'image/png'  # 默认
        return to_b64(data), mime


def validate_and_fix_fields(fields: dict) -> dict:
    """校验并修复提取的字段。采用宽松策略，尽量保留模型输出。"""
    import re
    from datetime import datetime
    
    # 1. 日期格式校验 - 尝试标准化，失败则保留原值
    if fields.get("issue_date"):
        date_str = str(fields["issue_date"]).strip()
        # 尝试多种日期格式
        for fmt in ["%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y年%m月%d日"]:
            try:
                dt = datetime.strptime(date_str, fmt)
                fields["issue_date"] = dt.strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        # 如果所有格式都失败，保留原始值（不置空）
    
    # 2. 发票代码/号码格式校验 - 提取数字，放宽位数限制
    if fields.get("invoice_code"):
        original_code = str(fields["invoice_code"])
        # 移除所有非数字字符（空格、连字符、点等）
        code = re.sub(r"[^\d]", "", original_code)
        # 标准位数：10位、12位、20位
        if len(code) in (10, 12, 20):
            fields["invoice_code"] = code
            if code != original_code:
                logger.info("发票代码清洗: '%s' -> '%s'", original_code, code)
        elif len(code) >= 8:
            # 放宽到 8-20 位，保留提取的数字
            fields["invoice_code"] = code
            logger.warning("发票代码位数非标准(%d位): '%s' -> '%s'", len(code), original_code, code)
        else:
            # 位数太少，可能识别错误，保留原值
            logger.warning("发票代码位数过少(%d位)，保留原值: '%s'", len(code), original_code)
    
    if fields.get("invoice_number"):
        original_number = str(fields["invoice_number"])
        # 移除所有非数字字符
        number = re.sub(r"[^\d]", "", original_number)
        # 标准位数：8位、20位
        if len(number) in (8, 20):
            fields["invoice_number"] = number
            if number != original_number:
                logger.info("发票号码清洗: '%s' -> '%s'", original_number, number)
        elif len(number) >= 6:
            # 放宽到 6-20 位，保留提取的数字
            fields["invoice_number"] = number
            logger.warning("发票号码位数非标准(%d位): '%s' -> '%s'", len(number), original_number, number)
        else:
            # 位数太少，可能识别错误，保留原值
            logger.warning("发票号码位数过少(%d位)，保留原值: '%s'", len(number), original_number)
    
    # 3. 金额字段清洗（去除逗号、空格等）
    for key in ["amount_ex_tax", "tax_amount", "amount_total"]:
        if fields.get(key) is not None:
            val = str(fields[key]).replace(",", "").replace(" ", "").replace("¥", "").replace("元", "")
            try:
                fields[key] = float(val)
            except (ValueError, TypeError):
                # 转换失败时保留原值
                pass

    # 3.5 纳税人识别号清洗：去空格/分隔符，统一大写
    for key in ("seller_tax_id", "buyer_tax_id"):
        if fields.get(key):
            cleaned = re.sub(r"[\s\-—－]", "", str(fields[key])).upper()
            if cleaned != fields[key]:
                logger.info("纳税人识别号清洗 %s: '%s' -> '%s'", key, fields[key], cleaned)
            fields[key] = cleaned
    
    # 4. 金额勾稽校验：仅记录警告，不修改原值
    ex_tax = fields.get("amount_ex_tax")
    tax = fields.get("tax_amount")
    total = fields.get("amount_total")
    
    if ex_tax is not None and tax is not None and total is not None:
        calculated = ex_tax + tax
        diff = abs(calculated - total)
        # 允许 ±0.5 误差
        if diff > 0.5:
            logger.warning(
                "金额勾稽不一致: %.2f + %.2f = %.2f ≠ %.2f (差 %.2f)",
                ex_tax, tax, calculated, total, diff
            )
    
    # 5. 如果只有不含税金额和税额，没有价税合计，自动计算
    if total is None and ex_tax is not None and tax is not None:
        fields["amount_total"] = round(ex_tax + tax, 2)
    
    # 6. 如果只有价税合计和税额，反推不含税金额
    if ex_tax is None and total is not None and tax is not None:
        fields["amount_ex_tax"] = round(total - tax, 2)
    
    return fields


def merge_extracted_fields(results: list[dict]) -> dict:
    """合并多页提取结果（智能合并）。"""
    if not results:
        return {}
    merged = {}
    keys = [
        "invoice_type", "invoice_code", "invoice_number", "issue_date",
        "amount_ex_tax", "tax_amount", "amount_total",
        "seller_name", "buyer_name", "seller_tax_id", "buyer_tax_id",
        "items", "remark",
    ]
    
    for k in keys:
        if k == "items":
            # 明细项跨页合并
            all_items = []
            for r in results:
                items = r.get("items", [])
                if items and isinstance(items, list):
                    all_items.extend(items)
            merged[k] = all_items if all_items else None
        elif k == "amount_total":
            # 金额取最后一页非空值
            for r in reversed(results):
                v = r.get(k)
                if v is not None:
                    merged[k] = v
                    break
            else:
                merged[k] = None
        else:
            # 基础信息取第一页非空值
            for r in results:
                v = r.get(k)
                if v is not None and v != [] and v != "":
                    merged[k] = v
                    break
            else:
                merged[k] = None
    
    return merged


def apply_qr_anchor(fields: dict, qr: dict | None) -> dict:
    """用 QR 码解码结果作为锚点，交叉校验/修正提取字段。

    QR 码是票据上的机器可读数据源，优先级高于 OCR/LLM 提取：
    - 提取字段为空 → 用 QR 补齐
    - 提取字段与 QR 冲突 → 以 QR 为准（记录警告日志）
    """
    if not qr:
        return fields

    key_map = {
        "invoice_code": "invoice_code",
        "invoice_number": "invoice_number",
        "issue_date": "issue_date",
        "amount_total": "amount_total",
    }
    for field, qr_key in key_map.items():
        qr_val = qr.get(qr_key)
        if qr_val is None:
            continue
        cur_val = fields.get(field)
        if cur_val in (None, "", []):
            fields[field] = qr_val
            logger.info("QR 锚点补齐 %s: %s", field, qr_val)
        elif str(cur_val) != str(qr_val):
            logger.warning(
                "QR 锚点修正 %s: 提取值 '%s' -> QR 值 '%s'",
                field, cur_val, qr_val,
            )
            fields[field] = qr_val
    return fields


def check_fields_sanity(fields: dict) -> list[str]:
    """对提取结果做完整性/一致性自检，返回问题列表（空列表表示通过）。

    检查项：关键系数完整性、格式合法性、金额勾稽一致性。
    """
    issues: list[str] = []

    number = fields.get("invoice_number")
    if not number:
        issues.append("缺少发票号码")
    elif not str(number).isdigit():
        issues.append(f"发票号码含非数字字符: {number}")

    code = fields.get("invoice_code")
    if not code and (not number or len(str(number)) != 20):
        # 无代码时仅全电发票（20 位号码）可接受
        issues.append("缺少发票代码（且号码非全电 20 位格式）")

    date = fields.get("issue_date")
    if not date:
        issues.append("缺少开票日期")
    else:
        try:
            from datetime import datetime
            datetime.strptime(str(date), "%Y-%m-%d")
        except ValueError:
            issues.append(f"开票日期格式异常: {date}")

    total = fields.get("amount_total")
    if total is None:
        issues.append("缺少价税合计")
    elif not isinstance(total, (int, float)) or total <= 0:
        issues.append(f"价税合计异常: {total}")

    ex_tax, tax = fields.get("amount_ex_tax"), fields.get("tax_amount")
    if None not in (ex_tax, tax, total) and isinstance(total, (int, float)):
        if abs(float(ex_tax) + float(tax) - float(total)) > 0.5:
            issues.append(f"金额勾稽不一致: {ex_tax} + {tax} ≠ {total}")

    return issues


async def extract_invoice_fields(
    file_path: Path,
    *,
    use_ocr: bool | None = None,
    model: str | None = None,
) -> dict:
    """对单个发票文件执行完整提取管线（不含查重/入库）。

    流程：PDF 转图 → 逐页提取（OCR+LLM 优先）→ 合并 → QR 锚点校正
    → 字段清洗 → 完整性自检 → 自检失败时用视觉模型二次提取交叉校验。

    Args:
        file_path: 发票文件（图片或 PDF）
        use_ocr: 是否优先使用 OCR 模式，None 时自动检测
        model: 指定提取模型，None 时使用配置中的 extract_model

    Returns:
        {"fields": dict, "issues": list[str], "raw_outputs": list[str],
         "mode": str, "model": str}
    """
    if use_ocr is None:
        use_ocr = check_paddleocr()

    if file_path.suffix.lower() == ".pdf":
        img_dir = file_path.parent / f"{file_path.stem}_pages"
        img_paths = pdf_to_images(file_path, img_dir)
    else:
        img_paths = [file_path]

    from ..llm.client import get_llm_config
    cfg = get_llm_config()
    model = model or cfg.get("extract_model") or cfg.get("chat_model")

    async def run_page(img_path: Path, ocr: bool) -> tuple[dict, str]:
        """提取单页，OCR 失败自动回退视觉模式。返回 (fields, raw)。"""
        if ocr:
            try:
                return await extract_with_ocr(img_path, cfg=cfg, model=model, return_raw=True)
            except Exception as e:
                logger.warning("OCR 模式失败，回退到视觉模型: %s", e)
        b64, mime = image_to_b64(img_path)
        return await extract_invoice(b64, mime, cfg=cfg, model=model, return_raw=True)

    async def merge_all(pages: list[Path]) -> tuple[dict, list]:
        """逐页提取 → 合并 → QR 校正 → 清洗校验。返回 (merged, raws)。"""
        results, raws = [], []
        for img_path in pages:
            try:
                extracted, raw = await run_page(img_path, use_ocr)
                results.append(extracted)
                raws.append(raw)
                logger.info("文件 %s 提取结果: %s", img_path.name, extracted)
            except Exception as e:
                logger.warning("提取失败 %s: %s", img_path, e)
                results.append({})
                raws.append("")

        merged = merge_extracted_fields(results)

        # QR 码锚点校验/修正（机器可读数据源，优先级高于 OCR/LLM 提取）
        try:
            qr = decode_invoice_qr(img_paths[0])
        except Exception as qr_err:
            logger.warning("QR 解码异常: %s", qr_err)
            qr = None
        if qr:
            merged = apply_qr_anchor(merged, qr)

        merged = validate_and_fix_fields(merged)
        return merged, raws

    logger.info("开始提取 %s，模式: %s", file_path.name, "OCR+LLM" if use_ocr else "纯视觉")
    merged, raws = await merge_all(img_paths)

    # 完整性自检；失败时用另一通道（视觉模型）二次提取交叉校验
    issues = check_fields_sanity(merged)
    if issues and use_ocr:
        logger.warning("自检发现问题 %s，启动视觉模型二次提取交叉校验", issues)
        second_results, second_raws = [], []
        for img_path in img_paths:
            try:
                extracted, raw = await run_page(img_path, ocr=False)
                second_results.append(extracted)
                second_raws.append(raw)
            except Exception as e:
                logger.warning("第二通道提取失败 %s: %s", img_path, e)
                second_results.append({})
                second_raws.append("")

        second_merged = merge_extracted_fields(second_results)
        second_merged = validate_and_fix_fields(second_merged)
        second_issues = check_fields_sanity(second_merged)

        if len(second_issues) < len(issues):
            logger.info(
                "采用第二通道结果（问题数 %d -> %d）", len(issues), len(second_issues)
            )
            # 以第二通道为主，第一通道的非空字段补齐缺失项
            for k, v in merged.items():
                if second_merged.get(k) in (None, "", []) and v not in (None, "", []):
                    second_merged[k] = v
            merged = second_merged
            issues = second_issues
            raws.extend(second_raws)
            mode = "ocr_llm+vision"
        else:
            mode = "ocr_llm"
    else:
        mode = "ocr_llm" if use_ocr else "vision"

    return {
        "fields": merged,
        "issues": issues,
        "raw_outputs": raws,
        "mode": mode,
        "model": model,
    }


async def process_invoice(invoice_id: int) -> None:
    """处理单张发票：更新状态 → 提取 → 查重 → 入库。"""
    db = SessionLocal()
    try:
        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not inv:
            logger.error("Invoice %d not found", invoice_id)
            return

        inv.status = "processing"
        db.commit()

        file_path = Path(inv.file_path)
        if not file_path.exists():
            inv.status = "error"
            inv.error_msg = f"文件不存在: {file_path}"
            db.commit()
            return

        # 完整提取管线（含 QR 校正、自检、交叉校验）
        out = await extract_invoice_fields(file_path)
        merged = out["fields"]
        if out["issues"]:
            logger.warning("发票 %d 自检遗留问题: %s", invoice_id, out["issues"])
        logger.info("校验后的最终结果: %s", merged)

        # 记录提取历史（换模型对比 / 审计追溯，reprocess 不覆盖）
        db.add(ExtractionRun(
            invoice_id=inv.id,
            mode=out["mode"],
            model=out["model"],
            raw_output="\n---\n".join(out["raw_outputs"])[:10000],
            fields=merged,
            issues=out["issues"],
        ))

        # 查重
        dup = check_duplicate(
            db,
            merged.get("invoice_code"),
            merged.get("invoice_number"),
            inv.file_hash,
            exclude_id=inv.id,
        )
        if dup:
            inv.status = "duplicate"
            inv.duplicate_of_id = dup.id
            inv.error_msg = f"与发票 #{dup.id} 重复"
        else:
            inv.status = "done"
            inv.invoice_type = merged.get("invoice_type")
            inv.invoice_code = merged.get("invoice_code")
            inv.invoice_number = merged.get("invoice_number")
            inv.issue_date = merged.get("issue_date")
            inv.amount_ex_tax = merged.get("amount_ex_tax")
            inv.tax_amount = merged.get("tax_amount")
            inv.amount_total = merged.get("amount_total")
            inv.seller_name = merged.get("seller_name")
            inv.buyer_name = merged.get("buyer_name")
            inv.seller_tax_id = merged.get("seller_tax_id")
            inv.buyer_tax_id = merged.get("buyer_tax_id")
            inv.items = merged.get("items")
            inv.remark = merged.get("remark")

        try:
            db.commit()
        except IntegrityError:
            # 并发上传相同发票时触发 (invoice_code, invoice_number) 唯一约束
            db.rollback()
            logger.warning("发票 %d 触发唯一约束，标记为重复", invoice_id)
            inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if inv:
                inv.status = "duplicate"
                inv.error_msg = "发票代码+号码与已有发票冲突（唯一约束）"
                db.commit()
        logger.info("发票 %d 处理完成: %s", invoice_id, inv.status)

    except Exception as e:
        logger.exception("处理发票 %d 失败", invoice_id)
        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv:
            inv.status = "error"
            inv.error_msg = str(e)
            db.commit()
    finally:
        db.close()


async def process_invoice_queue(invoice_ids: list[int]) -> None:
    """串行处理队列中的发票（避免本地模型并发过载）。"""
    for inv_id in invoice_ids:
        await process_invoice(inv_id)


def schedule_processing(invoice_ids: list[int]) -> None:
    """在后台任务中启动处理队列。"""
    asyncio.create_task(process_invoice_queue(invoice_ids))
