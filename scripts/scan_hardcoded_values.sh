#!/usr/bin/env bash
# 硬编码扫描（地址 / IP / 内嵌凭据 / 密钥）
#
# 判定口径（严格版）：
#   A 类 代码与编排文件（scripts/*.py、scripts/*.sh、drill/**）：出现地址字面量、IP 字面量、
#     URL 内嵌凭据、password/secret/token 直接赋值字面量 —— 一律阻断，没有白名单例外。
#     环境变量注入的写法（${VAR}、${VAR:-默认}、右侧为大写变量名）不算字面量。
#   B 类 示例配置文件（.env.example）：本身就是"配置面"，允许出现地址与虚拟口令，
#     逐行列出供人工核对，不参与阻断。
#   跳过：.git/、EVIDENCE/（证据产物）、本脚本自身（规则里必然出现地址模式）。
#
# 退出码：0 = A 类零命中；1 = A 类有命中
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

mapfile -t CODE_FILES < <(
  { find scripts drill -type f \( -name '*.py' -o -name '*.sh' -o -name 'Dockerfile' \
      -o -name '*.yml' -o -name '*.yaml' -o -name '*.template' -o -name '*.conf' \) \
      ! -name 'scan_hardcoded_values.sh' ; } | sort
)
CONFIG_FILES=(.env.example)

PATTERN='(https?://[A-Za-z0-9._:-]+|([0-9]{1,3}\.){3}[0-9]{1,3}|://[^ /]+:[^ /]+@|(password|passwd|secret|api[_-]?key|access[_-]?key|token)[[:space:]]*=[[:space:]]*[^[:space:]$]+)'

echo "# 硬编码扫描（严格版）"
echo "# A 类（阻断）扫描文件：${CODE_FILES[*]}"
echo "# 规则：$PATTERN"
echo

block=0
allow_note=0
for f in "${CODE_FILES[@]}"; do
  [ -f "$f" ] || continue
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    lineno="${hit%%:*}"
    line="${hit#*:}"
    # 注释行里的说明文字不算实现
    trimmed="${line#"${line%%[![:space:]]*}"}"
    if [[ "$trimmed" == \#* ]]; then
      echo "IGNORED  $f:$lineno: $line   （注释说明）"
      continue
    fi
    # 环境变量注入 / 右侧为变量名引用 → 非字面量
    rhs="${line#*=}"; rhs="${rhs#*:}"
    if [[ "$line" == *'${'* ]]; then
      echo "ALLOWED  $f:$lineno: ${line:0:60}   值为 \${VAR} 环境变量注入"
      allow_note=$((allow_note+1)); continue
    fi
    if [[ "$rhs" =~ ^[[:space:]]*[A-Z][A-Z0-9_]{2,}[[:space:]]*,?[[:space:]]*$ ]]; then
      echo "ALLOWED  $f:$lineno: ${line:0:60}   右侧是环境变量名引用"
      allow_note=$((allow_note+1)); continue
    fi
    echo "BLOCKING $f:$lineno: $line"
    block=$((block+1))
  done < <(grep -nE "$PATTERN" "$f" 2>/dev/null || true)
done

echo
echo "## B 类：示例配置文件（允许出现地址 / 虚拟口令，仅列出供核对）"
for f in "${CONFIG_FILES[@]}"; do
  [ -f "$f" ] || { echo "  （无 $f）"; continue; }
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    echo "CONFIG   $f:${hit:0:90}"
  done < <(grep -nE "$PATTERN" "$f" 2>/dev/null || true)
done

echo
echo "## 统计：A 类命中 ${block} 条（环境变量注入 ${allow_note} 条已排除）；B 类仅列示不阻断"
if [ "$block" -eq 0 ]; then
  echo "## 判定：PASS —— 代码与编排里没有写死的地址 / 账号 / 密钥"
  echo "## 所有可变项均由 os.environ / \${VAR} 注入；唯一配置面是 .env.example（不入库的 .env 由其派生）"
  exit 0
else
  echo "## 判定：FAIL —— 有 ${block} 条写死值，必须改成环境变量注入"
  exit 1
fi
