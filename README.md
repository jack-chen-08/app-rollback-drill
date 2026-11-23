# 会员空间平台 · 应用回滚演练脚本

一条命令跑完「部署缺陷版本 → 等告警 → 计时回滚 → 出报告」，回滚总耗时自动与 15 分钟红线比对，任一步失败立即停止并在报告中标出。

- 交付形态：脚本 + docker-compose 演练环境（示例接口服务 v1/v2 + PostgreSQL 16 + Redis）+ 实测报告样例
- 运行依赖：Docker / docker compose v2；脚本本体仅用 Python 3.11+ 标准库（无第三方包）
- 语言：报告与文档中文，变量名/命令英文

## 1. 交付清单

| 路径 | 作用 |
|---|---|
| `scripts/drill_rollback.py` | 演练主脚本（唯一入口，约 700 行，含报告生成） |
| `scripts/up_env.sh` | 一键构建两个镜像版本并拉起演练环境 |
| `run_drill.sh` | 包一层 `.env` 加载，`./run_drill.sh` 即跑 |
| `app/` | 示例接口服务（FastAPI + SQLAlchemy 2 + PostgreSQL 16/pgvector + Redis 缓存） |
| `ops/watchdog.py` | 健康巡检小进程，`/health` 连续失败即写告警日志（计时起点来源） |
| `frontend/releases/v1|v2` | 前端静态包两个版本，靠 `current` 符号链接原子切换 |
| `docker-compose.yml` | 演练环境编排（db / redis / api / web / watchdog） |
| `.env.example` | 全部环境变量清单（地址、账号、前缀一律来自环境变量） |
| `scripts/prove_dry_run.sh` | 一键采集 dry-run 前后原始 `docker ps -a` + 指纹并 diff，把证据直接写进 `reports/` |
| `tests/test_drill.py` | 14 条单测：配置校验、敏感值打码、计时口径、报告内容、失败报告口径、dry-run 不执行且逐条打印（含 `API_TAG=`/`ln -sfn`/`wait:`） |
| `reports/` | 真实跑出来的演练报告、失败注入报告、dry-run 前后状态对比 |

## 2. 快速开始

```bash
cp .env.example .env          # 唯一需要人填的动作；示例值可直接跑演练
bash scripts/up_env.sh        # 构建 v1(健康) / v2(缺陷) 镜像并拉起环境
./run_drill.sh --dry-run      # 只打印将执行的命令，不碰容器
./run_drill.sh                # 完整演练，报告落在 reports/
echo $?                       # 0 正常；1 某步失败；3 超过 15 分钟上限
```

单测：`python3 -m unittest discover -s tests -v`

## 3. 计时口径（这是整件事的核心）

| 时点 | 取值方式 |
|---|---|
| 缺陷部署开始 | 脚本把 api 镜像切到 `BAD_TAG`、前端指到坏版本 |
| **t0 告警触发** | `ops/watchdog.py` 观测到 `/health` 连续 N 次非 200 时写入 `run/alerts/alerts.log` 的时间戳，脚本读该行 |
| t_end | 最后一条冒烟用例结束 |
| 总耗时 | `t_end - t0`，与 `MAX_ROLLBACK_SECONDS`（默认 900 s）比对 |

刻意不把「部署缺陷版本 + 等告警」算进回滚耗时——那是故障注入，不是回滚；把它塞进分子会让指标虚高。报告里这两步单列在「准备阶段」。

## 4. 回滚五个动作怎么落地

1. **切换上一镜像标签**：先 `docker image inspect` 确认上一版镜像在本地（避免切到不存在的标签才发现），再 `docker compose up -d --no-deps api` 并带 `API_TAG=上一版`；随后探测 `/api/version` 确认已是上一版。
2. **滚动重启**：`docker compose up -d --no-deps --force-recreate --wait api`，`--wait` 保证容器 healthy 才继续（不 healthy 即该步失败，符合失败即停）。生产多副本/k8s 场景通过 `CMD_ROLLING_RESTART` 覆盖成 `kubectl rollout restart deploy/api` 等价命令，脚本不假设编排方式。
3. **恢复前端静态包**：把 `frontend/current` 指向 `releases/v1`，用「建临时链接 + `os.replace`」做原子切换（不会出现半截状态），再校验容器内 `/version.txt` 已变回 v1，最后 `docker compose restart web` 让静态服务重载。
4. **按前缀清理 Redis 缓存键**：`--scan --pattern "member-space:*"` 取键 → 每 200 个一批 `DEL` → 重新扫描必须为 0。用 SCAN 不用 KEYS，避免阻塞线上实例。
5. **5 条冒烟用例**：健康检查、登录、会员列表、活动详情、知识库检索（pgvector 余弦相似度），逐条记录耗时与 HTTP 码，全过才算回滚成功。

