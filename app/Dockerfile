FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG APP_VERSION=v1
ARG HEALTH_FORCE_STATUS=200

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_VERSION=${APP_VERSION} \
    HEALTH_FORCE_STATUS=${HEALTH_FORCE_STATUS}

WORKDIR /app
COPY requirements.txt ./
RUN pip install -i "${PIP_INDEX_URL}" -r requirements.txt
COPY service.py ./

EXPOSE 8000
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
