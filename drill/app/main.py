"""演练用的极简会员空间 API（虚构实现，无真实业务数据）。

暴露：
  GET /health   容器/数据库健康探针，可通过 APP_FORCE_UNHEALTHY 注入故障
  GET /version  返回构建期烧入的镜像版本 + 运行时标签，用于校验回滚是否生效

所有配置项只从环境变量读取，代码内不出现地址、账号或密钥。
"""

from __future__ import annotations

import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

APP_NAME = os.environ.get("APP_NAME", "member-space-api")
HEALTH_PATH = os.environ.get("APP_HEALTH_PATH", "/health")
VERSION_PATH = os.environ.get("APP_VERSION_PATH", "/version")
# 构建期烧入的版本（镜像不可变标识），运行期不可改
IMAGE_VERSION = os.environ.get("APP_VERSION_LABEL", "unknown")
# 运行期标签（compose 运行时注入，可改）
RUNTIME_LABEL = os.environ.get("APP_ENV_LABEL", "unlabelled")
FORCE_UNHEALTHY = os.environ.get("APP_FORCE_UNHEALTHY", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "2"))

app = FastAPI(title=APP_NAME, version=IMAGE_VERSION, docs_url=None, redoc_url=None)


def _db_probe() -> tuple[bool, str]:
    if not (DB_HOST and DB_NAME and DB_USER):
        return True, "database not configured, skipped"
    try:
        with psycopg.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=DB_CONNECT_TIMEOUT,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, "database reachable"
    except Exception as exc:  # noqa: BLE001 - 探针要把任何异常转成非健康
        return False, f"database unreachable: {type(exc).__name__}"


@app.get(HEALTH_PATH)
def health() -> JSONResponse:
    if FORCE_UNHEALTHY:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "reason": "fault injected via APP_FORCE_UNHEALTHY",
                "version": IMAGE_VERSION,
            },
        )
    ok, detail = _db_probe()
    if not ok:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "reason": detail, "version": IMAGE_VERSION},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "detail": detail, "version": IMAGE_VERSION},
    )


@app.get(VERSION_PATH)
def version() -> dict[str, str]:
    return {
        "service": APP_NAME,
        "image_version": IMAGE_VERSION,
        "runtime_label": RUNTIME_LABEL,
        "health_path": HEALTH_PATH,
    }
