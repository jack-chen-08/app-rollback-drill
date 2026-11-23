# 应用回滚演练报告

> 模式：**真实演练**（故障注入：未启用）

## 结论：✅ **回滚成功**

| 指标 | 值 |
| --- | --- |
| 回滚总耗时 | **4.08 秒** |
| SLA（≤ 900 秒 / 15 分钟） | ✅ 达标 |
| 演练开始 | 2026-11-24T01:16:27.276+08:00 |
| 演练结束 | 2026-11-24T01:16:31.350+08:00 |
| 失败步骤 | 无 |
| 退出码（预期） | 0 |
| 镜像仓库 | member-space-drill/app |
| 版本变化 | 1.0.0 → 0.2.0 |
| 演练入口 | http://127.0.0.1:18080 |

## 步骤明细

| # | 步骤 | 开始时间 | 结束时间 | 耗时(秒) | 结果 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `preflight`<br>预检（环境变量 / 镜像标签 / compose 解析） | 2026-11-24T01:16:27.276+08:00 | 2026-11-24T01:16:27.536+08:00 | 0.26 | ✅ 成功 | 环境变量齐备；镜像 member-space-drill/app:1.0.0 与 member-space-drill/app:0.2.0 均存在；compose 解析通过；故障注入=未启用 |
| 2 | `snapshot_before`<br>记录回滚前状态（docker ps / compose ps / version） | 2026-11-24T01:16:27.536+08:00 | 2026-11-24T01:16:27.902+08:00 | 0.37 | ✅ 成功 | 回滚前版本 image_version=1.0.0 runtime_label=drill-a；主机可见容器 14 个 |
| 3 | `switch_version`<br>切镜像版本（渲染并校验目标镜像引用） | 2026-11-24T01:16:27.903+08:00 | 2026-11-24T01:16:28.003+08:00 | 0.10 | ✅ 成功 | 目标镜像引用已确认为 member-space-drill/app:0.2.0 |
| 4 | `compose_up`<br>应用变更（docker compose up -d） | 2026-11-24T01:16:28.003+08:00 | 2026-11-24T01:16:28.972+08:00 | 0.97 | ✅ 成功 | 已应用镜像 0.2.0，容器动作: Container member-space-drill-api-1  Recreate; Container member-space-drill-api-1  Recreated; Container member-space-drill-api-1  Starting; Container member-space-drill-api-1  Started |
| 5 | `health_check`<br>健康检查轮询（/health） | 2026-11-24T01:16:28.972+08:00 | 2026-11-24T01:16:31.002+08:00 | 2.03 | ✅ 成功 | 健康检查通过，共轮询 2 次 |
| 6 | `verify_version`<br>校验版本（/version 应为回滚目标） | 2026-11-24T01:16:31.002+08:00 | 2026-11-24T01:16:31.005+08:00 | 0.00 | ✅ 成功 | 版本已由 1.0.0 变为 0.2.0，回滚生效 |
| 7 | `snapshot_after`<br>记录回滚后状态（docker ps / compose ps / version） | 2026-11-24T01:16:31.005+08:00 | 2026-11-24T01:16:31.350+08:00 | 0.34 | ✅ 成功 | 已记录回滚后状态；主机可见容器 14 个 |
| 8 | `report`<br>生成报告（JSON + Markdown） | 2026-11-24T01:16:31.350+08:00 | 2026-11-24T01:16:31.351+08:00 | 0.00 | ✅ 成功 | 已写出 report.json 与 report.md -> app-rollback-drill/EVIDENCE/01_rollback_success |

步骤耗时合计（含报告生成）：4.08 秒

## 版本校验

| 时点 | image_version | runtime_label |
| --- | --- | --- |
| 回滚前 | 1.0.0 | drill-a |
| 回滚后 | 0.2.0 | drill-a |

## 健康检查轮询记录

| 次数 | 时间 | HTTP | 结果 |
| --- | --- | --- | --- |
| 1 | 2026-11-24T01:16:28.974+08:00 | 502 | ❌ |
| 2 | 2026-11-24T01:16:31.002+08:00 | 200 | ✅ |

## 容器状态（回滚前）

