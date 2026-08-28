"""发票信息提取：优化的单次提取方案。"""
import base64
import json
import logging
import re

from .client import chat_completion

logger = logging.getLogger(__name__)

# 优化的单次提取 Prompt
EXTRACT_PROMPT = """你是一名专业的发票识别助手。请仔细识别图片、文字信息，提取以下信息。

要求：
1. 字段含义：
   - invoice_type: 票据类型，从以下选择最合适的一项：增值税电子普通发票、增值税专用发票、增值税普通发票、火车票、航空行程单、出租车票、网约车行程单、出租车发票、酒店水单、餐饮发票、其他
   - invoice_code: 发票代码，纯数字字符串，不要包含任何分隔符。
   - invoice_number: 发票号码，纯数字字符串，不要包含任何分隔符。
   - issue_date: 开票日期，输出格式 YYYY-MM-DD，识别不出则 null。
   - amount_ex_tax: 不含税金额（数字，不含货币符号)
   - tax_amount: 税额（数字）
   - amount_total: 价税合计/小写金额（数字）
   - seller_name: 销售方/商户名称
   - buyer_name: 购买方名称
   - items: 商品/服务明细，数组，每项为 {"name": 商品名称, "amount": 金额}，无则 []
   - remark: 备注栏内容，无则 null
3. 数字只保留数值（如 123.45），不要带"元"、"¥"等单位。
4. 电子发票与纸质发票都适用；火车票/行程单等交通票据按同样字段尽力填写（invoice_type 选对应类型，金额填实际支付金额）。
5. 对于模糊不清的字段，填 null，不要猜测。
6. 发票代码和发票号码必须输出为字符串类型，即使全是数字也要用引号包裹。

只输出一个 JSON 对象，不要输出任何解释、前后缀或 markdown 代码块标记。
参考输出JSON格式示例：
{"invoice_type":"","invoice_code":"","invoice_number":"","issue_date":"","amount_ex_tax":100.0,"tax_amount":0,"amount_total":0,"seller_name":"","buyer_name":"","items":[{"name":"","amount":100.0}],"remark":null}
"""


def _extract_json(raw: str) -> dict | None:
    """从模型输出中提取 JSON 对象，容忍代码块与多余文字。"""
    if not raw:
        return None
    text = raw.strip()
    # 去除 markdown 代码块
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 直接解析
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # 兜底：抓取第一个 { ... } 块
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def extract_invoice(
    image_b64: str,
    mime: str,
    *,
    cfg: dict | None = None,
    model: str | None = None,
) -> dict:
    """将发票图片送入视觉模型，返回结构化的发票字段 dict。
    
    使用单次提取，简单可靠。
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": EXTRACT_PROMPT,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{image_b64}",
                    },
                },
            ],
        }
    ]
    raw = await chat_completion(
        messages, cfg=cfg, model=model, temperature=0.0, max_tokens=2048
    )
    
    # 记录模型原始输出
    logger.info("模型原始输出:\n%s", raw)
    
    parsed = _extract_json(raw)
    if parsed is None:
        raise RuntimeError(f"模型输出无法解析为 JSON: {raw[:200]}")
    
    # 记录解析后的结构化数据
    logger.info("解析后的发票数据:\n%s", json.dumps(parsed, ensure_ascii=False, indent=2))
    
    return parsed


def to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
