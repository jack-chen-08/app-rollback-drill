# 应用回滚演练报告 / Rollback Drill Report

- 生成时间：2026-11-22 20:05:31
- 执行模式：实跑
- 脚本：`scripts/drill_rollback.py`

## 1. 环境与关键参数（敏感值已打码）

| 变量 | 值 |
|---|---|
| `API_BASE_URL` | `http://127.0.0.1:18080` |
| `WEB_BASE_URL` | `http://127.0.0.1:18081` |
| `API_IMAGE` | `member-space-api` |
| `GOOD_TAG` | `v1` |
| `BAD_TAG` | `v2` |
| `REDIS_PREFIX_CLEAN` | `member-space:` |
| `FRONTEND_RELEASE` | `./frontend/releases/v1` |
| `MAX_ROLLBACK_SECONDS` | `900` |
| `SMOKE_USERNAME` | `drill_user` |
| `SMOKE_ACTIVITY_ID` | `11111111-1111-1111-1111-111111111111` |
| `APP_SECRET` | `***` |
| `POSTGRES_PASSWORD` | `***` |
| `SMOKE_PASSWORD` | `***` |

## 2. 准备阶段（不计入回滚耗时）

| # | 步骤 | 开始 | 结束 | 耗时(s) | 结果 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 记录基线版本并跑冒烟 | 2026-11-22 20:05:07 | 2026-11-22 20:05:07 | 0.05 | ok | version=v1, smoke 5/5 passed |
| 2 | 部署缺陷版本并等待告警 | 2026-11-22 20:05:07 | 2026-11-22 20:05:11 | 4.26 | ok | 告警触发 2026-11-22T12:05:11.750Z（HTTP 500） |

## 3. 回滚时间线（计时起点 = 告警触发时刻）

| # | 步骤 | 开始 | 结束 | 耗时(s) | 结果 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 切换上一镜像标签 | 2026-11-22 20:05:11 | 2026-11-22 20:05:15 | 3.32 | ok | - |
| 2 | 滚动重启（健康检查通过才继续） | 2026-11-22 20:05:15 | 2026-11-22 20:05:19 | 4.65 | ok | - |
| 3 | 恢复前端静态包 | 2026-11-22 20:05:19 | 2026-11-22 20:05:31 | 11.71 | ok | - |
| 4 | 按前缀清理 Redis 缓存键 | 2026-11-22 20:05:31 | 2026-11-22 20:05:31 | 0.00 | failed | forced failure injected via --force-fail clean_redis |
| 5 | 运行 5 条冒烟用例 | - | - | 0.00 | pending | - |

## 4. 回滚总耗时

- 告警触发时刻：2026-11-22 20:05:11
- 回滚完成时刻：-
- 总耗时：**未完成**（fail-fast 于 `clean_redis`；上限 900 s = 15 min）
- 结论：**FAIL**（退出码 1，回滚未走完全链路）

## 5. 冒烟用例（准备阶段基线冒烟；回滚后冒烟未执行，因 fail-fast）

| 用例 | 结果 | 耗时(ms) | 说明 |
|---|---|---|---|
| 健康检查 /health | PASS | 4.2 | HTTP 200 |
| 登录 /api/login | PASS | 9.7 | HTTP 200 |
| 会员列表 /api/members | PASS | 4.9 | HTTP 200, items=5 |
| 活动详情 /api/activities/{id} | PASS | 5.3 | HTTP 200 |
| 知识库检索 /api/kb/search | PASS | 5.7 | HTTP 200, hits=3 |

## 6. 失败定位

- 失败步骤：**clean_redis**（按前缀清理 Redis 缓存键），时间 2026-11-22 20:05:31
- 错误：forced failure injected via --force-fail clean_redis

容器日志片段（tail）：

```text
api-1  | INFO:     Started server process [1]
api-1  | INFO:     Waiting for application startup.
api-1  | INFO:     Application startup complete.
api-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
api-1  | INFO:     172.19.0.6:54348 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     127.0.0.1:56360 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.1:50766 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:54354 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:54358 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     127.0.0.1:56362 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:54366 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     127.0.0.1:37776 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:49370 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:49376 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     127.0.0.1:37788 - "GET /health HTTP/1.1" 200 OK
api-1  | INFO:     172.19.0.6:49382 - "GET /health HTTP/1.1" 200 OK
```

## 7. 执行过的命令（逐步）

- **deploy_defective**
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --pull never api   (API_TAG=v2)`
  - `ln -sfn releases/v2 frontend/current`
- **switch_image_tag**
  - `docker image inspect member-space-api:v1 --format {{.Id}}`
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --pull never api   (API_TAG=v1)`
- **rolling_restart**
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --force-recreate --wait api   (API_TAG=v1)`
- **restore_frontend**
  - `ln -sfn releases/v1 frontend/current   (relative atomic swap)`
  - `docker compose -f docker-compose.yml -p rollback-drill restart web`

## 8. 验收标准对照

| AC | 判据 | 本次结果 |
|---|---|---|
| 1 | 回滚总耗时 ≤ 15 分钟 | 0.00 s → FAIL |
| 2 | 每步含开始/结束/耗时/结果 | PASS（6 步） |
| 3 | 某步失败即停、退出码非 0、报告标出失败步骤 | PASS（退出码 1，失败步骤 clean_redis，本报告即证据） |
| 4 | --dry-run 只打印命令，不改容器状态 | PASS（`bash scripts/prove_dry_run.sh` 采集 before/after `docker ps`，结果见 `reports/dry_run_docker_ps_diff.txt`） |
| 5 | 脚本无硬编码地址/账号/密钥 | PASS（全部来自环境变量，敏感值已打码） |

> 本次用 `--force-fail clean_redis` 注入失败，用于验证失败即停与报告定位能力。
