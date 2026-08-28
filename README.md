# 发票整理助手（Invoice Agent）

本地优先的 Web 应用智能体，帮助用户整理报销发票：OCR 识别、查重校验、分类报表、对话问答。

## 功能

- **OCR/信息提取**：从发票图片/PDF 中提取关键字段（视觉大模型直接结构化提取）
- **发票校验**：查重（防止重复报销）+ 基础规则校验
- **分类与报表**：按类型/月份/商户自动分类，汇总并导出 Excel 报销报表
- **对话式问答**：基于已整理发票数据与 LLM 对话（如"这个月差旅费多少"）
- **模型后端可配置**：默认本地 Ollama，可切换任意 OpenAI 兼容云端 API

## 技术栈

- **后端**：Python 3.11 / FastAPI / SQLAlchemy / PostgreSQL
- **LLM**：OpenAI 兼容客户端（Ollama / OpenAI / DashScope / 自定义）
- **前端**：React 18 / Vite / TypeScript / Ant Design
- **部署**：Docker Compose / Nginx

## 快速开始

### 1. 安装依赖

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd ../frontend
npm install
```

### 2. 启动后端

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

### 3. 启动前端（开发模式）

```bash
cd frontend
npm run dev
```

访问 http://localhost:5173

### 4. 配置模型（Settings 页面）

默认使用本地 Ollama，需先拉取视觉模型：

```bash
ollama pull qwen2.5vl:7b
```

也可在 Settings 页面切换到 OpenAI / DashScope / 自定义 API。

## Docker 部署（推荐）

### 模型预下载机制

为避免首次运行时下载模型失败或产生明显延迟，本项目在 Docker 镜像构建阶段就会预下载 **RapidOCR 模型**（det / rec / cls 三个 ONNX 模型）。这些模型会被烘焙到 `backend` 镜像层中，容器启动后即可直接使用。

> **历史说明**：早期版本曾使用 PaddleOCR，但 PaddlePaddle 3.x 在 aarch64（Apple Silicon Docker）上存在 C++ 段错误，已迁移到 RapidOCR（基于 ONNX Runtime），跨架构稳定。

LLM 模型（Ollama / OpenAI / DashScope 等）不在 Docker 中托管，而是由用户在宿主机或其他云端自行准备。`backend` 容器内通过 `host.docker.internal` 自动连接宿主机的 Ollama 服务。
http://host.docker.internal:8899/v1

### 快速启动

```bash
# 1. 启动宿主机 Ollama（或其他 LLM 服务oMLX）
ollama serve  # 确保监听 0.0.0.0:11434
ollama pull Qwen3.8-27B-4bit

# 2. 启动所有容器服务（首次会自动下载 RapidOCR 模型，耗时较短）
docker-compose up -d --build

# 3. 查看服务状态
docker-compose ps

# 4. 查看日志
docker-compose logs -f
```

### 访问地址

- **前端界面**：http://localhost（Nginx 托管）
- **后端 API**：http://localhost:8000
- **API 文档**：http://localhost:8000/docs
- **数据库**：localhost:5432

### 环境变量配置

复制 `.env.example` 为 `.env` 并修改：

```bash
cp .env.example .env
```

主要配置项：

```env
# PostgreSQL
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=invoice_agent

# 后端
DATA_DIR=/app/data
CORS_ORIGINS=http://localhost,http://localhost:80
```

### 常用命令

```bash
# 停止所有服务
docker-compose down

# 停止并删除数据卷（⚠️ 会清除数据库）
docker-compose down -v

# 重新构建镜像
docker-compose up -d --build

# 只启动后端和数据库（开发调试）
docker-compose up -d postgres backend

# 进入后端容器
docker-compose exec backend sh

# 进入数据库容器
docker-compose exec postgres psql -U postgres -d invoice_agent
```

### 数据持久化

- **PostgreSQL 数据**：`postgres_data` 卷
- **上传文件**：`backend_data` 卷（映射到 `/app/data`）

## 本地开发部署

```bash
# 1. 安装 PostgreSQL 并创建数据库
createdb invoice_agent

# 2. 配置环境变量
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/invoice_agent"

# 3. 启动后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 4. 启动前端（新终端）
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

### 如果需要让容器访问 docker 宿主机上的其他服务

`docker-compose.yml` 中 backend 已经配置了 `extra_hosts: ["host.docker.internal:host-gateway"]`，
容器内默认会自动通过 `http://host.docker.internal:11434/v1` 连接宿主机的 Ollama。
如有需要可在 Settings 页面手动调整 base_url，或通过环境变量 `OLLAMA_BASE_URL` 覆盖。

## 目录结构

```
local-model-project/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置
│   │   ├── database.py          # SQLAlchemy
│   │   ├── models.py            # 数据模型
│   │   ├── schemas.py           # Pydantic
│   │   ├── llm/                 # LLM 客户端
│   │   ├── services/            # 业务逻辑
│   │   └── routers/             # API 路由
│   ├── Dockerfile               # 后端镜像（含 RapidOCR 模型预下载）
│   └── requirements.txt         # Python 依赖
├── frontend/
│   ├── src/
│   │   ├── api/                 # API 封装
│   │   └── pages/               # 页面组件
│   ├── Dockerfile               # 前端镜像
│   └── nginx.conf               # Nginx 配置
├── docker-compose.yml           # 容器编排
├── .env.example                 # 环境变量模板
└── README.md
```

## 注意事项

- **数据库**：Docker 部署使用 PostgreSQL 15，数据持久化到 `postgres_data` 卷
- **文件存储**：上传的发票文件存储在 `backend_data` 卷（`/app/data/uploads`）
- **PDF 处理**：Docker 镜像已包含 `poppler-utils`，无需额外安装
- **本地模型**：需要 GPU 或较强 CPU，否则识别较慢
- **税务查验**：官方查验接口未开放，仅实现查重 + 规则校验
- **端口冲突**：如 80/8000/5432 端口被占用，修改 `docker-compose.yml` 中的端口映射

## 故障排查

### 后端无法连接数据库

```bash
# 检查数据库是否启动
docker-compose ps postgres

# 查看数据库日志
docker-compose logs postgres

# 测试数据库连接
docker-compose exec backend python -c "from app.database import engine; print(engine.connect())"
```

### 前端无法访问

```bash
# 检查 Nginx 配置
docker-compose exec frontend nginx -t

# 查看 Nginx 日志
docker-compose logs frontend
```

### 重新初始化数据库

```bash
# 停止服务并删除数据卷
docker-compose down -v

# 重新启动
docker-compose up -d
```
