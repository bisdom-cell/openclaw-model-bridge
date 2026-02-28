#!/usr/bin/env bash
blog_new_count=0
blog_new_events_file=""
day="$(TZ=Asia/Tokyo date "+%Y-%m-%d")"
set -euo pipefail

ROOT="${ROOT:-$HOME/.openclaw}"
JOB_DIR="$ROOT/jobs/openclaw_official"

FETCH="$JOB_DIR/fetch_github_releases.sh"
FORMAT_PY="$JOB_DIR/format_github_releases.py"
FETCH_BLOG="$JOB_DIR/fetch_official_blog.sh"
PARSE_BLOG="$JOB_DIR/parse_official_blog.py"

STATE="$JOB_DIR/state.json"
CACHE_DIR="$JOB_DIR/cache"
MSG="$CACHE_DIR/system_message.txt"

KB_SRC="$HOME/.kb/sources/openclaw_official.md"
KB_INBOX="$HOME/.kb/inbox.md"

mkdir -p "$CACHE_DIR" "$HOME/.kb/sources" "$ROOT/logs/jobs"

# init files if absent
if [ ! -f "$STATE" ]; then
  printf "%s\n" "{\"github_releases\":{\"last_updated\":null,\"seen_ids\":[]}}" > "$STATE"
fi
if [ ! -f "$KB_SRC" ]; then
  echo "# OpenClaw Official Watcher" > "$KB_SRC"
fi
if [ ! -f "$KB_INBOX" ]; then
  echo "# INBOX" > "$KB_INBOX"
fi

ATOM_PATH="$("$FETCH")"
BLOG_HTML="$("$FETCH_BLOG")"

# JSONL stream (string) -> write to temp file to avoid pipe subshell issues
JSONL_FILE="$(mktemp)"
"$FORMAT_PY" "$ATOM_PATH" > "$JSONL_FILE"

last_updated="$(jq -r ".github_releases.last_updated" "$STATE")"
seen_ids="$(jq -c ".github_releases.seen_ids" "$STATE")"

new_count=0
new_last_updated=""
new_ids_file="$(mktemp)"
new_events_file="$(mktemp)"

# Read JSONL from file (no subshell)
while IFS= read -r line; do
  [ -z "${line// }" ] && continue

  eid="$(printf "%s\n" "$line" | jq -r ".id")"
  ts="$(printf "%s\n" "$line" | jq -r ".ts")"

  # already seen?
  if printf "%s\n" "$seen_ids" | jq -e --arg id "$eid" "index(\$id) != null" >/dev/null 2>&1; then
    continue
  fi

  # only strictly newer than last_updated
  if [ "$last_updated" != "null" ] && [ -n "$last_updated" ]; then
    if [[ "$ts" < "$last_updated" || "$ts" == "$last_updated" ]]; then
      continue
    fi
  fi

  printf "%s\n" "$line" >> "$new_events_file"
  printf "%s\n" "$eid" >> "$new_ids_file"
  new_count=$((new_count+1))
  if [ -z "$new_last_updated" ]; then
    new_last_updated="$ts"
  fi
done < "$JSONL_FILE"

rm -f "$JSONL_FILE"

if [ "$new_count" -eq 0 ] && [ "$blog_new_count" -eq 0 ]; then
  echo "openclaw_official/github_releases: no new releases."
  rm -f "$new_ids_file" "$new_events_file" "$blog_new_events_file"
  exit 0
fi

blog_new_count=0
blog_new_events_file="$(mktemp)"
blog_all_file="$(mktemp)"
python3 "$PARSE_BLOG" "$BLOG_HTML" > "$blog_all_file"
while IFS= read -r ev; do
  url="$(printf "%s
" "$ev" | jq -r ".url")"
  if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
    printf "%s
" "$ev" >> "$blog_new_events_file"
    blog_new_count=$((blog_new_count+1))
  fi
