"""应用配置。

模型相关配置优先从数据库 Setting 表读取（可在 Settings 页面修改），
这里提供默认值与磁盘目录等环境变量配置。
"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据根目录（发票原件）。默认 backend/data
    data_dir: str = os.environ.get(
        "DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")
    )
    # 数据库 URL（PostgreSQL）
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/invoice_agent"
    )
    # CORS 允许的来源（逗号分隔的字符串，便于环境变量配置）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:80,http://localhost"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
    # 前端构建产物目录（存在时由 FastAPI 静态托管）
    frontend_dist: str = str(Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")


settings = Settings()

DATA_DIR = Path(settings.data_dir)
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_URL = settings.database_url
for d in (DATA_DIR, UPLOAD_DIR):
    d.mkdir(parents=True, exist_ok=True)
