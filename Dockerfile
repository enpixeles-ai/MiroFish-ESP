FROM python:3.11

# 安装 Node.js （满足 >=18）及必要工具
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm \
  && rm -rf /var/lib/apt/lists/*

# 从 uv 官方镜像复制 uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# 先复制依赖描述文件以利用缓存
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# 安装依赖（Node + Python）
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# 复制项目源码
COPY . .

ENV FLASK_DEBUG=false
ENV NODE_ENV=production

RUN npm run build --prefix frontend

EXPOSE 3000 5001

# Iniciar backend y frontend compilado
CMD ["npm", "run", "start"]
