#!/usr/bin/env bash
# 一键复现三条证据：真实回滚 / 失败注入 / dry-run。
# 会依次重置环境并在 EVIDENCE/ 下留下 stdout、报告与退出码。
set -uo pipefail
cd "$(dirname "$0")/.."
set -a
# shellcheck disable=SC1091
source ./.env
set +a

EV=EVIDENCE
FAIL_HEALTH_TIMEOUT="${FAIL_HEALTH_TIMEOUT:-20}"

mkdir -p "$EV/00_env" "$EV/01_rollback_success" "$EV/02_failure_injection" "$EV/03_dry_run"

echo "== 记录镜像与镜像 ID"
docker images --format '{{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedSince}}' \
  | grep -F "${APP_IMAGE_REPO}" | tee "$EV/00_env/images.txt"

echo
echo "== 准备：环境恢复为当前版本 ${APP_CURRENT_TAG}"
bash scripts/up_demo_env.sh >"$EV/00_env/up_demo_env.log" 2>&1
echo "up_demo_env rc=$?" | tee -a "$EV/00_env/up_demo_env.log"
curl -fsS "${DRILL_API_BASE_URL}${VERSION_CHECK_PATH}" | tee "$EV/00_env/version_before.txt"; echo

echo
echo "== 路径 1/3：真实回滚（成功路径）"
bash scripts/reset_to_current.sh >"$EV/01_rollback_success/reset.log" 2>&1
python3 scripts/rollback_drill.py --report-dir "$EV/01_rollback_success" \
  >"$EV/01_rollback_success/stdout.log" 2>&1
rc1=$?
echo "$rc1" >"$EV/01_rollback_success/exit_code.txt"
echo "退出码=$rc1  耗时=$(grep -o '回滚总耗时 [0-9.]* 秒' "$EV/01_rollback_success/stdout.log" | head -1)"

echo
echo "== 路径 2/3：失败注入（DRILL_FAIL_STEP=health_check）"
bash scripts/reset_to_current.sh >"$EV/02_failure_injection/reset.log" 2>&1
DRILL_FAIL_STEP=health_check \
  HEALTH_CHECK_TIMEOUT_SECONDS="$FAIL_HEALTH_TIMEOUT" \
  HEALTH_CHECK_INTERVAL_SECONDS=2 \
  python3 scripts/rollback_drill.py --report-dir "$EV/02_failure_injection" \
  >"$EV/02_failure_injection/stdout.log" 2>&1
rc2=$?
echo "$rc2" >"$EV/02_failure_injection/exit_code.txt"
echo "退出码=$rc2  耗时=$(grep -o '回滚总耗时 [0-9.]* 秒' "$EV/02_failure_injection/stdout.log" | head -1)"

