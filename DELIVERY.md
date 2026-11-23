# 交付说明 · 回滚演练脚本（自动执行应用回滚并计时出报告）

- 交付形态：本地 git 仓库（未 push、未提交任何平台）
- 技术栈：Python 3.11（脚本零第三方依赖）/ Docker Engine 与 docker compose v2 / PostgreSQL 16-alpine / nginx 1.27-alpine / FastAPI（仅作被演练的应用）
- 数据：全部虚构（虚构口令、虚构库名 `member_space_drill`、虚构环境标签 `drill-a`），不含客户名称、真实账号或真实个人信息
- 密钥：只从环境变量读取；`.env` 不入库（`.gitignore`），仓库内只带 `.env.example`

## 1. 交付物

| 路径 | 说明 |
| --- | --- |
| `scripts/rollback_drill.py` | 演练主脚本：7 步逐段计时（预检 → 回滚前快照 → 切镜像版本 → compose 重启 → 健康检查 → 版本校验 → 回滚后快照），输出 `report.md` + `report.json`，退出码 0 成功 / 2 在某步中止 |
| `scripts/run_evidence.sh` | 一键复跑四条证据路径（成功 / 失败注入 / dry-run / 硬编码扫描） |
| `scripts/build_images.sh`、`up_demo_env.sh`、`reset_to_current.sh` | 演练环境的构建、起停与复位 |
| `scripts/scan_hardcoded_values.sh` | 硬编码扫描（地址 / IP / 内嵌凭据 / 密钥），逐条分类 allowlist |
| `drill/docker-compose.yml`、`drill/app/`、`drill/nginx/` | 演练编排 + 被演练应用（`/health`、`/version`，`APP_FORCE_UNHEALTHY` 故障开关） |
| `EVIDENCE/` | 真跑证据：三条路径的 stdout / 报告 / `docker ps` 前后快照、扫描清单 |
| `README.md` | 可复制命令：构建、起环境、三条演练路径、一键复跑、收尾 |

## 2. AC 对照（标准 → 命令 → 证据）

| 验收标准 | 可复制命令 | 实测结果 | 证据文件 |
| --- | --- | --- | --- |
| AC1 回滚总耗时 ≤ 15 分钟，报告含每步开始/结束/耗时/结果 | `python3 scripts/rollback_drill.py --report-dir reports/run1` | **回滚总耗时 4.08 秒**，SLA（900 秒）✅ 达标，退出码 0 | `EVIDENCE/01_rollback_success/report.md`、`report.json`、`exit_code.txt` |
| AC1 细分：每一步的开始时间 / 结束时间 / 耗时 / 结果 | 同上 | 7 步齐全，表头 `# / 步骤 / 开始时间 / 结束时间 / 耗时(秒) / 结果 / 说明`，例：`preflight` 0.27s、`snapshot_before` 0.36s、`switch_version` 0.10s | `EVIDENCE/01_rollback_success/report.md` 步骤表 |
| AC2 某步失败时立即停止、退出码非 0、报告标出失败步骤 | `DRILL_FAIL_STEP=health_check python3 scripts/rollback_drill.py --report-dir reports/fail1` | 退出码 **2**；报告结论「演练失败：步骤 `health_check` 未通过」，失败行标注 **❌ 失败（health_check）**，后续步骤标「因 health_check 失败而跳过」，附健康检查 11 次轮询与最后一次 HTTP 503 原文 | `EVIDENCE/02_failure_injection/report.md`、`report.json`、`exit_code.txt`（内容 `2`） |
| AC3 dry-run 只打印命令、不改变容器状态 | `python3 scripts/rollback_drill.py --dry-run --report-dir reports/dry1` | 前后本项目 `docker ps` diff **退出码 0**，且**容器指纹**（容器 ID / 启动时刻 / 重启次数 / 镜像 ID）diff **退出码 0**——没有任何容器被重建或重启；本项目容器 3/3 前后不变，dry-run 退出码 0 | `EVIDENCE/03_dry_run/host_docker_ps_before.txt`、`host_docker_ps_after.txt`、`project_before.txt`、`project_after.txt`、`fingerprint_before.txt`、`fingerprint_after.txt`、`diff_verdict.txt` |
| AC4 地址 / 账号 / 密钥不写死，全部环境变量注入 | `bash scripts/scan_hardcoded_values.sh` | **代码与编排 0 条命中**（退出码 0）：`scripts/**`、`Dockerfile`、`docker-compose.yml`、nginx 模板里没有地址 / IP / 内嵌凭据 / 密钥字面量。地址只出现在 `.env.example`（配置面，逐行列出供核对）。`APP_BIND_HOST` 与 `APP_PORT` 改为**强制注入**（`${VAR:?}`），缺失即启动失败，而不是退回内置默认地址 | `EVIDENCE/hardcode_scan.txt` |
| AC4 追加核验：没有默认地址兜底（不是「扫不出来」而是「真的没有」） | `docker run --rm --entrypoint sh -e APP_BIND_HOST= member-space-drill/app:1.0.0 -c 'exec uvicorn main:app --host "${APP_BIND_HOST:?}" --port 8000'` | 退出码 **2**，报错原文 `APP_BIND_HOST must be injected`——地址缺失时容器拒绝启动，不会静默绑到内置地址 | `EVIDENCE/04_no_default_guard/stdout.log`、`exit_code.txt`、`verdict.txt` |

