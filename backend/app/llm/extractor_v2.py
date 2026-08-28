"""OCR + LLM 混合提取器：先用 OCR 提取文字和位置，再让 LLM 结构化。"""
import json
import logging
import re
from pathlib import Path

from .client import chat_completion
from ..services.ocr_service import (
    check_paddleocr,
    build_ocr_context,
)

logger = logging.getLogger(__name__)


# 精简版 Prompt：只做结构化，文字已由 OCR 提供
# 注意：开头的"直接输出"指令是为了防止 reasoning 类模型（如 gemma4:e4b）
# 先生成长篇思考过程，导致 content 被 max_tokens 截断为空。
STRUCTURE_PROMPT = """[直接输出最终 JSON，不要输出任何思考过程、解释或 markdown 标记]

你是发票信息整理助手，任务是把 OCR 识别出的文字整理成结构化字段。

OCR 结果按区域分组如下：
====================
{ocr_context}
====================

请精准提取以下字段：

字段定义：
- invoice_type: 票据类型
- invoice_code: 发票代码
- invoice_number: 发票号码
- issue_date: 开票日期
- amount_ex_tax: 不含税金额
- tax_amount: 税额
- amount_total: 价税合计/小写金额
- seller_name: 销售方名称
- buyer_name: 购买方名称
- seller_tax_id: 销售方纳税人识别号（大写字母+数字，无空格）
- buyer_tax_id: 购买方纳税人识别号（同上格式）
- items: 商品/服务明细，数组，每项为 {{"name": "商品名称", "amount": 金额}}
- remark: 备注栏内容

输出规则：
1. 只输出一个 JSON 对象，不要任何前后缀
2. 数字只保留数值（如 123.45），移除"¥"、"元"等单位
3. 发票代码/号码：保留所有数字，移除空格、连字符、全角字符
4. 找不到的字段填 null
5. 票据类型选择：增值税电子普通发票、增值税专用发票、增值税普通发票、火车票、航空行程单、出租车票、网约车行程单、其他

输出示例：
{{"invoice_type":"增值税电子普通发票","invoice_code":"144032409110","invoice_number":"02203307","issue_date":"2024-01-14","amount_ex_tax":972.45,"tax_amount":58.35,"amount_total":1030.8,"seller_name":"xxx","buyer_name":"yyy","seller_tax_id":"91440300MA5XXXXX0Y","buyer_tax_id":"91440300MA5YYYYY9X","items":[{{"name":"餐饮费","amount":972.45}}],"remark":null}}
"""


def _parse_json(raw: str) -> dict | None:
    """从模型输出中提取 JSON。"""
    if not raw:
        return None
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def extract_with_ocr(
    img_path: Path,
    *,
    cfg: dict | None = None,
    model: str | None = None,
    return_raw: bool = False,
) -> dict | tuple[dict, str]:
    """OCR + LLM 混合识别入口。

    流程：
    1. RapidOCR 提取文字和坐标
    2. 按位置分组（右上角、中部、底部）
    3. LLM 根据结构化 OCR 结果输出 JSON

    return_raw=True 时返回 (parsed, raw) 元组。
    默认使用配置中的 extract_model（而非 chat_model）。
    """
    if not check_paddleocr():
        raise RuntimeError("RapidOCR 未安装，无法使用 OCR 模式")

    if cfg is None:
        from .client import get_llm_config
        cfg = get_llm_config()

    # 1. 构建 OCR 上下文
    ocr_context = build_ocr_context(img_path)
    if not ocr_context:
        raise RuntimeError("OCR 未识别到任何文字")

    # 2. 调用 LLM 结构化
    prompt = STRUCTURE_PROMPT.format(ocr_context=ocr_context)
    messages = [{"role": "user", "content": prompt}]

    raw = await chat_completion(
        messages,
        cfg=cfg,
        model=model or cfg.get("extract_model"),
        temperature=0.0,
        max_tokens=4096,  # 留足 buffer，避免 reasoning 模型（gemma4 等）被截断
        json_mode=True,
    )
    logger.info("OCR+LLM 模式 LLM 原始输出:\n%s", raw)

    # 3. 解析
    parsed = _parse_json(raw)
    if parsed is None:
        raise RuntimeError(f"JSON 解析失败: {raw[:200]}")

    logger.info("OCR+LLM 模式解析结果:\n%s", json.dumps(parsed, ensure_ascii=False, indent=2))
    return (parsed, raw) if return_raw else parsed
