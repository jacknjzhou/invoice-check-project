"""设置路由：模型配置读写 + 连接测试。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..llm.client import apply_preset, chat_completion, get_llm_config
from ..models import Setting
from ..schemas import SettingOut, SettingTestResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_or_create_setting(db: Session, key: str, default: str = "") -> Setting:
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        s = Setting(key=key, value=default)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


@router.get("", response_model=SettingOut)
def get_settings():
    """读取当前模型配置。"""
    cfg = get_llm_config()
    return SettingOut(**cfg)


@router.put("", response_model=SettingOut)
def update_settings(data: SettingOut, db: Session = Depends(get_db)):
    """更新模型配置（provider 变更时套用预设）。"""
    current = get_llm_config()
    if data.provider != current.get("provider"):
        # provider 切换，套用预设
        new_cfg = apply_preset(data.provider, current)
        new_cfg["api_key"] = data.api_key  # 保留用户填的 key
    else:
        new_cfg = data.model_dump()

    for k, v in new_cfg.items():
        s = _get_or_create_setting(db, k)
        s.value = str(v) if v is not None else ""
    db.commit()
    return SettingOut(**new_cfg)


@router.post("/test", response_model=SettingTestResponse)
async def test_connection():
    """测试 LLM 连接（发送极简消息）。"""
    cfg = get_llm_config()
    try:
        messages = [{"role": "user", "content": "hi"}]
        resp = await chat_completion(messages, cfg=cfg, timeout=30.0, retries=0)
        if resp:
            return SettingTestResponse(ok=True, message="连接成功")
        return SettingTestResponse(ok=False, message="响应为空")
    except Exception as e:
        return SettingTestResponse(ok=False, message=str(e))