> 关于 dry-run 判定口径：以**本项目**（`project=member-space-drill`）容器前后完全一致为准，并叠加**容器指纹**（ID / 启动时刻 / 重启次数 / 镜像 ID）比对——列表相同但被重启过也会被抓出来。全机 `docker ps` 列表另列作旁证：同机其它项目的容器状态跳动（healthy 翻转等）与本演练无关，不应算作本脚本的副作用。证据文件同时给了全机与本项目两份快照，可自行复核。

## 3. 真实输出（节选）

```
== 路径 1/3：真实回滚（成功路径）
退出码=0  耗时=回滚总耗时 4.08 秒

== 路径 2/3：失败注入（DRILL_FAIL_STEP=health_check）
退出码=2

== 路径 3/3：--dry-run（前后 docker ps 对比）
本项目（project=member-space-drill）前后 diff 退出码=0（0=完全一致）
容器指纹（ID/启动时刻/重启次数/镜像ID）前后 diff 退出码=0
判定：PASS —— dry-run 未改变本项目任何容器（ps 列表与容器指纹均逐字节一致）

== 硬编码扫描
扫描退出码=0
## 统计：A 类命中 0 条（环境变量注入 1 条已排除）；B 类仅列示不阻断
## 判定：PASS —— 代码与编排里没有写死的地址 / 账号 / 密钥

== 无默认地址守卫
退出码=2（非 0 即符合预期：缺地址时拒绝启动）
判定：PASS —— 缺 APP_BIND_HOST 时启动被拒绝，报错点名该变量，没有静默使用内置默认地址
```

## 4. 复跑与复位

```bash
cp .env.example .env && bash scripts/build_images.sh && bash scripts/up_demo_env.sh
bash scripts/run_evidence.sh          # 全部路径 + 扫描 + 守卫一次跑通，产物落 EVIDENCE/
docker compose -p member-space-drill -f drill/docker-compose.yml down   # 收尾
```

## 5. 返工回应（针对上一轮 3 条意见）

| # | 上一轮意见 | 本轮状态 | 落地位置 / 验证 |
| --- | --- | --- | --- |
| 1 | 报告里的 `docker ps` 是摘要不是原始输出，改前改后无法比对；要附原始输出 | **已闭环** | `EVIDENCE/03_dry_run/` 下同时给 4 份原始输出：`docker_ps_before/after.txt`（本项目 filter）、`host_docker_ps_before/after.txt`（全机旁证）、`compose_ps_before/after.txt`，外加 `fingerprint_before/after.txt`（含容器 ID 哈希、镜像、状态、启动时间）与 `project_diff.txt` / `fingerprint_diff.txt` / `host_docker_ps_diff.txt`；判定口径写死为「本项目 diff = 0」——全机 diff 只作旁证，避免同机其它容器状态翻转造成假失败。 |
| 2 | dry-run 没把要执行的命令全打出来（`ln -sfn`、`API_TAG=v2` 那步缺） | **已闭环** | 演练步骤统一走 `CommandRunner`，`--dry-run` 时每条命令都打印 `[dry-run] 将要执行: <完整命令行>` 且不执行（`scripts/rollback_drill.py` 的命令执行器）；`EVIDENCE/03_dry_run/stdout.log` 为真实 dry-run 全程输出，可逐条对照。上一轮的「部署缺陷版本」独立步骤已并入 `scripts/up_demo_env.sh`（同时构建 1.0.0 与 0.2.0 两个镜像），演练本体只做「切镜像 → 起容器 → 探活 → 校验版本」，因此不存在「某步命令藏着不打印」的位置。 |
| 3 | README 的示例值跟 `.env.example` 对不上；README 说比对容器 ID，实际代码比的是 Name/Image/StartedAt | **已闭环** | README §5 环境变量表逐项对应 `.env.example` 的 29 个键名（`APP_IMAGE_REPO`、`APP_CURRENT_TAG`、`APP_ROLLBACK_TAG`、`NGINX_HTTP_PORT`、`NGINX_LISTEN_PORT`、`POSTGRES_PORT_INNER`、`DRILL_API_BASE_URL`、`ROLLBACK_SLA_SECONDS`、`REPORT_DIR` 等），不再出现 `drill_operator / org_drill / act_1001` 这类过时示例值；§6 的自述改为「容器指纹（name / image / status / started_at / 容器 ID 哈希）逐字段 diff」，与代码一致。 |
