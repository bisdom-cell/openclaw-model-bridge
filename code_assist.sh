#!/usr/bin/env bash
# code_assist.sh — GLM-5.2 Coding 编程助手 (V37.9.254/255 provider glm5_coding 的直连消费方)
#
# 直连 Volcengine Ark GLM-5.2 端点做纯代码生成/重构/解释 (原则 #94: 纯推理绕开 Gateway,
# 完全控制 max_tokens/temperature, 无工具注入/无 200KB proxy 截断/无 12 工具上限)。
#
# 🔴 安全: API key 只从 env GLM5_API_KEY 读 (Volcengine ark-... key), 绝不硬编码/落盘。
# Volcengine: model 字段接收 endpoint ID (ep-...), 走 env GLM5_ENDPOINT_ID (缺省回退公开 model 名)。
#
# 用法:
#   export GLM5_API_KEY='ark-...'
#   export GLM5_ENDPOINT_ID='ep-...'
#   ./code_assist.sh "写一个 Python LRU cache"
#   echo "重构这段代码" | ./code_assist.sh
#   ./code_assist.sh --file mycode.py "给这个文件加类型注解"
#   ./code_assist.sh --json "返回 {name, args} 的函数签名 JSON"
#   ./code_assist.sh --dry-run "..."          # 只打印请求体, 不发送 (结构测试)
#   ./code_assist.sh --temp 0 --max-tokens 4096 "..."
set -euo pipefail

MODEL="${GLM5_ENDPOINT_ID:-${GLM5_MODEL:-glm-5-2-260617}}"
BASE_URL="${GLM5_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
MAX_TOKENS=8192
TEMP=0.2
JSON_MODE=0
DRY_RUN=0
STREAM=0
FILE=""
SYS_PROMPT="You are an expert programmer. Write complete, correct, runnable code. \
No placeholders, no TODO stubs, no ellipsis. Prefer clear idiomatic code that matches \
the surrounding style. When asked to explain, be precise and concrete."

usage() { grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --file)       FILE="$2"; shift 2 ;;
    --json)       JSON_MODE=1; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --stream)     STREAM=1; shift ;;
    --max-tokens) MAX_TOKENS="$2"; shift 2 ;;
    --temp)       TEMP="$2"; shift 2 ;;
    --model)      MODEL="$2"; shift 2 ;;
    --system)     SYS_PROMPT="$2"; shift 2 ;;
    -h|--help)    usage 0 ;;
    *)            break ;;
  esac
done

PROMPT="${*:-}"
if [ -z "$PROMPT" ] && [ ! -t 0 ]; then PROMPT="$(cat)"; fi
if [ -z "$PROMPT" ]; then echo "error: 无 prompt (传参数或 stdin)" >&2; usage 1 >&2; fi

USER_CONTENT="$PROMPT"
if [ -n "$FILE" ]; then
  if [ ! -f "$FILE" ]; then echo "error: 文件不存在: $FILE" >&2; exit 1; fi
  FILE_BODY="$(cat "$FILE")"
  USER_CONTENT="$PROMPT

--- 文件: $FILE ---
$FILE_BODY"
fi

# 用 python3 安全构造 JSON 请求体 (避免 shell 转义地狱 + 注入)
REQ_BODY="$(SYS_PROMPT="$SYS_PROMPT" USER_CONTENT="$USER_CONTENT" \
  MODEL="$MODEL" MAX_TOKENS="$MAX_TOKENS" TEMP="$TEMP" \
  JSON_MODE="$JSON_MODE" STREAM="$STREAM" python3 - <<'PY'
import json, os
body = {
    "model": os.environ["MODEL"],
    "messages": [
        {"role": "system", "content": os.environ["SYS_PROMPT"]},
        {"role": "user", "content": os.environ["USER_CONTENT"]},
    ],
    "max_tokens": int(os.environ["MAX_TOKENS"]),
    "temperature": float(os.environ["TEMP"]),
}
if os.environ["JSON_MODE"] == "1":
    body["response_format"] = {"type": "json_object"}
if os.environ["STREAM"] == "1":
    body["stream"] = True
print(json.dumps(body, ensure_ascii=False))
PY
)"

if [ "$DRY_RUN" = "1" ]; then
  echo "POST $BASE_URL/chat/completions"
  echo "$REQ_BODY" | python3 -m json.tool
  exit 0
fi

if [ -z "${GLM5_API_KEY:-}" ]; then
  echo "error: 未设 GLM5_API_KEY env" >&2; exit 1
fi

if [ "$STREAM" = "1" ]; then
  curl -sS -N "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $GLM5_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$REQ_BODY"
  echo
  exit 0
fi

# -w 捕获 HTTP 状态附在末行; || true 防 set -e 在 curl 非零时静默中止 (排错友好)
RESP="$(curl -sS -w $'\n__HTTP_STATUS__:%{http_code}' "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $GLM5_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$REQ_BODY" || true)"

printf '%s' "$RESP" | python3 - <<'PY'
import json, sys
raw = sys.stdin.read()
status = ""
if "__HTTP_STATUS__:" in raw:
    raw, _, status = raw.rpartition("__HTTP_STATUS__:")
    raw = raw.rstrip("\n")
    status = status.strip()
try:
    d = json.loads(raw) if raw.strip() else {}
except Exception as e:
    # 非 JSON / 空响应 → 打印 HTTP 状态 + 原始返回 (排错, 不再吞成 "char 0")
    print(f"解析响应失败 (HTTP {status or '?'}): {e}", file=sys.stderr)
    print(f"--- 原始返回 ---\n{raw[:2000] or '(空)'}", file=sys.stderr)
    sys.exit(1)
if not d:
    print(f"空响应 (HTTP {status or '?'}) — 端点未返回 body", file=sys.stderr); sys.exit(1)
if "error" in d:
    print(f"API error (HTTP {status or '?'}):", json.dumps(d["error"], ensure_ascii=False), file=sys.stderr); sys.exit(1)
ch = (d.get("choices") or [{}])[0]
msg = ch.get("message", {})
content = msg.get("content", "")
reasoning = msg.get("reasoning_content")
if reasoning:
    print("=== reasoning ===", file=sys.stderr)
    print(reasoning, file=sys.stderr)
    print("=== answer ===", file=sys.stderr)
print(content)
usage = d.get("usage", {})
if usage:
    print(f"[tokens: prompt={usage.get('prompt_tokens')} "
          f"completion={usage.get('completion_tokens')} "
          f"finish={ch.get('finish_reason')}]", file=sys.stderr)
PY
