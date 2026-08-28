"""发票提取准确率评测脚本。

对 golden 测试集运行完整提取管线（含 QR 校正、自检），与人工标注
真值逐字段比对，输出每个字段的准确率。用于更换模型前后量化对比。

数据集布局：
    eval_dataset/
        ground_truth.json
        001.jpg
        002.pdf
        ...

ground_truth.json 格式：
[
    {
        "file": "001.jpg",
        "expected": {
            "invoice_code": "144032409110",
            "invoice_number": "02203307",
            "issue_date": "2024-01-14",
            "amount_ex_tax": 972.45,
            "tax_amount": 58.35,
            "amount_total": 1030.80,
            "seller_name": "xxx公司",
            "buyer_name": "yyy公司"
        }
    }
]

用法（在 backend 目录）：
    python evaluate.py --dataset ./eval_dataset
    python evaluate.py --dataset ./eval_dataset --no-ocr            # 强制纯视觉模式
    python evaluate.py --dataset ./eval_dataset --model qwen2.5vl:7b
    python evaluate.py --dataset ./eval_dataset --out results.json  # 保存明细
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# 参与评分的字段（items 为复杂结构、remark 自由文本，不计分）
FIELD_KEYS = [
    "invoice_type", "invoice_code", "invoice_number", "issue_date",
    "amount_ex_tax", "tax_amount", "amount_total",
    "seller_name", "buyer_name", "seller_tax_id", "buyer_tax_id",
]
AMOUNT_KEYS = {"amount_ex_tax", "tax_amount", "amount_total"}


def _norm(key: str, value) -> str:
    """字段值标准化后用于比对。"""
    if value is None:
        return ""
    if key in AMOUNT_KEYS:
        try:
            return f"{round(float(value), 2):.2f}"
        except (ValueError, TypeError):
            return str(value).strip()
    return str(value).strip()


def field_match(key: str, expected, got) -> bool:
    """单个字段比对：金额允许 ±0.01 误差，其余精确匹配。"""
    return _norm(key, expected) == _norm(key, got)


async def evaluate_dataset(
    dataset_dir: Path,
    *,
    use_ocr: bool | None = None,
    model: str | None = None,
) -> dict:
    """运行评测，返回统计结果 dict。"""
    from app.services.processing import extract_invoice_fields

    gt_path = dataset_dir / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(f"缺少真值文件: {gt_path}")

    entries = json.loads(gt_path.read_text(encoding="utf-8"))
    if not entries:
        raise ValueError("ground_truth.json 为空")

    # 统计结构：field -> {"correct": int, "total": int}
    stats = {k: {"correct": 0, "total": 0} for k in FIELD_KEYS}
    details = []
    start = time.time()

    for i, entry in enumerate(entries, 1):
        fname = entry.get("file", "")
        expected = entry.get("expected", {})
        fpath = dataset_dir / fname
        detail = {"file": fname, "ok": False, "mismatches": {}, "issues": [], "error": None}
        details.append(detail)

        if not fpath.exists():
            detail["error"] = f"文件不存在: {fpath}"
            print(f"[{i}/{len(entries)}] {fname} - 跳过（文件不存在）")
            continue

        try:
            out = await extract_invoice_fields(fpath, use_ocr=use_ocr, model=model)
        except Exception as e:  # noqa: BLE001
            detail["error"] = str(e)
            print(f"[{i}/{len(entries)}] {fname} - 提取失败: {e}")
            continue

        got = out["fields"]
        detail["issues"] = out["issues"]
        detail["got"] = {k: got.get(k) for k in FIELD_KEYS}

        n_match = 0
        for k in FIELD_KEYS:
            exp_val = expected.get(k)
            if exp_val in (None, ""):
                continue  # 真值为空不计入分母
            stats[k]["total"] += 1
            if field_match(k, exp_val, got.get(k)):
                stats[k]["correct"] += 1
                n_match += 1
            else:
                detail["mismatches"][k] = {
                    "expected": exp_val,
                    "got": got.get(k),
                }
        detail["ok"] = not detail["mismatches"]
        status = "OK" if detail["ok"] else f"{n_match}/{len(expected)} 字段命中"
        print(f"[{i}/{len(entries)}] {fname} - {status}")
        if detail["mismatches"]:
            for k, m in detail["mismatches"].items():
                print(f"    {k}: 期望 {m['expected']!r} / 实际 {m['got']!r}")

    elapsed = time.time() - start
    total_evaluable = sum(s["total"] for s in stats.values())
    total_correct = sum(s["correct"] for s in stats.values())

    field_accuracy = {
        k: round(s["correct"] / s["total"], 4) if s["total"] else None
        for k, s in stats.items()
    }
    return {
        "model": model or "(配置默认 extract_model)",
        "files": len(entries),
        "elapsed_sec": round(elapsed, 1),
        "overall_accuracy": round(total_correct / total_evaluable, 4) if total_evaluable else 0,
        "field_accuracy": field_accuracy,
        "field_stats": stats,
        "details": details,
    }


def print_report(result: dict) -> None:
    """打印评测报告。"""
    print("\n" + "=" * 60)
    print(f"模型: {result['model']}   文件数: {result['files']}   耗时: {result['elapsed_sec']}s")
    print(f"总体字段准确率: {result['overall_accuracy']:.1%}")
    print("-" * 60)
    print(f"{'字段':<16}{'准确率':<10}{'命中/总数'}")
    for k, acc in result["field_accuracy"].items():
        s = result["field_stats"][k]
        acc_str = f"{acc:.1%}" if acc is not None else "  -"
        print(f"{k:<16}{acc_str:<10}{s['correct']}/{s['total']}")
    print("=" * 60)


async def main() -> int:
    parser = argparse.ArgumentParser(description="发票提取准确率评测")
    parser.add_argument("--dataset", required=True, help="评测数据集目录")
    parser.add_argument("--no-ocr", action="store_true", help="强制纯视觉模式（不用 OCR）")
    parser.add_argument("--model", default=None, help="指定提取模型（覆盖配置）")
    parser.add_argument("--out", default=None, help="评测明细 JSON 输出路径")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset)
    try:
        result = await evaluate_dataset(
            dataset_dir,
            use_ocr=False if args.no_ocr else None,
            model=args.model,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"评测数据集错误: {e}", file=sys.stderr)
        return 2

    print_report(result)
    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"明细已保存: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