## 5. 环境变量清单

脚本里没有任何地址、账号、密钥字面量；缺哪个变量会直接报名字并退出 2。
下表「示例」列与仓库里的 `.env.example` 逐字一致（以该文件为准）。

| 变量 | 示例（= `.env.example`） | 说明 |
|---|---|---|
| `API_BASE_URL` / `WEB_BASE_URL` | `http://127.0.0.1:18080` / `http://127.0.0.1:18081` | 被测服务的接口地址与前端地址 |
| `API_IMAGE` / `GOOD_TAG` / `BAD_TAG` | `member-space-api` / `v1` / `v2` | 镜像名、回滚目标标签、缺陷版标签 |
| `ALERT_LOG` | `./run/alerts/alerts.log` | watchdog 告警日志（计时起点） |
| `ALERT_TIMEOUT_S` | `120` | 等「健康检查失败 + 新告警」的上限 |
| `REDIS_CLI` | `docker compose -p rollback-drill exec -T redis redis-cli` | 执行 SCAN/DEL 的客户端命令 |
| `REDIS_PREFIX_CLEAN` | `member-space:` | 要清理的缓存键前缀 |
| `FRONTEND_CURRENT` / `FRONTEND_RELEASE` | `./frontend/current` / `./frontend/releases/v1` | 前端当前软链与要恢复的版本目录 |
| `SMOKE_USERNAME` / `SMOKE_PASSWORD` | `drill_user` / 示例值 | 冒烟用虚构账号（来自环境变量，不入库源码） |
| `SMOKE_ORG_ID` / `SMOKE_ACTIVITY_ID` / `SMOKE_KB_QUERY` | `00000000-0000-0000-0000-000000000001` / `11111111-1111-1111-1111-111111111111` / `资源对接流程` | 冒烟用例入参 |
| `MAX_ROLLBACK_SECONDS` | `900` | 15 分钟红线，超出则退出码 3 并在报告标 FAIL |
| `REPORT_DIR` | `./reports` | 报告输出目录 |
| `CMD_SWITCH_TAG` / `CMD_ROLLING_RESTART` / `CMD_RESTORE_FRONTEND` / `CMD_CLEAN_REDIS` | 空 | 可选：用等价命令替换默认 docker compose 实现 |

## 6. 两条硬性行为怎么验

```bash
# ① 失败即停：注入在 clean_redis 步失败
./run_drill.sh --force-fail clean_redis; echo "exit=$?"      # 期望 exit=1
grep "失败步骤" reports/drill_forced_failure_*.md            # 报告标出步骤 + 错误 + 日志尾部

# ② dry-run 不改状态：一键采集 before/after 原始输出并比对
bash scripts/prove_dry_run.sh                                # 末行应为 verdict=PASS
```

`prove_dry_run.sh` 把两份 `docker ps -a` 原始输出直接提交进 `reports/`
（`dry_run_docker_ps_before.txt` / `dry_run_docker_ps_after.txt`，另有项目内
`dry_run_compose_ps_before|after.txt`），再用 `dry_run_fingerprint_before|after.txt`
做比对，`dry_run_diff.txt` 为 unified diff，`dry_run_verdict.txt` 为结论行。

> 比对字段是 `Id`、`Name`、`Image`、`State.StartedAt` 四项，不是 `docker ps` 的
> `Status` 列。`Status`（`Up 3 minutes`）会随时间自然漂移，纳入比对会让 diff 永远非空；
> 而 `Id` 变化即容器被重建、`StartedAt` 变化即被重启——这两项不动才叫「没有副作用」。
> 脚本在容器数为 0 时判 FAIL，避免「演练环境没起、比了个空集」也算通过。

dry-run 的控制台输出在 `reports/dry_run_console.txt`，每一条将执行的命令都带
`[dry-run] $` 前缀，且把 `API_TAG=...` 这类环境切换写在命令前面，例如：

```text
[dry-run] $ API_TAG=v2 docker compose -f docker-compose.yml -p rollback-drill up -d --no-deps --pull never api
[dry-run] $ ln -sfn releases/v2 ./frontend/current   (relative atomic swap)
[dry-run] $ wait: GET http://127.0.0.1:18080/health != 200 AND new 'alert_fired' line in ./run/alerts/alerts.log (timeout 120s)
```


## 7. 验收标准对照