done < "$blog_all_file"
rm -f "$blog_all_file"
echo "DEBUG blog_new_count=$blog_new_count"
now_jst="$(TZ=Asia/Tokyo date "+%Y-%m-%d %H:%M JST")"
{
  echo "[System Message][SOURCE=openclaw-official][PRIORITY=P0]"
  echo "OpenClaw GitHub Releases Digest (${now_jst})"
  echo ""
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    ts="$(printf "%s\n" "$ev" | jq -r ".ts")"
    echo "- ${ts} | ${title}"
    echo "  ${url}"
  done < "$new_events_file"

# Blog -> INBOX (de-dup by URL)
if [ "$blog_new_count" -gt 0 ]; then
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    line="- [ ] (${day}) openclaw blog | ${title} | ${url}"
    if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
      printf "\n%s\n" "$line" >> "$KB_INBOX"
    fi
  done < "$blog_new_events_file"
fi

  if [ "$blog_new_count" -gt 0 ]; then
    echo ""
    echo "P0 Blog"
    while IFS= read -r ev; do
      ts="$(printf "%s\n" "$ev" | jq -r ".ts")"
      title="$(printf "%s\n" "$ev" | jq -r ".title")"
      url="$(printf "%s\n" "$ev" | jq -r ".url")"
      echo "- ${ts} | ${title}"
      echo "  ${url}"
    done < "$blog_new_events_file"
  fi
} > "$MSG"

day="$(TZ=Asia/Tokyo date "+%Y-%m-%d")"
{
  echo ""
  echo "## ${day}"
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    ts="$(printf "%s\n" "$ev" | jq -r ".ts")"
    id="$(printf "%s\n" "$ev" | jq -r ".id")"
    fp="$(printf "%s\n" "$ev" | jq -r ".fingerprint")"
    echo "- **${title}**"
    echo "  - Time: ${ts}"
    echo "  - URL: ${url}"
    echo "  - ID: ${id}"
    echo "  - Fingerprint: ${fp}"
  done < "$new_events_file"

# Blog -> INBOX (de-dup by URL)
if [ "$blog_new_count" -gt 0 ]; then
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    line="- [ ] (${day}) openclaw blog | ${title} | ${url}"
    if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
      printf "\n%s\n" "$line" >> "$KB_INBOX"
    fi
  done < "$blog_new_events_file"
fi
} >> "$KB_SRC"

# INBOX append with de-dup by URL
while IFS= read -r ev; do
  title="$(printf "%s
" "$ev" | jq -r ".title")"
  url="$(printf "%s
" "$ev" | jq -r ".url")"
  line="- [ ] (${day}) openclaw release | ${title} | ${url}"
  if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
    printf "
%s
" "$line" >> "$KB_INBOX"
  fi
done < "$new_events_file"

# Blog -> INBOX (de-dup by URL)
if [ "$blog_new_count" -gt 0 ]; then
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    line="- [ ] (${day}) openclaw blog | ${title} | ${url}"
    if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
      printf "\n%s\n" "$line" >> "$KB_INBOX"
    fi
  done < "$blog_new_events_file"
fi

add_json="$(jq -R . < "$new_ids_file" | jq -s .)"
updated_seen="$(jq -c --argjson add "$add_json" --argjson seen "$seen_ids" "(\$add + \$seen)[:200]" <<< "{}")"

tmp="$(mktemp)"
jq --arg last "$new_last_updated" --argjson seen "$updated_seen" \
  ".github_releases.last_updated=\$last | .github_releases.seen_ids=\$seen" "$STATE" > "$tmp"
mv "$tmp" "$STATE"

rm -f "$new_ids_file" "$new_events_file" "$blog_new_events_file"

echo "openclaw_official/github_releases: new=${new_count}, last_updated=${new_last_updated}"
echo "system_message_saved=${MSG}"
echo "kb_source_saved=${KB_SRC}"
echo "kb_inbox_saved=${KB_INBOX}"
echo "---- SYSTEM MESSAGE ----"
cat "$MSG"

# OPTIONAL: announce hook (adapt to your environment)
# "$ROOT/bin/announce.sh" < "$MSG"
openclaw message send --target +85200000000 --message "$(cat "$MSG")" --json >/dev/null 2>&1 || true
