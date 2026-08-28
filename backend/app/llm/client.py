"""OpenAI 兼容的异步 LLM 客户端。

Ollama（http://localhost:11434/v1）与各类云端 API 均提供
`/chat/completions` 接口，这里用 httpx 统一封装，支持文本与视觉（image_url base64）。
"""
import asyncio
import logging
import os

import httpx

from ..database import SessionLocal
from ..models import Setting

logger = logging.getLogger(__name__)


def _snip_json(text: str) -> str | None:
    """截取第一个完整的 JSON 对象（平衡大括号），容忍 markdown 代码块与重复输出。

    不能用 first-{ 到 last-} 截取：模型重复输出多个 JSON 块时，
    跨块截取会产生非法 JSON。
    """
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


def _detect_default_base_url() -> str:
    """根据运行环境自动推断 Ollama base_url。

    优先级：
    1. 显式环境变量 OLLAMA_BASE_URL
    2. 通过 /proc/1/cgroup 检测是否在容器内运行（容器内使用 host.docker.internal）
    3. 默认 localhost（宿主机本地开发）
    """
    explicit = os.getenv("OLLAMA_BASE_URL")
    if explicit:
        return explicit

    # 容器内运行时，通过 host.docker.internal 访问宿主机的 Ollama
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
            content = f.read()
        if "docker" in content or "containerd" in content:
            return "http://host.docker.internal:11434/v1"
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return "http://localhost:11434/v1"


# 默认配置（首次运行或数据库无记录时使用）
DEFAULTS = {
    "provider": "ollama",
    "base_url": _detect_default_base_url(),
    "api_key": "",
    "extract_model": "qwen2.5vl:7b",
    "chat_model": "qwen2.5vl:7b",
    "json_mode": "0",  # response_format:json_object 开关；部分模型（glm-ocr 等）开启后输出残缺
}

CLOUD_PRESETS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "extract_model": "gpt-4o",
        "chat_model": "gpt-4o-mini",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "extract_model": "qwen-vl-max",
        "chat_model": "qwen-max",
    },
}


def get_llm_config() -> dict:
    """从数据库读取配置，缺失字段用默认值补齐。"""
    cfg = dict(DEFAULTS)
    db = SessionLocal()
    try:
        for row in db.query(Setting).all():
            if row.key in cfg and row.value:
                cfg[row.key] = row.value
    finally:
        db.close()
    return cfg


def apply_preset(provider: str, current: dict) -> dict:
    """切换 provider 时套用预设（用户已填的 api_key 保留）。"""
    cfg = dict(DEFAULTS)
    if provider == "ollama":
        cfg["base_url"] = current.get("base_url") or DEFAULTS["base_url"]
        cfg["extract_model"] = current.get("extract_model") or DEFAULTS["extract_model"]
        cfg["chat_model"] = current.get("chat_model") or DEFAULTS["chat_model"]
        cfg["api_key"] = ""
    elif provider in CLOUD_PRESETS:
        preset = CLOUD_PRESETS[provider]
        cfg["base_url"] = current.get("base_url") or preset["base_url"]
        cfg["extract_model"] = current.get("extract_model") or preset["extract_model"]
        cfg["chat_model"] = current.get("chat_model") or preset["chat_model"]
        cfg["api_key"] = current.get("api_key") or ""
    else:  # 自定义 OpenAI 兼容
        cfg["base_url"] = current.get("base_url") or DEFAULTS["base_url"]
        cfg["extract_model"] = current.get("extract_model") or DEFAULTS["extract_model"]
        cfg["chat_model"] = current.get("chat_model") or DEFAULTS["chat_model"]
        cfg["api_key"] = current.get("api_key") or ""
    cfg["provider"] = provider
    return cfg


async def chat_completion(
    messages: list[dict],
    *,
    cfg: dict | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    timeout: float = 180.0,
    retries: int = 2,
    json_mode: bool = False,
    frequency_penalty: float | None = None,
) -> str:
    """调用 OpenAI 兼容 chat 接口，返回文本内容。失败抛出 RuntimeError。

    json_mode=True 时附加 ``response_format: {"type": "json_object"}``，
    OpenAI / DashScope / 新版 Ollama 均支持；若服务端拒绝该参数，
    会自动去掉后重试，保证兼容旧模型。

    兼容性说明：部分模型（如 gemma4）会把"思考过程"放在非标准的
    ``reasoning`` 字段，``content`` 仍可能为空。本函数会优先返回
    ``content``，若为空则回退到 ``reasoning`` 中的 JSON 片段，避免
    被截断到思考过程的模型输出空白。
    """
    cfg = cfg or get_llm_config()
    model = model or cfg.get("chat_model")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    use_response_format = json_mode and str(cfg.get("json_mode", "0")).lower() in ("1", "true")
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if frequency_penalty is not None:
            # 用于打破病态重复输出（部分本地模型对某些图会无限重复同一 JSON）
            payload["frequency_penalty"] = frequency_penalty
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0].get("message", {})
                # 1) 标准 OpenAI 字段
                content = msg.get("content")
                # 2) reasoning 类模型的非标准字段（Ollama 上 gemma4 等会把
                #    思考过程放在这里；如果 content 已被截断为空，尝试用
                #    reasoning 中可解析出的 JSON 片段，避免把思考过程当结果）
                if not content:
                    reasoning = msg.get("reasoning", "")
                    if reasoning:
                        content = _snip_json(reasoning) or ""
                return content or ""
        except httpx.HTTPStatusError as e:
            last_err = e
            # 服务端不认识 response_format（常见于旧版 Ollama/vLLM），
            # 去掉该参数重试
            if use_response_format and e.response.status_code in (400, 422):
                logger.warning("服务端不支持 response_format，回退为普通文本模式")
                use_response_format = False
                continue
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")
