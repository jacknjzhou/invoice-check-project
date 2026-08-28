"""FastAPI 应用入口。"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routers import chat, invoices, reports, settings as settings_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="发票整理助手", version="0.1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(invoices.router)
app.include_router(reports.router)
app.include_router(chat.router)
app.include_router(settings_router.router)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health_check():
    """健康检查端点（用于 Docker 健康检查）。"""
    return {"status": "healthy"}


# 静态托管前端构建产物（生产模式）
frontend_dist = Path(settings.frontend_dist)
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
