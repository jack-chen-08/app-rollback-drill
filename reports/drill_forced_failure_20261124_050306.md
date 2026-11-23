# 应用回滚演练报告 / Rollback Drill Report

- 生成时间：2026-11-24 05:03:06
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
| 1 | 记录基线版本并跑冒烟 | 2026-11-24 05:02:43 | 2026-11-24 05:02:43 | 0.06 | ok | version=v1, smoke 5/5 passed |
| 2 | 部署缺陷版本并等待告警 | 2026-11-24 05:02:43 | 2026-11-24 05:02:46 | 3.31 | ok | 告警触发 2026-11-24 05:02:46（本地时间；原始 2026-11-23T21:02:46.651Z，连接失败（API 未应答）） |

## 3. 回滚时间线（计时起点 = 告警触发时刻）

| # | 步骤 | 开始 | 结束 | 耗时(s) | 结果 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 切换上一镜像标签 | 2026-11-24 05:02:46 | 2026-11-24 05:02:50 | 3.28 | ok | image member-space-api:v1 id=sha256:f3942, /api/version 已是 v1 |
| 2 | 滚动重启（健康检查通过才继续） | 2026-11-24 05:02:50 | 2026-11-24 05:02:54 | 4.80 | ok | recreated with --wait; /health 已恢复 200 |
| 3 | 恢复前端静态包 | 2026-11-24 05:02:54 | 2026-11-24 05:03:06 | 11.80 | ok | frontend 静态包已切回 v1（本地与 HTTP 双向校验） |
| 4 | 按前缀清理 Redis 缓存键 | 2026-11-24 05:03:06 | 2026-11-24 05:03:06 | 0.00 | failed | forced failure injected via --force-fail clean_redis |
| 5 | 运行 5 条冒烟用例 | - | - | 0.00 | pending | - |

## 4. 回滚总耗时

- 告警触发时刻：2026-11-24 05:02:46
- 回滚完成时刻：-
- 总耗时：**未完成**（fail-fast 于 `clean_redis`；上限 900 s = 15 min）
- 结论：**FAIL**（退出码 1，回滚未走完全链路）

## 5. 冒烟用例（准备阶段基线冒烟；回滚后冒烟未执行，因 fail-fast）

| 用例 | 结果 | 耗时(ms) | 说明 |
|---|---|---|---|
| 健康检查 /health | PASS | 1.7 | HTTP 200 |
| 登录 /api/login | PASS | 6.9 | HTTP 200 |
| 会员列表 /api/members | PASS | 13.9 | HTTP 200, items=5 |
| 活动详情 /api/activities/{id} | PASS | 8.2 | HTTP 200 |
| 知识库检索 /api/kb/search | PASS | 8.9 | HTTP 200, hits=3 |

## 6. 回滚后终态核对

（fail-fast，未走到终态核对）

## 7. 失败定位

- 失败步骤：**clean_redis**（按前缀清理 Redis 缓存键），时间 2026-11-24 05:03:06
- 错误：forced failure injected via --force-fail clean_redis

容器日志片段（tail）：

```text
[service: redis]
redis-1  | 1:C 23 Nov 2026 21:02:30.124 # WARNING Memory overcommit must be enabled! Without it, a background save or replication may fail under low memory condition. Being disabled, it can also cause failures without low memory condition, see https://github.com/jemalloc/jemalloc/issues/1328. To fix this issue add 'vm.overcommit_memory = 1' to /etc/sysctl.conf and then reboot or run the command 'sysctl vm.overcommit_memory=1' for this to take effect.
redis-1  | 1:C 23 Nov 2026 21:02:30.125 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
redis-1  | 1:C 23 Nov 2026 21:02:30.125 * Redis version=7.4.12, bits=64, commit=00000000, modified=0, pid=1, just started
redis-1  | 1:C 23 Nov 2026 21:02:30.125 * Configuration loaded
redis-1  | 1:M 23 Nov 2026 21:02:30.125 * Increased maximum number of open files to 10032 (it was originally set to 1024).
redis-1  | 1:M 23 Nov 2026 21:02:30.125 * monotonic clock: POSIX clock_gettime
redis-1  | 1:M 23 Nov 2026 21:02:30.126 * Running mode=standalone, port=6379.
redis-1  | 1:M 23 Nov 2026 21:02:30.127 * Server initialized
redis-1  | 1:M 23 Nov 2026 21:02:30.127 * Ready to accept connections tcp
```

## 8. 执行过的命令（逐步）

- **deploy_defective**
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --pull never api   (API_TAG=v2)`
  - `ln -sfn releases/v2 frontend/current   (relative atomic swap)`
  - `wait: GET http://127.0.0.1:18080/health != 200 AND new 'alert_fired' line in run/alerts/alerts.log (timeout 120.0s)`
- **switch_image_tag**
  - `docker image inspect member-space-api:v1 --format {{.Id}}`
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --pull never api   (API_TAG=v1)`
- **rolling_restart**
  - `docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --force-recreate --wait api   (API_TAG=v1)`
- **restore_frontend**
  - `ln -sfn releases/v1 frontend/current   (relative atomic swap)`
  - `docker compose -f docker-compose.yml -p rollback-drill restart web`

## 9. 验收标准对照

| AC | 判据 | 本次结果 |
|---|---|---|
| 1 | 回滚总耗时 ≤ 15 分钟 | 0.00 s → FAIL |
| 2 | 每步含开始/结束/耗时/结果 | PASS（6 步） |
| 3 | 某步失败即停、退出码非 0、报告标出失败步骤 | PASS（退出码 1，失败步骤 clean_redis，本报告即证据） |
| 4 | --dry-run 只打印命令，不改容器状态 | PASS（`scripts/prove_dry_run.sh` 采集 before/after 原始 `docker ps -a` 与 `Id/Name/Image/StartedAt` 指纹，原始输出已提交为 `reports/dry_run_docker_ps_before.txt` 与 `reports/dry_run_docker_ps_after.txt`，比对结果见 `reports/dry_run_diff.txt` 与 `reports/dry_run_verdict.txt`） |
| 5 | 脚本无硬编码地址/账号/密钥 | PASS（全部来自环境变量，敏感值已打码） |

> 本次用 `--force-fail clean_redis` 注入失败，用于验证失败即停与报告定位能力。
