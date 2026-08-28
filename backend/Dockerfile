FROM python:3.11-slim

# 安装系统依赖
# - poppler-utils: PDF 转图片
# - libgl1 / libglib2.0-0: opencv-python 依赖（RapidOCR 间接依赖）
# - libxcb1: opencv-python GUI 后端依赖（即便 headless 也常被链接）
# - libgomp1: onnxruntime 多线程支持
RUN apt-get update && apt-get install -y \
    poppler-utils \
    curl \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（不再需要 paddlepaddle / paddleocr）
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/uploads

# ======================================================================
# 预下载 RapidOCR 模型（避免首次运行时下载失败或延迟）
# 模型默认缓存在 /root/.rapidocr
# 包含：detection.onnx + recognition.onnx + cls.onnx + 字典文件
# ======================================================================
RUN python -c "from rapidocr_onnxruntime import RapidOCR; \
    print('开始预下载 RapidOCR 模型...'); \
    engine = RapidOCR(); \
    print('RapidOCR 模型预下载完成')"

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
