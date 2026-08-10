FROM python:3.13-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY backend.py .
COPY templates/ templates/

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8899}/api/health || exit 1

# 暴露端口（云平台会通过 PORT 环境变量注入）
EXPOSE 8899

CMD python3 -m uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8899}
