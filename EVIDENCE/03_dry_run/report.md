# 应用回滚演练报告

> 模式：**--dry-run（仅打印命令，未改变任何容器状态）**

## 结论：ℹ️ **演练计划预览（dry-run，未执行）**

| 指标 | 值 |
| --- | --- |
| 回滚总耗时 | **0.00 秒** |
| SLA（≤ 900 秒 / 15 分钟） | —（dry-run） |
| 演练开始 | 2026-11-24T01:16:59.706+08:00 |
| 演练结束 | 2026-11-24T01:16:59.707+08:00 |
| 失败步骤 | 无 |
| 退出码（预期） | 0 |
| 镜像仓库 | member-space-drill/app |
| 版本变化 | 1.0.0 → 0.2.0 |
| 演练入口 | http://127.0.0.1:18080 |

## 步骤明细

| # | 步骤 | 开始时间 | 结束时间 | 耗时(秒) | 结果 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `preflight`<br>预检（环境变量 / 镜像标签 / compose 解析） | 2026-11-24T01:16:59.706+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 预检项: APP_IMAGE_REPO, APP_CURRENT_TAG, APP_ROLLBACK_TAG, DRILL_API_BASE_URL, NGINX_HTTP_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, COMPOSE_FILE + 两个镜像标签 + compose 解析（dry-run 不做实际校验） |
| 2 | `snapshot_before`<br>记录回滚前状态（docker ps / compose ps / version） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：采集回滚前 docker ps / compose ps / version |
| 3 | `switch_version`<br>切镜像版本（渲染并校验目标镜像引用） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：以 APP_IMAGE_TAG=0.2.0 渲染 compose 并校验镜像引用为 member-space-drill/app:0.2.0 |
| 4 | `compose_up`<br>应用变更（docker compose up -d） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：应用镜像 0.2.0 到 api 服务 |
| 5 | `health_check`<br>健康检查轮询（/health） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：轮询 /health，超时 60 秒 |
| 6 | `verify_version`<br>校验版本（/version 应为回滚目标） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：校验 /version 返回 0.2.0 |
| 7 | `snapshot_after`<br>记录回滚后状态（docker ps / compose ps / version） | 2026-11-24T01:16:59.707+08:00 | 2026-11-24T01:16:59.707+08:00 | 0.00 | ⏭️ 计划（未执行） | 计划：采集回滚后 docker ps / compose ps / version |
| 8 | `report`<br>生成报告（JSON + Markdown） | 2026-11-24T01:16:59.707+08:00 | — | 0.00 | ⏸️ 未执行 |  |

步骤耗时合计（含报告生成）：0.00 秒

## 版本校验

| 时点 | image_version | runtime_label |
| --- | --- | --- |
| 回滚前 | — | — |
| 回滚后 | — | — |

## 容器状态（回滚前）

```text
(未采集)
```

## 容器状态（回滚后）

```text
(未采集)
```

## 执行过的命令

```text
rc=dry-run  (0.00s)  docker version --format '{{.Server.Version}}'
rc=dry-run  (0.00s)  docker image inspect member-space-drill/app:1.0.0
rc=dry-run  (0.00s)  docker image inspect member-space-drill/app:0.2.0
rc=dry-run  (0.00s)  APP_IMAGE=member-space-drill/app:1.0.0 APP_IMAGE_TAG=1.0.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill config -q
rc=dry-run  (0.00s)  docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill ps -a
rc=dry-run  (0.00s)  docker ps -a --format 'table {{.Names}}	{{.Image}}	{{.Status}}	{{.Ports}}'
rc=dry-run  (0.00s)  docker ps -a --format '{{.Names}}	{{.Image}}	{{.Status}}'
rc=dry-run  (0.00s)  APP_IMAGE=member-space-drill/app:0.2.0 APP_IMAGE_TAG=0.2.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill config --format json
rc=dry-run  (0.00s)  APP_IMAGE=member-space-drill/app:0.2.0 APP_IMAGE_TAG=0.2.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill up -d --no-deps api
rc=dry-run  (0.00s)  docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill ps -a
rc=dry-run  (0.00s)  docker ps -a --format 'table {{.Names}}	{{.Image}}	{{.Status}}	{{.Ports}}'
rc=dry-run  (0.00s)  docker ps -a --format '{{.Names}}	{{.Image}}	{{.Status}}'
```

---
本报告由 scripts/rollback_drill.py 自动生成；环境变量与凭据均不落盘。
