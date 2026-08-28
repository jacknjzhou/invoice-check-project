"""对话路由：基于发票数据的问答。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ChatRequest, ChatResponse
from ..services.chat import answer_question

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """对话问答：基于已整理发票数据回答用户问题。"""
    question = req.question
    if not question and req.messages:
        # 兼容：如果没传 question，取最后一条 user 消息
        for msg in reversed(req.messages):
            if msg.role == "user":
                question = msg.content
                break
    if not question:
        return ChatResponse(answer="请提供您的问题。", context_note=None)

    # 构建历史（排除最后一条）
    history = []
    for msg in req.messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})
    if history and history[-1]["role"] == "user":
        history = history[:-1]  # 移除最后一条（当前问题）

    answer, note = await answer_question(db, question, history)
    return ChatResponse(answer=answer, context_note=note)