| AC | 判据 | 证据 |
|---|---|---|
| 1 | 回滚总耗时 ≤ 15 分钟 | `reports/drill_rollback_*.md` 第 4 节实测值与 PASS/FAIL |
| 2 | 每步开始/结束/耗时/结果 | 同一报告第 2、3 节表格 |
| 3 | 某步失败即停、退出码非 0、标出失败步骤 | `reports/drill_forced_failure_*.md` + 终端 `exit=1` |
| 4 | `--dry-run` 只打印命令、不改容器状态 | `reports/dry_run_console.txt`（逐条命令含 `API_TAG=`/`ln -sfn`/`wait:`）+ `reports/dry_run_docker_ps_before.txt`/`_after.txt`（原始 `docker ps -a`）+ `reports/dry_run_diff.txt` + `reports/dry_run_verdict.txt` |
| 5 | 脚本无硬编码地址、账号、密钥 | `grep -nE "https?://\|password\|secret" scripts/drill_rollback.py` 只见变量名；敏感值在报告里打码 |

## 8. 已知边界（实话）

- `app/` 是**演练替身**服务：只为让 5 条冒烟用例有真实数据与真实依赖（PG 16 + Redis），业务逻辑刻意极简，不是可上线的会员系统。
- 演练环境是单副本；多副本/多实例/容器编排变更通过 `CMD_*` 覆盖命令接入，脚本本身不绑定 compose。
- 前端静态包用符号链接切换，真实环境若是 CDN/OSS 则对应 `CMD_RESTORE_FRONTEND` 换成回源/刷缓存命令。
- 知识库检索用的是确定性哈希 embedding（离线可跑、不外呼模型），真实环境换成本地或内网 embedding 服务即可，接口契约不变。
- 计时精度为秒级（Python `datetime.now()`），不追求毫秒级；对 15 分钟红线足够。

## 9. 复现记录

见 `reports/` 目录（全部为真实执行产物，不是手写）：
- `drill_rollback_20261124_050347.md`：完整演练报告
- `drill_forced_failure_20261124_050306.md`：失败注入报告（退出码 1）
- `dry_run_console.txt`：dry-run 逐条命令（每条带 `[dry-run] $`，含 `API_TAG=` 与 `ln -sfn`）
- `dry_run_docker_ps_before.txt` / `dry_run_docker_ps_after.txt`：执行前后**原始** `docker ps -a`
- `dry_run_compose_ps_before.txt` / `dry_run_compose_ps_after.txt`：项目内 `docker compose ps`
- `dry_run_fingerprint_before.txt` / `dry_run_fingerprint_after.txt`：`Id/Name/Image/StartedAt` 指纹
- `dry_run_diff.txt`（本次为空 = 无差异）、`dry_run_verdict.txt`（`verdict=PASS`）
- `unittest_output.txt`：单测输出（14 tests OK）

## 10. 本机实测结果（2026-11-24 重跑）

对应报告：`reports/drill_rollback_20261124_050347.md`、`reports/drill_forced_failure_20261124_050306.md`。

| 项 | 实测值 |
|---|---|
| 完整演练退出码 | `0` |
| 回滚总耗时（告警触发 → 冒烟结束） | **20.61 s**（上限 900 s） |
| 回滚五步耗时 | 切标签 3.40 s / 滚动重启 4.67 s / 恢复前端 11.70 s / 清缓存 0.52 s / 冒烟 0.03 s |
| 准备阶段 | 基线冒烟 0.04 s、部署缺陷版本 4.19 s（不计入回滚耗时） |
| 冒烟用例 | 5/5 PASS（health / login / members / activities / kb search） |
| 基线版本 / 恢复后版本 | 演练前 `v1`，恢复后 `/api/version` = `v1`，前端 `current -> releases/v1` |
| Redis 清理 | `purged 1 keys with prefix 'member-space:', re-scan after purge = 0 keys` |
| 失败注入演练 | `--force-fail clean_redis` → 退出码 `1`、报告标出 `失败步骤：clean_redis`、`结论：**FAIL**` |
| dry-run | 7 个容器 `Id/Name/Image/StartedAt` 前后完全一致，`verdict=PASS` |
| 单测 | `14 tests OK` |

一键复现全部上述数字：

```bash
cp .env.example .env && bash scripts/up_env.sh
python3 -m unittest discover -s tests -v          # 14 tests OK
./run_drill.sh --dry-run; echo "exit=$?"          # exit=0，不碰容器
bash scripts/prove_dry_run.sh                     # 末行 verdict=PASS
./run_drill.sh; echo "exit=$?"                    # exit=0，报告含总耗时 ≤ 900 s
./run_drill.sh --force-fail clean_redis; echo "exit=$?"   # exit=1，报告标失败步骤
```

> 说明：`clean_redis` 之后紧接着跑冒烟，`/api/kb/search` 会重新写回 1 个 `member-space:kb:*` 缓存键。这是**预期行为**（缓存被正常重建），不是清理不干净——判断清理是否生效看的是「DEL 之后立刻 re-scan = 0 键」，报告里已记录该复查值。
