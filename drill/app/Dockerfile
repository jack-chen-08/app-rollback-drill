# 基础镜像由构建参数注入，便于在受限网络下指向内部镜像源；仓库里不写死具体镜像仓库地址
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# 版本标签在构建期烧进镜像，用于证明"回滚后跑的是另一份镜像"
ARG APP_VERSION_LABEL=0.0.0
ENV APP_VERSION_LABEL=${APP_VERSION_LABEL} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

EXPOSE 8000

# 监听地址与端口必须由环境注入：缺了就启动失败，而不是悄悄用一个内置默认地址
CMD ["sh", "-c", "exec uvicorn main:app --host \"${APP_BIND_HOST:?APP_BIND_HOST must be injected}\" --port \"${APP_PORT:?APP_PORT must be injected}\""]
