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
    
    # 2. 发票代码/号码格式校验
    # 中国发票代码：10/12 位（全电发票无代码）；发票号码：8 位（纸质/早期电子）
    # 或 20 位（全电）。位数不对说明模型张冠李戴（常见：把纳税人识别号、
    # 身份证号、电子客票号当成发票号码），置空以触发 QR 锚点补齐/二次提取，
    # 绝不能带错值入库（会污染查重）。
    if fields.get("invoice_code"):
        original_code = str(fields["invoice_code"])
        code = re.sub(r"[^\d]", "", original_code)
        if len(code) in (10, 12):
            fields["invoice_code"] = code
            if code != original_code:
                logger.info("发票代码清洗: '%s' -> '%s'", original_code, code)
        else:
            logger.warning(
                "发票代码位数非标准(%d位): '%s'，置空待 QR/二次提取修正",
                len(code), original_code,
            )
            fields["invoice_code"] = None

    if fields.get("invoice_number"):
        original_number = str(fields["invoice_number"])
        number = re.sub(r"[^\d]", "", original_number)
        if len(number) in (8, 20):
            fields["invoice_number"] = number
            if number != original_number:
                logger.info("发票号码清洗: '%s' -> '%s'", original_number, number)
        else:
            logger.warning(
                "发票号码位数非标准(%d位): '%s'，置空待 QR/二次提取修正",
                len(number), original_number,
            )
            fields["invoice_number"] = None
    
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
    
    # 4. 金额勾稽校验 + 规则化修复
    # 实测模型常见错误：ex_tax=tax=total 照抄、金额幻觉（填 100 等）。
    # 以价税合计（可被 QR 锚定，最可信）为基准，按规则修复明显错值，
    # 修复不了的保留不一致（由 sanity 触发二次提取/人工复核）。
    ex_tax = fields.get("amount_ex_tax")
    tax = fields.get("tax_amount")
    total = fields.get("amount_total")

    def _num(v):
        return isinstance(v, (int, float))

    if _num(ex_tax) and _num(total) and ex_tax > total:
        # 不含税金额不可能大于价税合计：模型幻觉/错位，置空待重取
        logger.warning("不含税金额 %.2f > 价税合计 %.2f，置空", ex_tax, total)
        fields["amount_ex_tax"] = None
        ex_tax = None
    if _num(tax) and _num(total) and (tax < 0 or tax > total):
        # 税额为负或超过合计（如免税票税额被照抄成合计值）：置空
        logger.warning("税额 %.2f 非法（应为 0~合计），置空", tax)
        fields["tax_amount"] = None
        tax = None

    ex_tax, tax = fields.get("amount_ex_tax"), fields.get("tax_amount")
    if _num(ex_tax) and _num(tax) and _num(total) and abs(ex_tax + tax - total) > 0.5:
        if abs(ex_tax - total) < 0.01 and abs(tax - total) < 0.01:
            # ex_tax = tax = total：免税/未显示税额票，税额被照抄，置空
            logger.warning("税额与合计相同（照抄模式），置空税额")
            fields["tax_amount"] = None
        else:
            # 税额看起来合理（0 < tax < total）但不含税对不上：用合计-税额反推
            if 0 < tax < total and abs((total - tax) - ex_tax) > 0.5:
                fixed = round(total - tax, 2)
                logger.warning(
                    "勾稽不一致，按 合计-税额 反推不含税: %.2f -> %.2f", ex_tax, fixed
                )
                fields["amount_ex_tax"] = fixed

    # 4.5 税额缺失但有不含税与合计的差额：增值税发票必有税额，反推补齐
    # （免税票 ex_tax == total，差额为 0 不会触发）
    if fields.get("tax_amount") in (None, 0):
        ex_tax, total = fields.get("amount_ex_tax"), fields.get("amount_total")
        if _num(ex_tax) and _num(total):
            gap = round(total - ex_tax, 2)
            if 0 < gap < total:
                logger.warning("税额缺失，按 合计-不含税 反推: %.2f", gap)
                fields["tax_amount"] = gap

    # 5. 如果只有不含税金额和税额，没有价税合计，自动计算
    if fields.get("amount_total") is None and fields.get("amount_ex_tax") is not None and fields.get("tax_amount") is not None:
        fields["amount_total"] = round(fields["amount_ex_tax"] + fields["tax_amount"], 2)

    # 6. 如果只有价税合计和税额，反推不含税金额
    if fields.get("amount_ex_tax") is None and fields.get("amount_total") is not None and fields.get("tax_amount") is not None:
        fields["amount_ex_tax"] = round(fields["amount_total"] - fields["tax_amount"], 2)

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

    async def merge_all(pages: list[Path]) -> tuple[dict, list, dict | None]:
        """逐页提取 → 合并 → QR 校正 → 清洗校验。返回 (merged, raws, qr)。"""
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
            qr = decode_invoice_qr(pages[0])
        except Exception as qr_err:
            logger.warning("QR 解码异常: %s", qr_err)
            qr = None
        if qr:
            merged = apply_qr_anchor(merged, qr)

        merged = validate_and_fix_fields(merged)
        # validate 会把位数不合法的代码/号码置空，再用 QR 补齐一次
        if qr:
            merged = apply_qr_anchor(merged, qr)
        return merged, raws, qr

    logger.info("开始提取 %s，模式: %s", file_path.name, "OCR+LLM" if use_ocr else "纯视觉")
    merged, raws, qr = await merge_all(img_paths)

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
        if qr:
            second_merged = apply_qr_anchor(second_merged, qr)
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
        try:
            # 数据库异常后 session 处于失效事务状态，必须先 rollback
            # 才能继续操作（否则抛 PendingRollbackError）
            db.rollback()
            inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if inv:
                inv.status = "error"
                inv.error_msg = str(e)[:500]
                db.commit()
        except Exception:
            # 数据库本身不可用（如 Postgres 重启）时无法写状态，等待下次重试
            logger.exception("发票 %d 状态更新失败（数据库不可用）", invoice_id)
    finally:
        db.close()


async def process_invoice_queue(invoice_ids: list[int]) -> None:
    """串行处理队列中的发票（避免本地模型并发过载）。单张失败不中断队列。"""
    for inv_id in invoice_ids:
        try:
            await process_invoice(inv_id)
        except Exception:
            logger.exception("队列处理发票 %d 异常，继续处理下一张", inv_id)


def schedule_processing(invoice_ids: list[int]) -> None:
    """在后台任务中启动处理队列。"""
    asyncio.create_task(process_invoice_queue(invoice_ids))
