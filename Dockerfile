# ── 构建阶段：安装 Python 依赖 ──────────────────────────────
FROM python:3.12-alpine AS builder

# 编译 pycryptodome 需要 gcc / musl-dev
RUN apk add --no-cache gcc musl-dev

WORKDIR /install

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install/deps -r requirements.txt

# ── 运行阶段：最小镜像 ────────────────────────────────────────
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 从构建阶段只复制已安装的包
COPY --from=builder /install/deps /usr/local

# 复制项目文件（.dockerignore 已过滤无用内容）
COPY . .

EXPOSE 8880

CMD ["python", "main.py"]