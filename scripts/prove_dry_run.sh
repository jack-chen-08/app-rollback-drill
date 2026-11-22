#!/usr/bin/env bash
# 证明 --dry-run 无副作用：采集 before/after 的容器状态并做 diff。
# 产物: reports/dry_run_docker_ps_diff.txt（对照前先做一次 --dry-run 空跑）
#
# 用法: bash scripts/prove_dry_run.sh
set -euo pipefail

cd "$(dirname "$0")/.."   # project root (this script lives in scripts/)

mkdir -p reports run/alerts
OUT=reports/dry_run_docker_ps_diff.txt
BEFORE=$(mktemp)
AFTER=$(mktemp)
DIFF=$(mktemp)
trap 'rm -f "$BEFORE" "$AFTER" "$DIFF"' EXIT

# Status 里的 "Up N seconds" 会随时间漂移，故只比对 Names/Image/State/ID；
# 容器 ID 不变即证明容器未被重建或重启。
FMT='{{.Names}}\t{{.Image}}\t{{.State}}\t{{.ID}}'
snap() { docker ps --filter "name=rollback-drill" --format "$FMT" | sort; }

snap > "$BEFORE"
./run_drill.sh --dry-run > reports/dry_run_console.txt 2>&1 || {
  echo "dry-run 本身失败，见 reports/dry_run_console.txt" >&2
  exit 1
}
snap > "$AFTER"

{
  echo "# dry-run 前后容器状态对比（证明 --dry-run 不改动任何容器状态）"
  echo "# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# 命令: docker ps --filter name=rollback-drill --format '$FMT' | sort"
  echo "# 说明: 对比字段剔除 Status 中的运行时长(Up N seconds)，该字段随时间自然漂移；"
  echo "#       容器 ID 不变即证明容器未被重建/重启。"
  echo
  echo "## BEFORE (--dry-run 之前)"
  cat "$BEFORE"
  echo
  echo "## AFTER  (--dry-run 之后)"
  cat "$AFTER"
  echo
  echo "## DIFF"
  if diff -u "$BEFORE" "$AFTER" > "$DIFF"; then
    echo '(无差异 / no diff)'
    echo "结论: 容器 Names/Image/State/ID 全部一致 -> --dry-run 无副作用"
  else
    cat "$DIFF"
    echo "结论: 存在差异 -> --dry-run 有副作用，需修脚本"
    exit 1
  fi
} > "$OUT"

echo "[prove_dry_run] written: $OUT"
sed -n '/## DIFF/,$p' "$OUT"
