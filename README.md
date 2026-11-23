# 回滚演练脚本（自动执行应用回滚并计时出报告）

docker compose 环境下把「应用回滚」做成一条可重复的演练：逐步执行、逐步计时、出报告、可失败注入、可 dry-run。

## 环境要求

- Linux + Docker Engine 24+ / docker compose v2
- Python 3.11+（脚本零第三方依赖，仅标准库）
- 端口 18080（nginx 入口）、15432（演练库）空闲

## 快速开始

```bash
# 1. 准备环境变量（仓库只带示例，真实环境变量不入库）
cp .env.example .env            # 按需改端口；口令为演练用一次性虚拟值

# 2. 构建两个版本的镜像（当前版 1.0.0 / 回滚目标 0.2.0）并起演练环境
bash scripts/build_images.sh
bash scripts/up_demo_env.sh
curl -s localhost:18080/version   # 应为当前版本 1.0.0

# 3. 真实回滚演练（成功路径）：出报告并校验总耗时
python3 scripts/rollback_drill.py --report-dir reports/run1
# 报告：reports/run1/report.md、report.json；退出码 0 = 回滚成功且耗时在 SLA 内

# 4. 失败注入：让健康检查步骤失败，验证脚本立即停止且退出码非 0
DRILL_FAIL_STEP=health_check python3 scripts/rollback_drill.py --report-dir reports/fail1
echo $?    # 2 = 在指定步骤中止

# 5. dry-run：只打印命令，不动任何容器
python3 scripts/rollback_drill.py --dry-run --report-dir reports/dry1
```

## 一键复跑全部证据

```bash
bash scripts/run_evidence.sh     # 依次跑 成功 / 失败注入 / dry-run / 硬编码扫描，产物落 EVIDENCE/
```

## 脚本与目录

| 路径 | 说明 |
| --- | --- |
| `scripts/rollback_drill.py` | 演练主脚本：7 步（预检 → 回滚前快照 → 切版本 → compose 重启 → 健康检查 → 版本校验 → 回滚后快照），逐步计时，出 `report.md` / `report.json` |
| `scripts/build_images.sh` | 构建 `1.0.0` 与 `0.2.0` 两个应用镜像 |
| `scripts/up_demo_env.sh` | 起演练环境（postgres 16 + api + nginx）并等健康 |
| `scripts/reset_to_current.sh` | 把环境重置回当前版本，供失败注入 / dry-run 前复位 |
| `scripts/run_evidence.sh` | 一次跑通四条证据路径，汇总到 `EVIDENCE/` |
| `scripts/scan_hardcoded_values.sh` | 硬编码扫描：地址 / 账号 / 密钥，逐条分类 allowlist，退出码即判定 |
| `drill/docker-compose.yml` | 演练编排：`postgres:16-alpine` + 应用镜像 + `nginx:1.27-alpine` |
| `drill/app/` | 演练应用（FastAPI，暴露 `/health`、`/version`，故障注入开关 `APP_FORCE_UNHEALTHY`） |
| `EVIDENCE/` | 真跑证据：四路径的 stdout / 报告 / `docker ps` 前后快照 / 扫描清单 |

## 环境变量（全部可覆盖，代码无字面量）

镜像与版本：`APP_IMAGE_REPO`、`APP_CURRENT_TAG`、`APP_ROLLBACK_TAG`、`APP_BASE_IMAGE`、`POSTGRES_IMAGE`、`NGINX_IMAGE`
编排：`COMPOSE_PROJECT_NAME`、`COMPOSE_FILE`、`APP_NAME`、`APP_ENV_LABEL`、`APP_PORT`*、`APP_BIND_HOST`*、`API_UPSTREAM_HOST`（带 `*` 为必填：`${VAR:?}` 强制注入，缺了容器直接启动失败）；端口映射：`NGINX_HTTP_PORT`、`NGINX_LISTEN_PORT`
探活：`DRILL_API_BASE_URL`、`HEALTH_CHECK_PATH`、`VERSION_CHECK_PATH`、`HEALTH_CHECK_TIMEOUT_SECONDS`、`HEALTH_CHECK_INTERVAL_SECONDS`
数据库：`POSTGRES_HOST`、`POSTGRES_PORT_INNER`、`POSTGRES_USER`、`POSTGRES_DB`、`POSTGRES_PASSWORD`、`DB_CONNECT_TIMEOUT`
演练行为：`ROLLBACK_SLA_SECONDS`（默认 900）、`DRILL_FAIL_STEP`（留空=不注入）、`REPORT_DIR`

## 收尾

```bash
docker compose -p member-space-drill -f drill/docker-compose.yml down   # 停演练环境
```
