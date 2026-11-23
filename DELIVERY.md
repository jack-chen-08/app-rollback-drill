# 交付说明 · 应用回滚演练脚本（返工第 1 轮）

- 仓库：https://github.com/jack-chen-08/app-rollback-drill
- 提交：本次返工提交（见平台回帖与 `git log -1`）
- 上一轮被退回的提交：`dd25d87`（评审已确认 AC 1/2/3/5 有可核对证据，本轮在其上修补，不推翻已验证的实现）

## 1. 三条返工意见 → 处理结果

| # | 评审原文要点 | 本轮怎么改 | 复核方式（读者可自行复跑） |
|---|---|---|---|
| 1 | [blocker] 自述"提交了前后 docker ps 原始输出"，但 `reports/` 里只有空 diff 与一句结论 | `scripts/prove_dry_run.sh` 重写：直接采集 **原始** `docker ps -a`（全机）与项目内 `docker compose ps`，落盘到 `reports/dry_run_docker_ps_before.txt` / `_after.txt`、`dry_run_compose_ps_before.txt` / `_after.txt`；指纹 `Id/Name/Image/StartedAt` 落 `dry_run_fingerprint_before/after.txt`；比对写 `dry_run_diff.txt`、结论写 `dry_run_verdict.txt`。容器数为 0 时判 FAIL，避免"环境没起、比了个空集"也算通过 | `bash scripts/prove_dry_run.sh` → 末行 `verdict=PASS`；本次产物已提交在 `reports/` |
| 2 | [minor] dry-run 时"部署缺陷版本"一步只打印了 compose up，未打印 `ln -sfn` 与等待告警，且未显示 `API_TAG=v2` | ① `Runner.run()` 把 `extra_env` 前置到打印行（`API_TAG=v2 docker compose …`）；② 部署缺陷版本一步在 dry-run 下显式打印前端软链原子替换与"等新告警"两行。新增 2 条单测锁住该行为 | `./run_drill.sh --dry-run` → `reports/dry_run_console.txt` 第 6–8 行；`python3 -m unittest discover -s tests -v` |
| 3 | [minor] README §5 环境变量示例值与仓库 `.env.example` 对不上；§6 说"容器 ID 不变即证明"但脚本实际比对的是 `Status` 列 | §5 的示例列改为与 `.env.example` 逐字一致（`drill_user` / `00000000-…-0001` / `11111111-…-1111` / `资源对接流程` / `drill_user` 账号等）；§6 改为如实描述比对四项 `Id/Name/Image/State.StartedAt`，并说明为什么排除 `Status`（`Up 3 minutes` 随时间漂移，纳入比对会让 diff 永远非空） | 对读 `README.md` §5 与 `.env.example`；§6 与 `scripts/prove_dry_run.sh` 实现一致 |

## 2. 本单范围 ①–⑤ 对照（不是只修那三条意见）

| 范围要求 | 实现位置 | 本次实测证据 |
|---|---|---|
| ① 记录基线：当前版本号与冒烟结果 | `baseline` 步：`GET /api/version` + 5 条冒烟 | 报告 §2，`baseline ok (0.04s)`，5/5 PASS |
| ② 部署缺陷版本（健康检查 500）并确认告警触发 | `deploy_defective` 步：切缺陷标签 + 前端指到缺陷包 + 等 `/health != 200` 且 watchdog 写入 `alert_fired` | `deploy_defective ok (4.19s)`；计时起点 = 告警日志时间戳 |
| ③ 从告警时刻计时回滚：切镜像标签、滚动重启、恢复前端静态包、按前缀清 Redis | `switch_image_tag` / `rolling_restart` / `restore_frontend` / `clean_redis` 四步 | 3.40 s / 4.67 s / 11.70 s / 0.52 s；清理行 `purged 1 keys with prefix 'member-space:', re-scan after purge = 0 keys` |
| ④ 跑 5 条冒烟（健康检查、登录、会员列表、活动详情、知识库检索） | `smoke_suite` 步，虚构账号来自环境变量 | 报告 §5：5/5 PASS（health/login/members/activities/kb search） |
| ⑤ 停止计时出报告：每步起止、总耗时、失败步骤与日志片段 | `render_report()` | 总耗时 **20.61 s** ≤ 900 s，结论 PASS；失败注入报告标 `失败步骤：clean_redis` 并附日志尾部 |

## 3. 本次实测（2026-11-24，docker compose 环境）

| 项 | 结果 |
|---|---|
| 单测 | 14 tests OK（原 12 + 本轮新增 2） |
| 完整演练 | exit 0，总耗时 20.61 s，5/5 冒烟 PASS |
| 失败注入 | `--force-fail clean_redis` → exit 1，报告 FAIL 并标出失败步骤 |
| dry-run | 7 个容器 `Id/Name/Image/StartedAt` 前后完全一致，`verdict=PASS` |
| 版本回滚 | `v1 → 缺陷 v2 → v1`，`/api/version` 与前端 `current` 软链双核对 |

产物：`reports/drill_rollback_20261124_050347.md`、`reports/drill_forced_failure_20261124_050306.md`、
`reports/dry_run_*.txt`、`reports/unittest_output.txt`。

## 4. 复现

```bash
cp .env.example .env && bash scripts/up_env.sh     # 构建 v1(健康)/v2(缺陷) 两个镜像并拉起环境
python3 -m unittest discover -s tests -v           # 14 tests OK
./run_drill.sh --dry-run; echo "exit=$?"           # exit=0
bash scripts/prove_dry_run.sh                      # 末行 verdict=PASS
./run_drill.sh; echo "exit=$?"                     # exit=0，报告总耗时 ≤ 900 s
./run_drill.sh --force-fail clean_redis; echo "exit=$?"   # exit=1
```

## 5. 已知边界

- `app/` 是演练替身服务，不是可上线会员系统；只为让 5 条冒烟有真实数据与真实依赖（PG 16 + Redis）。
- 单副本环境；多副本/编排变更通过 `CMD_SWITCH_TAG` / `CMD_ROLLING_RESTART` / `CMD_RESTORE_FRONTEND` / `CMD_CLEAN_REDIS` 覆盖接入。
- 前端用符号链接原子切换；真实环境若是 CDN/OSS，换成回源/刷缓存命令即可。
- 知识库检索用确定性哈希 embedding（离线可跑），换成内网 embedding 服务后接口契约不变。
- `clean_redis` 之后跑冒烟会重建 1 个 `member-space:kb:*` 键——这是预期的缓存重建，判断清理生效看的是 DEL 后立刻 re-scan = 0。