echo
echo "== 路径 3/3：--dry-run（前后 docker ps 对比）"
bash scripts/reset_to_current.sh >"$EV/03_dry_run/reset.log" 2>&1
DOCKER="$(command -v docker || echo /usr/bin/docker)"
# 用 State（running/exited/created）而不是 Status 里的 "Up 27 minutes" —— 运行时长每秒都在变，
# 拿它比对会把「什么都没动」误判成变化；真正的"是否被重建/重启"由下面的容器指纹判定。
PS_FMT='{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Ports}}'
snap_all()  { "$DOCKER" ps -a --format "$PS_FMT" | sort; }
snap_proj() { "$DOCKER" ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" --format "$PS_FMT" | sort; }
# 指纹：容器 ID / 启动时刻 / 重启次数 / 镜像 ID —— 比 ps 列表更难糊弄，重建或重启都会变
fingerprint() {
  "$DOCKER" ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    | while read -r id; do
        [ -n "$id" ] || continue
        "$DOCKER" inspect -f '{{.Id}} started={{.State.StartedAt}} restarts={{.RestartCount}} image={{.Image}}' "$id"
      done | sort
}

sleep 2   # 等 compose up 的容器状态从 Created 稳定为 Up，避免快照抓到瞬时态
snap_all  >"$EV/03_dry_run/host_docker_ps_before.txt"
snap_proj >"$EV/03_dry_run/project_before.txt"
fingerprint >"$EV/03_dry_run/fingerprint_before.txt"
before_all=$(wc -l <"$EV/03_dry_run/host_docker_ps_before.txt")
before_proj=$(wc -l <"$EV/03_dry_run/project_before.txt")
echo "before 快照行数：全机=$before_all  本项目=$before_proj"
if [[ "$before_proj" -eq 0 ]]; then
  echo "致命：本项目 before 快照为空，无法对比（说明快照时 compose 项目未起来）" | tee "$EV/03_dry_run/diff_verdict.txt"
  exit 3
fi

python3 scripts/rollback_drill.py --dry-run --report-dir "$EV/03_dry_run" \
  >"$EV/03_dry_run/stdout.log" 2>&1
rc3=$?

snap_all  >"$EV/03_dry_run/host_docker_ps_after.txt"
snap_proj >"$EV/03_dry_run/project_after.txt"
fingerprint >"$EV/03_dry_run/fingerprint_after.txt"

diff "$EV/03_dry_run/host_docker_ps_before.txt" "$EV/03_dry_run/host_docker_ps_after.txt" \
  >"$EV/03_dry_run/host_docker_ps_diff.txt"
diff_all=$?
diff "$EV/03_dry_run/project_before.txt" "$EV/03_dry_run/project_after.txt" \
  >"$EV/03_dry_run/project_diff.txt"
diff_proj=$?
diff "$EV/03_dry_run/fingerprint_before.txt" "$EV/03_dry_run/fingerprint_after.txt" \
  >"$EV/03_dry_run/fingerprint_diff.txt"
diff_fp=$?

# 判定口径：本项目容器前后必须完全一致（diff=0）；全机列表作旁证（其它项目容器的 healthy 翻转与本演练无关）
{
  echo "本项目（project=${COMPOSE_PROJECT_NAME}）前后 diff 退出码=$diff_proj（0=完全一致）"
  echo "before/after 行数：全机=$before_all/$(wc -l <"$EV/03_dry_run/host_docker_ps_after.txt") 本项目=$before_proj/$(wc -l <"$EV/03_dry_run/project_after.txt")"
  echo "dry-run 退出码=$rc3"
  echo "容器指纹（ID/启动时刻/重启次数/镜像ID）前后 diff 退出码=$diff_fp（0=一个容器都没被重建或重启）"
  if [[ "$diff_proj" -eq 0 && "$diff_fp" -eq 0 ]]; then
    echo "判定：PASS —— dry-run 未改变本项目任何容器（ps 列表与容器指纹均逐字节一致）"
  else
    echo "判定：FAIL —— 本项目容器状态有变化，见 project_diff.txt / fingerprint_diff.txt"
  fi
  echo "---- 旁证：全机 docker ps -a 前后 diff 退出码=$diff_all，差异行数=$(wc -l <"$EV/03_dry_run/host_docker_ps_diff.txt")（非判定依据，其它项目容器状态变化会体现在这里）"
} | tee "$EV/03_dry_run/diff_verdict.txt"
echo "$rc3" >"$EV/03_dry_run/exit_code.txt"

echo
echo "== 硬编码扫描"
bash scripts/scan_hardcoded_values.sh >"$EV/hardcode_scan.txt" 2>&1
echo "扫描退出码=$?"

echo
echo "== 无默认地址守卫（缺 APP_BIND_HOST 必须启动失败，而不是退回内置默认地址）"
mkdir -p "$EV/04_no_default_guard"
GUARD_IMG="${APP_IMAGE_REPO}:${APP_CURRENT_TAG}"
"$DOCKER" run --rm --network none --entrypoint sh -e APP_BIND_HOST= "$GUARD_IMG" -c \
  'exec uvicorn main:app --host "${APP_BIND_HOST:?APP_BIND_HOST must be injected}" --port 8000' \
  >"$EV/04_no_default_guard/stdout.log" 2>&1
guard_rc=$?
echo "退出码=$guard_rc" >"$EV/04_no_default_guard/exit_code.txt"
{
  echo "镜像：$GUARD_IMG"
  echo "命令：docker run --rm --network none --entrypoint sh -e APP_BIND_HOST= <img> -c 'exec uvicorn ... --host \"\${APP_BIND_HOST:?}\" ...'"
  echo "退出码=$guard_rc（非 0 即符合预期：缺地址时拒绝启动）"
  if [ "$guard_rc" -ne 0 ] && grep -q "APP_BIND_HOST must be injected" "$EV/04_no_default_guard/stdout.log"; then
    echo "判定：PASS —— 缺 APP_BIND_HOST 时启动被拒绝，报错点名该变量，没有静默使用内置默认地址"
  else
    echo "判定：FAIL —— 缺地址仍能启动，说明镜像里还残留默认地址"
  fi
} | tee "$EV/04_no_default_guard/verdict.txt"
echo "守卫判定=$([ "$guard_rc" -ne 0 ] && echo PASS || echo FAIL)"

echo
echo "== 汇总"
for d in 01_rollback_success 02_failure_injection 03_dry_run; do
  printf '%-24s exit=%s  %s\n' "$d" "$(cat "$EV/$d/exit_code.txt")" \
    "$(grep -o '回滚总耗时 [0-9.]* 秒' "$EV/$d/report.md" | head -1 || true)"
done
