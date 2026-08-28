"""发票识别准确度验证脚本（独立运行，不依赖 DB/后端服务）。

复刻当前识别管线的关键逻辑：
  PDF 300dpi 转图 → QR 码锚点解析 → 视觉模型提取（EXTRACT_PROMPT）
  → 字段清洗（与 validate_and_fix_fields 一致的关键步骤）→ 勾稽/完整性自检

用法：
  .venv/bin/python verify_acc.py invoice-docs --single "212元.png"   # 单张冒烟
  .venv/bin/python verify_acc.py invoice-docs                        # 全量
  .venv/bin/python verify_acc.py invoice-docs --out results.json     # 保存明细
"""
import argparse
import asyncio
import ast
import base64
import json
import re
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
DOC_DIR = BASE.parent / "invoice-docs"
RENDER_DIR = BASE / "data" / "verify_render"

BASE_URL = "http://localhost:11434/v1"
MODEL = "glm-ocr:bf16"
DPI = 300


def load_prompt() -> str:
    """从 app/llm/extractor.py 抽取 EXTRACT_PROMPT，保证与线上逻辑一致。"""
    src = (BASE / "app" / "llm" / "extractor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EXTRACT_PROMPT":
            return ast.literal_eval(node.value)
    raise RuntimeError("EXTRACT_PROMPT not found")


def render_pdf(pdf_path: Path) -> list[Path]:
    """PDF 转图（300dpi，与 pdf_to_images 一致）。"""
    import fitz  # pymupdf
    out_dir = RENDER_DIR / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
            out = out_dir / f"p{i}.png"
            pix.save(out)
            pages.append(out)
    finally:
        doc.close()
    return pages


def to_b64(img_path: Path) -> str:
    return base64.b64encode(img_path.read_bytes()).decode()


def snip_json(text: str) -> str | None:
    """截取第一个完整的 JSON 对象（平衡大括号），兼容模型重复输出多块。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


async def extract_page(client: httpx.AsyncClient, img_path: Path) -> tuple[dict, str]:
    """调用视觉模型提取单页（json_mode 对 glm-ocr 有害，不用 response_format）。"""
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": load_prompt()},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{to_b64(img_path)}"}},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": 2048,
    }
    last_err = None
    expected_keys = {"invoice_type", "invoice_code", "invoice_number", "issue_date",
                     "amount_ex_tax", "tax_amount", "amount_total",
                     "seller_name", "buyer_name", "items", "remark"}
    for attempt in range(2):  # 解析失败/结构异常重试一次（二次加 frequency_penalty 打破重复循环）
        body = dict(payload)
        if attempt > 0:
            body["frequency_penalty"] = 0.5
        resp = await client.post(f"{BASE_URL}/chat/completions", json=body, timeout=300)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = msg.get("content") or ""
        if not content:
            reasoning = msg.get("reasoning", "")
            content = snip_json(reasoning) or ""
        raw = snip_json(content)
        if not raw:
            last_err = f"非 JSON 输出: {content[:120]}"
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = e
            continue
        if not (set(obj) & expected_keys):
            last_err = f"JSON 字段全部无法识别: {list(obj)[:8]}"
            continue
        return obj, content
    raise RuntimeError(str(last_err))


def decode_qr(img_path: Path) -> dict | None:
    """QR 解码（与 qr_service 相同格式解析）。"""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode, ZBarSymbol
        img = Image.open(img_path)
        rotations = [img, img.transpose(Image.ROTATE_90), img.transpose(Image.ROTATE_180), img.transpose(Image.ROTATE_270)]
        for rot in rotations:
            for r in decode(rot, symbols=[ZBarSymbol.QRCODE]):
                text = r.data.decode("utf-8", errors="replace")
                parts = [p.strip() for p in text.split(",")]
                if parts[0] != "01" or len(parts) < 6:
                    continue
                out = {"invoice_code": None, "invoice_number": None, "amount_total": None, "issue_date": None}
                idx, code = 2, parts[2]
                if code.isdigit() and len(code) in (10, 12):
                    out["invoice_code"] = code
                    idx += 1
                elif code == "":
                    idx += 1
                num = parts[idx]
                if num.isdigit() and len(num) in (8, 20):
                    out["invoice_number"] = num
                    idx += 1
                else:
                    continue
                try:
                    out["amount_total"] = round(float(parts[idx]), 2)
                    idx += 1
                except (ValueError, IndexError):
                    pass
                d = parts[idx] if idx < len(parts) else ""
                if d.isdigit() and len(d) == 8:
                    out["issue_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                return out
    except Exception as e:
        print(f"    QR 解码异常: {e}")
    return None


def clean_fields(f: dict) -> dict:
    """与 validate_and_fix_fields 一致的关键清洗：日期、代码/号码、金额、勾稽修复。"""
    # 日期
    d = f.get("issue_date")
    if d:
        for fmt in ("%Y-%m-%d", "%Y年%m月%d日", "%Y/%m/%d"):
            try:
                from datetime import datetime
                f["issue_date"] = datetime.strptime(str(d).strip(), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
    # 代码/号码仅保留数字，位数不合法置空（待 QR 补齐）
    for key, std in (("invoice_code", (10, 12)), ("invoice_number", (8, 20))):
        v = f.get(key)
        if v is not None:
            digits = re.sub(r"\D", "", str(v))
            f[key] = digits if len(digits) in std else None
    # 金额
    for key in ("amount_ex_tax", "tax_amount", "amount_total"):
        v = f.get(key)
        if v is not None:
            val = str(v).replace(",", "").replace(" ", "").replace("¥", "").replace("元", "")
            try:
                f[key] = round(float(val), 2)
            except (ValueError, TypeError):
                f[key] = None
    # 纳税人识别号
    for key in ("seller_tax_id", "buyer_tax_id"):
        if f.get(key):
            f[key] = re.sub(r"[\s\-—－]", "", str(f[key])).upper()
    # 勾稽修复（与线上 reconcile 规则一致）
    ex_tax, tax, total = f.get("amount_ex_tax"), f.get("tax_amount"), f.get("amount_total")
    _n = lambda x: isinstance(x, (int, float))
    if _n(ex_tax) and _n(total) and ex_tax > total:
        f["amount_ex_tax"] = ex_tax = None
    if _n(tax) and _n(total) and (tax < 0 or tax > total):
        f["tax_amount"] = tax = None
    ex_tax, tax = f.get("amount_ex_tax"), f.get("tax_amount")
    if _n(ex_tax) and _n(tax) and _n(total) and abs(ex_tax + tax - total) > 0.5:
        if abs(ex_tax - total) < 0.01 and abs(tax - total) < 0.01:
            f["tax_amount"] = None
        elif 0 < tax < total and abs((total - tax) - ex_tax) > 0.5:
            f["amount_ex_tax"] = round(total - tax, 2)
    # 税额缺失/为 0 且有差额：反推补齐（与线上 4.5 规则一致）
    if f.get("tax_amount") in (None, 0):
        ex2, tot2 = f.get("amount_ex_tax"), f.get("amount_total")
        if _n(ex2) and _n(tot2):
            gap = round(tot2 - ex2, 2)
            if 0 < gap < tot2:
                f["tax_amount"] = gap
    if f.get("amount_total") is None and f.get("amount_ex_tax") is not None and f.get("tax_amount") is not None:
        f["amount_total"] = round(f["amount_ex_tax"] + f["tax_amount"], 2)
    if f.get("amount_ex_tax") is None and f.get("amount_total") is not None and f.get("tax_amount") is not None:
        f["amount_ex_tax"] = round(f["amount_total"] - f["tax_amount"], 2)
    return f


def sanity(f: dict) -> list[str]:
    """与 check_fields_sanity 一致的自检。"""
    issues = []
    number = f.get("invoice_number")
    if not number:
        issues.append("缺少发票号码")
    elif not str(number).isdigit():
        issues.append(f"发票号码含非数字: {number}")
    if not f.get("invoice_code") and (not number or len(str(number)) != 20):
        issues.append("缺少发票代码（且号码非全电 20 位）")
    d = f.get("issue_date")
    if not d:
        issues.append("缺少开票日期")
    else:
        try:
            from datetime import datetime
            datetime.strptime(str(d), "%Y-%m-%d")
        except ValueError:
            issues.append(f"日期格式异常: {d}")
    total = f.get("amount_total")
    if total is None:
        issues.append("缺少价税合计")
    ex_tax, tax = f.get("amount_ex_tax"), f.get("tax_amount")
    if None not in (ex_tax, tax, total) and total is not None:
        try:
            if abs(float(ex_tax) + float(tax) - float(total)) > 0.5:
                issues.append(f"勾稽不一致: {ex_tax}+{tax}≠{total}")
        except (TypeError, ValueError):
            pass
    return issues


def apply_qr(f: dict, qr: dict | None) -> list[str]:
    """QR 锚点校验/修正，返回冲突记录。"""
    conflicts = []
    if not qr:
        return conflicts
    for k in ("invoice_code", "invoice_number", "amount_total", "issue_date"):
        qv = qr.get(k)
        if qv is None:
            continue
        cv = f.get(k)
        if cv in (None, ""):
            f[k] = qv
        elif str(cv) != str(qv):
            conflicts.append(f"{k}: 提取={cv} QR={qv}")
            f[k] = qv
    return conflicts


async def process_file(client: httpx.AsyncClient, path: Path, sem: asyncio.Semaphore) -> dict:
    result = {"file": path.name, "qr": None, "qr_conflicts": [], "fields": None, "issues": [], "error": None}
    async with sem:
        try:
            t0 = time.time()
            if path.suffix.lower() == ".pdf":
                pages = render_pdf(path)
            else:
                pages = [path]
            page_fields = []
            for p in pages:
                f, _raw = await extract_page(client, p)
                page_fields.append(f)
            merged = {}
            for f in page_fields:  # 首页字段优先，items 拼接
                for k, v in f.items():
                    if k == "items" and v:
                        merged.setdefault("items", []).extend(v)
                    elif k not in merged or merged[k] in (None, ""):
                        merged[k] = v
            merged = clean_fields(merged)
            qr = decode_qr(pages[0])
            result["qr"] = qr
            result["qr_conflicts"] = apply_qr(merged, qr)
            merged = clean_fields(merged)
            # 二次 QR 补齐（清洗置空不合法号码后）
            apply_qr(merged, qr)
            result["fields"] = {k: merged.get(k) for k in (
                "invoice_type", "invoice_code", "invoice_number", "issue_date",
                "amount_ex_tax", "tax_amount", "amount_total",
                "seller_name", "buyer_name", "seller_tax_id", "buyer_tax_id")}
            result["issues"] = sanity(merged)
            result["elapsed"] = round(time.time() - t0, 1)
        except Exception as e:
            result["error"] = str(e)[:200]
    return result


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", default=None, help="只处理单个文件（文件名）")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    files = sorted([p for p in DOC_DIR.iterdir() if p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg")])
    if args.single:
        wanted = [s.strip() for s in args.single.split(",") if s.strip()]
        files = [p for p in files if p.name in wanted]
    if not files:
        print("未找到发票文件", file=sys.stderr)
        return 2

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(2)
    results = []
    async with httpx.AsyncClient() as client:
        t0 = time.time()
        for i, path in enumerate(files, 1):
            r = await process_file(client, path, sem)
            results.append(r)
            mark = "ERR" if r["error"] else ("ISSUES: " + "; ".join(r["issues"]) if r["issues"] else "OK")
            print(f"[{i}/{len(files)}] {path.name} -> {mark}")
            if r["qr_conflicts"]:
                print(f"    QR 冲突修正: {r['qr_conflicts']}")

    ok = sum(1 for r in results if not r["error"] and not r["issues"])
    qr_hit = sum(1 for r in results if r["qr"])
    n_conflict = sum(1 for r in results if r["qr_conflicts"])
    print("\n" + "=" * 70)
    print(f"文件数: {len(results)}  完全通过: {ok}  QR 命中: {qr_hit}  QR 修正冲突: {n_conflict}  耗时: {time.time()-t0:.0f}s")
    for r in results:
        if r["error"]:
            print(f"  [错误] {r['file']}: {r['error']}")
        elif r["issues"]:
            print(f"  [问题] {r['file']}: {r['issues']}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已保存: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