```text
NAMES                           IMAGE                                           STATUS                      PORTS
member-space-drill-api-1        member-space-drill/app:1.0.0                    Up 1 second                 8000/tcp
member-space-drill-nginx-1      docker.1panel.live/library/nginx:1.27-alpine    Up 32 minutes               80/tcp, 0.0.0.0:18080->8080/tcp, [::]:18080->8080/tcp
member-space-drill-postgres-1   docker.1panel.live/library/postgres:16-alpine   Up 32 minutes (healthy)     5432/tcp
other-project-pg-3                        docker.1panel.live/library/postgres:16-alpine   Up 34 minutes (healthy)     0.0.0.0:55433->5432/tcp, [::]:55433->5432/tcp
other-project-svc-2                    ad10ca536106                                    Exited (1) 40 minutes ago   
other-project-pg-1                       pgvector/pgvector:pg16                          Up 45 minutes (healthy)     0.0.0.0:55432->5432/tcp, [::]:55432->5432/tcp
rollback-drill-api-1            member-space-api:v1                             Exited (0) 5 hours ago      
rollback-drill-watchdog-1       python:3.12-slim                                Up 5 hours                  
rollback-drill-redis-1          docker.1panel.live/library/redis:7-alpine       Up 5 hours (healthy)        6379/tcp
rollback-drill-web-1            python:3.12-slim                                Up 5 hours (healthy)        0.0.0.0:18081->8080/tcp, [::]:18081->8080/tcp
rollback-drill-db-1             pgvector/pgvector:pg16                          Up 5 hours (healthy)        5432/tcp
other-project-pg-2                       pgvector/pgvector:pg16                          Exited (0) 5 hours ago      
other-project-svc-1       de9bfd9b32f0                                    Exited (0) 3 days ago       
other-project-db-1        pgvector/pgvector:pg16                          Exited (0) 5 hours ago
```

## 容器状态（回滚后）

```text
NAMES                           IMAGE                                           STATUS                      PORTS
member-space-drill-api-1        member-space-drill/app:0.2.0                    Up 2 seconds                8000/tcp
member-space-drill-nginx-1      docker.1panel.live/library/nginx:1.27-alpine    Up 32 minutes               80/tcp, 0.0.0.0:18080->8080/tcp, [::]:18080->8080/tcp
member-space-drill-postgres-1   docker.1panel.live/library/postgres:16-alpine   Up 32 minutes (healthy)     5432/tcp
other-project-pg-3                        docker.1panel.live/library/postgres:16-alpine   Up 34 minutes (healthy)     0.0.0.0:55433->5432/tcp, [::]:55433->5432/tcp
other-project-svc-2                    ad10ca536106                                    Exited (1) 40 minutes ago   
other-project-pg-1                       pgvector/pgvector:pg16                          Up 45 minutes (healthy)     0.0.0.0:55432->5432/tcp, [::]:55432->5432/tcp
rollback-drill-api-1            member-space-api:v1                             Exited (0) 5 hours ago      
rollback-drill-watchdog-1       python:3.12-slim                                Up 5 hours                  
rollback-drill-redis-1          docker.1panel.live/library/redis:7-alpine       Up 5 hours (healthy)        6379/tcp
rollback-drill-web-1            python:3.12-slim                                Up 5 hours (healthy)        0.0.0.0:18081->8080/tcp, [::]:18081->8080/tcp
rollback-drill-db-1             pgvector/pgvector:pg16                          Up 5 hours (healthy)        5432/tcp
other-project-pg-2                       pgvector/pgvector:pg16                          Exited (0) 5 hours ago      
other-project-svc-1       de9bfd9b32f0                                    Exited (0) 3 days ago       
other-project-db-1        pgvector/pgvector:pg16                          Exited (0) 5 hours ago
```

## 执行过的命令

```text
rc=0  (0.04s)  docker version --format '{{.Server.Version}}'
rc=0  (0.06s)  docker image inspect member-space-drill/app:1.0.0
rc=0  (0.05s)  docker image inspect member-space-drill/app:0.2.0
rc=0  (0.12s)  APP_IMAGE=member-space-drill/app:1.0.0 APP_IMAGE_TAG=1.0.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill config -q
rc=0  (0.14s)  docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill ps -a
rc=0  (0.11s)  docker ps -a --format 'table {{.Names}}	{{.Image}}	{{.Status}}	{{.Ports}}'
rc=0  (0.11s)  docker ps -a --format '{{.Names}}	{{.Image}}	{{.Status}}'
rc=0  (0.10s)  APP_IMAGE=member-space-drill/app:0.2.0 APP_IMAGE_TAG=0.2.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill config --format json
rc=0  (0.97s)  APP_IMAGE=member-space-drill/app:0.2.0 APP_IMAGE_TAG=0.2.0 docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill up -d --no-deps api
rc=0  (0.13s)  docker compose -f app-rollback-drill/drill/docker-compose.yml --env-file app-rollback-drill/.env --project-name member-space-drill ps -a
rc=0  (0.11s)  docker ps -a --format 'table {{.Names}}	{{.Image}}	{{.Status}}	{{.Ports}}'
rc=0  (0.10s)  docker ps -a --format '{{.Names}}	{{.Image}}	{{.Status}}'
```

---
本报告由 scripts/rollback_drill.py 自动生成；环境变量与凭据均不落盘。
