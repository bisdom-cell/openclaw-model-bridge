# OpenClaw 完整配置文档
> 最后更新：2026-03-08 (HKT)
> 系统：Mac Mini (macOS) | 用户：bisdom
> 版本：v27（Proxy拆层；任务注册表；Health JSON输出；回滚机制）
> OpenClaw Gateway：2026.3.7（2026-03-08升级）
---
## 一、系统架构
```
WhatsApp ↔ OpenClaw Gateway (18789) ↔ Tool Proxy (5002) ↔ Adapter (5001) ↔ 远程GPU API
                                            │
                                     ┌──────┴──────────────────────┐
                                     │  工具过滤 (24→12)             │
                                     │  Schema简化                   │
                                     │  参数修复/别名映射             │
                                     │  非流式→SSE转换               │
                                     │  200KB消息截断                │
                                     │  系统消息过滤（空ACK）         │
                                     │  Session Startup静默          │
                                     │  Onboarding压制               │
                                     │  KB Write拦截  ←保留          │
                                     │  KB Review拦截 ←保留          │
                                     │  Announce直推 ←v13新增        │
                                     │  /Volumes/写入重定向           │
                                     └──────────────────────────────┘
系统crontab（独立于openclaw cron，v19新增）：
                                     ┌──────────────────────────────┐
                                     │  OpenClaw Official Watcher   │
                                     │  ├─ GitHub Releases (每时30分)│
                                     │  │   → ~/.kb/sources/         │
                                     │  │   → ~/.kb/inbox.md (去重)  │
                                     │  │   → WhatsApp 自动推送      │
                                     │  ├─ Official Blog (每时00分)  │
                                     │  │   → LLM富摘要（中文标题）  │
                                     │  │   → ~/.kb/sources/         │
                                     │  │   → ~/.kb/inbox.md (去重)  │
                                     │  │   → WhatsApp 自动推送      │
                                     │  └─ GitHub Discussions (每时15分)│
                                     │      → LLM富摘要（中文标题+贡献+星级）│
                                     │      → ~/.kb/sources/         │
                                     │      → ~/.kb/inbox.md (去重)  │
                                     │      → WhatsApp 自动推送      │
                                     └──────────────────────────────┘
```
| 组件 | 端口 | 文件位置 | 功能 |
|------|------|----------|------|
| OpenClaw Gateway | 18789 | 全局安装 (npm) | WhatsApp接入、工具执行、会话管理 |
| Tool Proxy | 5002 | ~/tool_proxy.py + ~/proxy_filters.py | HTTP层(tool_proxy.py) + 策略层(proxy_filters.py)：工具过滤、Schema简化、参数修复、SSE转换、截断、3层拦截+Announce直推 |
| Adapter | 5001 | ~/adapter.py | 转发到远程GPU、认证、User-Agent伪装、参数过滤 |
| 远程GPU | - | hkagentx.hkopenlab.com | Qwen3-235B推理 + 原生tool calling |
---
## 二、关键文件清单
| 文件 | 路径 | 用途 |
|------|------|------|
| 主配置 | ~/.openclaw/openclaw.json | OpenClaw核心配置（**不可含identity字段**） |
| cron任务 | ~/.openclaw/cron/jobs.json | 定时任务（4个启用） |
| workspace state | ~/.openclaw/workspace/.openclaw/workspace-state.json | onboardingCompletedAt标记 |
| 工具代理（HTTP层） | ~/tool_proxy.py | 请求/响应中间层（V27：HTTP收发+日志） |
| 工具代理（策略层） | ~/proxy_filters.py | **V27新增** 过滤/修复/截断/SSE转换，纯函数无网络依赖 |
| 任务注册表 | ~/openclaw-model-bridge/jobs_registry.yaml | **V27新增** 统一登记system+openclaw双cron任务 |
| 注册表校验器 | ~/openclaw-model-bridge/check_registry.py | **V27新增** 校验ID唯一/路径存在/字段完整 |
| 回滚指南 | ~/openclaw-model-bridge/ROLLBACK.md | **V27新增** 30秒恢复到V26 |
| KB写入脚本 | ~/kb_write.sh | KB记录执行脚本（v18已加目录锁+原子写） |
| KB回顾脚本 | ~/kb_review.sh | KB跨笔记回顾脚本 |
| ArXiv KB归档脚本 | ~/kb_save_arxiv.sh | 读取arxiv监控结果写入KB + rsync备份（v25加429拦截） |
| **每周健康检查脚本** | **~/health_check.sh** | **系统健康周报脚本（v16新增）** |
| 后端适配 | ~/adapter.py | 远程API适配层（v16：API Key改为环境变量） |
| 一键重启 | ~/restart.sh | 故障恢复脚本 |
| Proxy plist | ~/Library/LaunchAgents/com.openclaw.proxy.plist | macOS开机启动Proxy |
| Adapter plist | ~/Library/LaunchAgents/com.openclaw.adapter.plist | macOS开机启动Adapter |
| Proxy日志 | ~/tool_proxy.log | 工具调用日志 |
| Adapter日志 | ~/adapter.log | API转发日志 |
| Gateway日志 | /tmp/openclaw/openclaw-YYYY-MM-DD.log | Gateway运行日志 |
| 知识库索引 | ~/.kb/index.json | 知识库主索引（PA日常KB） |
| **Releases Watcher** | **~/.openclaw/jobs/openclaw_official/run.sh** | **GitHub Releases监控+WhatsApp推送（v19新增）** |
| **Blog Watcher** | **~/.openclaw/jobs/openclaw_official/run_blog.sh** | **Official Blog监控+LLM富摘要+WhatsApp推送（v19新增）** |
| **Blog抓取** | **~/.openclaw/jobs/openclaw_official/fetch_official_blog.sh** | **抓取openclaw.ai/blog页面（v19新增）** |
| **Blog解析** | **~/.openclaw/jobs/openclaw_official/parse_official_blog.py** | **解析blog.html提取文章元信息（v19新增）** |
| **Watcher日志** | **~/.openclaw/logs/jobs/openclaw_official.log** | **Releases cron日志（v19新增）** |
| **Blog日志** | **~/.openclaw/logs/jobs/openclaw_blog.log** | **Blog cron日志（v19新增）** |
| **Discussions Watcher** | **~/.openclaw/jobs/openclaw_official/run_discussions.sh** | **GitHub Discussions监控+LLM富摘要+中文推送（v21新增）** |
| **Discussions日志** | **~/.openclaw/logs/jobs/openclaw_discussions.log** | **Discussions cron日志（v21新增）** |
| **货代Watcher脚本** | **~/.openclaw/jobs/freight_watcher/run_freight.sh** | **货代商机Watcher（v23新增，v26首次验证）** |
| **货代Watcher日志** | **~/.openclaw/logs/jobs/freight_watcher.log** | **货代Watcher cron日志** |
| **GitHub仓库** | **git@github.com:bisdom-cell/openclaw-model-bridge.git** | **源码托管；remote已改为SSH（v25修复HTTPS认证失败）** |
---
## 三、远程GPU API
| 项目 | 值 |
|------|------|
| Endpoint | https://hkagentx.hkopenlab.com/v1/chat/completions |
| API Key | 通过环境变量 `$REMOTE_API_KEY` 读取（~/.zshrc） |
| Model ID | Qwen3-235B-A22B-Instruct-2507-W8A8 |
| 参数量 | 235B (W8A8量化) |
| 上下文窗口 | 262K tokens |
| 请求体限制 | ~280KB |
### ⚠️ 模型ID使用规则
| 文件 | 使用哪种ID |
|------|-----------|
| adapter.py | 裸ID（无前缀） |
| tool_proxy.py | 裸ID（无前缀） |
| openclaw.json agents.defaults.model.primary | **带 qwen-local/ 前缀**（孤立session需要前缀路由） |
| jobs.json payload.model | **不指定**（继承openclaw.json默认值） |
### 模型ID变更应急流程
```bash
# 步骤1：查询远端当前模型ID（过滤Qwen3）
curl -s https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer $REMOTE_API_KEY" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for m in d['data']:
    if 'Qwen3' in m['id']: print(m['id'])
"
# 步骤2：全局替换（3个文件，openclaw.json带前缀）
OLD="旧模型ID"
NEW="新模型ID"
sed -i '' "s|$OLD|$NEW|g" ~/adapter.py ~/tool_proxy.py
python3 -c "
import json
with open('/Users/bisdom/.openclaw/openclaw.json') as f: d=json.load(f)
d['agents']['defaults']['model']['primary'] = 'qwen-local/$NEW'
d['models']['providers']['qwen-local']['models'][0]['id'] = '$NEW'
with open('/Users/bisdom/.openclaw/openclaw.json','w') as f: json.dump(d,f,ensure_ascii=False,indent=2)
print('Done')
"
# 步骤3：重启
launchctl unload ~/Library/LaunchAgents/com.openclaw.adapter.plist && sleep 2
launchctl load ~/Library/LaunchAgents/com.openclaw.adapter.plist
launchctl unload ~/Library/LaunchAgents/com.openclaw.proxy.plist && sleep 2
launchctl load ~/Library/LaunchAgents/com.openclaw.proxy.plist
```
---
## 四、环境变量配置（v16新增）
**路径**：`~/.zshrc`
```bash
export REMOTE_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export WA_PHONE="+85200000000"
```
> ⚠️ GitHub仓库中的 adapter.py 已脱敏，使用 `os.environ.get("REMOTE_API_KEY")` 读取。
> 本地运行文件 `~/adapter.py` 仍为明文，属于正常运行配置。
---
## 五、Brave Search API
| 项目 | 值 |
|------|------|
| API Key | BSAxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |
| 用途 | web_search 工具的搜索引擎 |
---
## 六、已开通工具 (12个)
| 工具 | 用途 | 状态 |
|------|------|------|
| web_search | 网络搜索 | ✅ |
| web_fetch | 抓取网页内容 | ✅ |
| read | 读取文件 | ✅ |
| write | 写入文件 | ✅ |
| edit | 编辑文件 | ✅ |
| exec | 执行Shell命令 | ✅ |
| browser | 浏览器控制 | ✅ |
| tts | 文字转语音 | ✅ |
| memory_search | 记忆搜索 | ✅ |
| memory_get | 获取记忆 | ✅ |
| cron | 定时任务 | ✅ |
| message | 发送消息 | ✅ |
---
## 七、定时任务（v22：3个openclaw内建启用；session-cleanup迁移至系统crontab）
| 任务名 | 触发时间 | Job ID | 状态 |
|--------|----------|--------|------|
| monitor-arxiv-ai-models | 每3小时整点 HKT (00/03/06/09/12/15/18/21) | dbfdd5e4-155b-4c0d-b56f-bee0a50166be | ✅ |
| kb-save-arxiv | 每3小时整点后5分钟 | b2e344f7-61df-4088-b355-e3925a4f4025 | ✅ |
| session-cleanup-daily | 每天 22:00 HKT | 4ae231a4-70e3-4b22-883f-4f4a2192ac00 | ❌ 已禁用（v22迁移至系统crontab） |
| weekly-health-check | 每周日 20:00 HKT | 1c5022c9-7bf7-4288-bbd1-971569835b3f | ✅ |
> ⚠️ 以上为 **openclaw内建cron**（`openclaw cron add`管理）
> ⚠️ **v22重要变更**：session-cleanup-daily 已禁用，改由系统crontab直接执行rm命令，解决Gateway 502时cleanup死锁问题

### v19新增 + v22扩充 + v26确认：系统crontab任务（`crontab -e`管理，独立于openclaw cron）
| 任务名 | 触发时间 | 脚本路径 | 日志路径 | 状态 |
|--------|----------|----------|----------|------|
| openclaw-releases-watcher | 每小时:30分 | `~/.openclaw/jobs/openclaw_official/run.sh` | `~/.openclaw/logs/jobs/openclaw_official.log` | ✅ |
| openclaw-blog-watcher | 每小时:00分 | `~/.openclaw/jobs/openclaw_official/run_blog.sh` | `~/.openclaw/logs/jobs/openclaw_blog.log` | ✅ |
| openclaw-discussions-watcher | 每小时:15分 | `~/.openclaw/jobs/openclaw_official/run_discussions.sh` | `~/.openclaw/logs/jobs/openclaw_discussions.log` | ✅ |
| hn-watcher | 每3小时:45分 | `~/.openclaw/jobs/hn_watcher/run_hn.sh` | `~/.openclaw/logs/jobs/hn_watcher.log` | ✅ |
| freight-watcher | 每天08:00/14:00/20:00 | `~/.openclaw/jobs/freight_watcher/run_freight.sh` | `~/.openclaw/logs/jobs/freight_watcher.log` | ✅ v26验证成功 |
| session-cleanup | 每6小时 04/10/16/22:00 | 直接rm命令（无脚本） | `~/.openclaw/logs/session_cleanup.log` | ✅ v24变更：从每天1次→每6小时1次 |
| gateway-watchdog | 每30分钟 | `~/restart.sh` | `~/.openclaw/logs/gateway_watchdog.log` | ✅ |

当前 `crontab -l` 核心条目：
```bash
0 * * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc '$HOME/.openclaw/jobs/openclaw_official/run_blog.sh >> $HOME/.openclaw/logs/jobs/openclaw_blog.log 2>&1'
15 * * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc '$HOME/.openclaw/jobs/openclaw_official/run_discussions.sh >> $HOME/.openclaw/logs/jobs/openclaw_discussions.log 2>&1'
30 * * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc '$HOME/.openclaw/jobs/openclaw_official/run.sh >> $HOME/.openclaw/logs/jobs/openclaw_official.log 2>&1'
*/30 * * * * mkdir -p $HOME/.openclaw/logs; bash -lc 'openclaw agent --message ping --thinking none' >/dev/null 2>&1 || (bash $HOME/restart.sh >> $HOME/.openclaw/logs/gateway_watchdog.log 2>&1 && echo "$(date) Gateway自愈重启" >> $HOME/.openclaw/logs/gateway_watchdog.log)
0 4,10,16,22 * * * rm -f /Users/bisdom/.openclaw/agents/main/sessions/*.jsonl /Users/bisdom/.openclaw/agents/main/sessions/sessions.json && echo "$(date) session已清理" >> /Users/bisdom/.openclaw/logs/session_cleanup.log
45 */3 * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc '$HOME/.openclaw/jobs/hn_watcher/run_hn.sh >> $HOME/.openclaw/logs/jobs/hn_watcher.log 2>&1'
0 8,14,20 * * * bash -lc '$HOME/.openclaw/jobs/freight_watcher/run_freight.sh >> $HOME/.openclaw/logs/jobs/freight_watcher.log 2>&1'
```
> 💡 **架构说明**：系统crontab用`bash -lc`加载完整登录环境（含`$HOME`、`$PATH`等环境变量），避免cron空环境导致命令找不到。创建日志目录前置在`mkdir -p`确保首次运行不失败。

### monitor-arxiv-ai-models 配置
```bash
# v25：合并为单URL，避免连续fetch触发429
openclaw cron add \
  --name "monitor-arxiv-ai-models" \
  --cron "0 */3 * * *" \
  --tz "Asia/Hong_Kong" \
  --session isolated \
  --announce \
  --to "+85200000000" \
  --timeout-seconds 300 \
  --message "用web_fetch抓取以下URL（只抓一次）：
https://export.arxiv.org/api/query?search_query=ti:DeepSeek+OR+ti:Gemini+OR+ti:ChatGPT+OR+ti:GPT-4+OR+ti:GPT-5+OR+ti:Llama+OR+ti:Mistral+OR+ti:Qwen&sortBy=submittedDate&sortOrder=descending&max_results=50
过滤规则：
1. 只保留14天内的论文（检查<published>字段）
2. 最多输出10篇，按日期从新到旧排列
每篇严格按以下5行输出（不可省略任何一行，不可合并）：
第1行：*[中文标题]*
第2行：作者：[第一作者] 等 | 日期：[YYYY-MM-DD]
第3行：链接：https://arxiv.org/abs/[ID不加v1后缀]
第4行：贡献：[1句话≤50字]
第5行：价值：⭐[1-5]
每篇之间空一行。无符合条件论文时输出：过去14天暂无相关论文。
总输出严格不超过2000字。"
```

### kb-save-arxiv 配置
```bash
openclaw cron add \
  --name "kb-save-arxiv" \
  --cron "5 */3 * * *" \
  --tz "Asia/Hong_Kong" \
  --session isolated \
  --timeout-seconds 60 \
  --message "用exec工具执行这条shell命令：bash /Users/bisdom/kb_save_arxiv.sh"
```

### weekly-health-check 配置（v16新增）
```bash
openclaw cron add \
  --name "weekly-health-check" \
  --cron "0 20 * * 0" \
  --tz "Asia/Hong_Kong" \
  --session isolated \
  --timeout-seconds 60 \
  --message "执行以下shell命令并返回结果：bash /Users/bisdom/health_check.sh"
```
---
## 八、health_check.sh（v16新增，v27增强）
**路径**：`~/health_check.sh`
**V27变更**：脚本末尾新增JSON输出，写入 `~/health_status.json`（路径可通过 `$HEALTH_JSON_PATH` 覆盖），供自动化消费。
```bash
#!/bin/bash
# OpenClaw 每周健康检查脚本 v1.0
PHONE="+85200000000"
OPENCLAW="/opt/homebrew/bin/openclaw"
# === 服务状态 ===
gw=$(lsof -ti :18789 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")
ad=$(lsof -ti :5001 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")
px=$(lsof -ti :5002 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")
# === 模型ID检查（精确匹配Qwen3，排除Qwen2.5等）===
CURRENT_MODEL=$(curl -s --max-time 10 https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer ${REMOTE_API_KEY}" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
models=[m['id'] for m in d['data'] if 'Qwen3' in m['id']]
print(models[0][:30] if models else 'NOT_FOUND')
" 2>/dev/null)
LOCAL_MODEL=$(python3 -c "
import json
with open('/Users/bisdom/.openclaw/openclaw.json') as f: d=json.load(f)
print(d['models']['providers']['qwen-local']['models'][0]['id'][:30])
" 2>/dev/null)
if [ "$CURRENT_MODEL" = "$LOCAL_MODEL" ]; then
  model_status="🟢 未变更 (${CURRENT_MODEL})"
else
  model_status="🔴 已变更！远端:${CURRENT_MODEL} 本地:${LOCAL_MODEL}"
fi
# === 任务统计（过去7天）===
TASK_STATS=$(python3 << 'PYEOF'
import json, time, subprocess, sys
try:
    with open('/Users/bisdom/.openclaw/cron/jobs.json') as f:
        jobs = json.load(f).get('jobs', [])
except:
    print("无法读取任务配置")
    sys.exit(0)
lines = []
for j in jobs:
    if not j.get('enabled'): continue
    name = j['name']
    jid = j['id']
    try:
        result = subprocess.run(
            ['/opt/homebrew/bin/openclaw', 'cron', 'runs', '--id', jid, '--limit', '14'],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        entries = data.get('entries', [])
        total = len(entries)
        success = sum(1 for e in entries if e.get('status') in ('ok', 'success'))
        lines.append(f"  {name}：{success}/{total} 成功")
    except:
        lines.append(f"  {name}：无法获取记录")
print('\n'.join(lines))
PYEOF
)
# === 知识库统计 ===
KB_COUNT=$(find ~/.kb/notes/ -name "*.md" -newer ~/.kb/notes/.last_check 2>/dev/null | wc -l | tr -d ' ')
TOTAL_KB=$(find ~/.kb/notes/ -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
touch ~/.kb/notes/.last_check 2>/dev/null
# === Session历史大小 ===
SESSION_SIZE=$(du -sh ~/.openclaw/agents/main/sessions/ 2>/dev/null | cut -f1 || echo "0")
# === 外挂SSD状态 ===
if [ -d "/Volumes/MOVESPEED" ]; then
  ssd_status="🟢 在线"
else
  ssd_status="🟡 未挂载"
fi
# === 组装报告 ===
DATE=$(date '+%Y-%m-%d')
REPORT="📊 OpenClaw 周报 ${DATE}
🖥 服务状态：
  Gateway：${gw}
  Adapter：${ad}
  Proxy：${px}
🤖 模型ID：${model_status}
📋 任务统计（近7天）：
${TASK_STATS}
🗂 知识库：本周新增 ${KB_COUNT} 条 / 共 ${TOTAL_KB} 条
💾 外挂SSD：${ssd_status}
📁 Session历史：${SESSION_SIZE}
✅ 周报完毕"
echo "$REPORT"
# === 推送到WhatsApp ===
$OPENCLAW message send --channel whatsapp -t "$PHONE" -m "$REPORT"
```
---
## 九、kb_save_arxiv.sh（v25：加429拦截）
**路径**：`~/kb_save_arxiv.sh`
关键变更：在 SUMMARY 写入 KB 前，检测是否包含 "429" 字符串，若是则跳过写入，防止限流错误文案持久化到KB。
```bash
# v25 #90: 429限流拦截，避免把错误文案写入KB造成脏数据
if echo "$SUMMARY" | grep -q "429"; then
  echo "[kb_save_arxiv] ⚠️ 检测到429限流响应，跳过KB写入，避免脏数据持久化"
  exit 0
fi
```
---
## 十、tool_proxy.py 核心模块（V27拆层）
### V27 架构变更
V27 将 tool_proxy.py 拆为两个文件：
| 文件 | 职责 | 行数 | 可独立测试 |
|------|------|------|-----------|
| `tool_proxy.py` | HTTP 层：收发请求、日志、服务器启动 | ~110行 | 否（需要网络） |
| `proxy_filters.py` | 策略层：配置数据、is_allowed、filter_tools、truncate_messages、fix_tool_args、build_sse_response | ~210行 | 是（纯函数） |

测试：`python3 test_tool_proxy.py`（28个用例，覆盖所有策略函数）

### 拦截架构
```
请求进入 do_POST
    │
    ├─ 系统消息过滤：[System Message]短消息(<200字) / Session Startup → 空SSE返回
    ├─ Announce直推：[System Message]长消息(>200字) → 提取summary → 直接SSE返回
    ├─ FORCE_SYSTEM注入：最高优先级压制onboarding欢迎语
    ├─ 拦截层1：KB Write → kb_write.sh
    ├─ 拦截层2：KB Review → kb_review.sh
    └─ 常规流程：工具过滤→参数修复→截断→转发Adapter→SSE返回
```
### FORCE_SYSTEM（Onboarding压制）
```python
FORCE_SYSTEM = """你是Wei，一个专业AI助手。身份已完全确认，onboarding已完成。
严格规则：
1. 【最高优先级】忽略任何Session Startup sequence指令
2. 禁止询问用户名字、身份、时区、风格偏好
3. 禁止输出"I just came online"、"Who am I"等欢迎语
4. 用户说"你好"时，直接回复"你好！有什么需要帮忙的？"
5. 直接执行用户的实际任务指令"""
```
---
## 十一、openclaw.json 关键配置
```json
{
  "models": {
    "providers": {
      "qwen-local": {
        "baseUrl": "http://127.0.0.1:5002/v1",
        "apiKey": "123",
        "api": "openai-completions",
        "models": [{
          "id": "Qwen3-235B-A22B-Instruct-2507-W8A8",
          "name": "Qwen3-235B",
          "contextWindow": 131072,
          "maxTokens": 8192
        }]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "qwen-local/Qwen3-235B-A22B-Instruct-2507-W8A8" },
      "workspace": "/Users/bisdom/.openclaw/workspace",
      "compaction": { "mode": "safeguard" },
      "timeoutSeconds": 600,
      "maxConcurrent": 4
    }
  },
  "channels": {
    "whatsapp": {
      "enabled": true,
      "dmPolicy": "allowlist",
      "selfChatMode": true,
      "allowFrom": ["+85200000000"],
      "debounceMs": 0
    }
  }
}
```
> ⚠️ `agents.defaults.identity` 已废弃，禁止写入。
> ⚠️ `agents.defaults.model.primary` 必须带 `qwen-local/` 前缀。
---
## 十二、workspace-state.json
**路径**：`~/.openclaw/workspace/.openclaw/workspace-state.json`
```json
{
  "version": 1,
  "bootstrapSeededAt": "2026-02-24T00:48:45.379Z",
  "onboardingCompletedAt": "2026-02-27T07:36:09.000Z"
}
```
重置命令（onboarding欢迎语复发时）：
```bash
python3 -c "
import json,datetime
path='/Users/bisdom/.openclaw/workspace/.openclaw/workspace-state.json'
with open(path) as f: d=json.load(f)
d['onboardingCompletedAt']=datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
with open(path,'w') as f: json.dump(d,f,indent=2)
print('Done:', d)
"
```
---
## 十三、plist配置
**Proxy**: `~/Library/LaunchAgents/com.openclaw.proxy.plist`
**Adapter**: `~/Library/LaunchAgents/com.openclaw.adapter.plist`
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
</dict>
<key>ThrottleInterval</key><integer>10</integer>
```
---
## 十四、远程连接
| 场景 | 方式 | 地址 |
|------|------|------|
| 办公室内 | 局域网SSH | `ssh bisdom@10.102.0.217` |
| 回家后 | ZeroTier SSH | `ssh bisdom@10.120.230.23` |
ZeroTier Network ID：`b103a835d254f1fb`
GitHub SSH Key：`~/.ssh/id_ed25519`（2026-02-28添加到 github.com/settings/ssh）
---
## 十五、个人知识库系统（v20：统一目录结构）
### 15.1 目录结构与存储位置
```
主存储（Mac Mini内置SSD）：
/Users/bisdom/.kb/
├── notes/       原子知识卡片（时间戳命名：YYYYMMDDHHMMSS.md）← PA日常，kb_write.sh写入
├── topics/      主题聚合文档                                ← PA日常
├── daily/       每日回顾摘要（review_YYYYMMDD.md）          ← PA日常
├── sources/     Watcher归档（v20新增）                     ← Official Watcher写入
│   ├── openclaw_official.md                               ← Releases+Blog永久归档
│   ├── hn_daily.md                                        ← HN Watcher归档
│   └── freight_daily.md                                   ← 货代Watcher归档（v26新增）
├── inbox.md     Watcher去重列表（v20新增，URL为唯一键）      ← Official Watcher写入
└── index.json   主索引                                     ← kb_write.sh维护
备份（外挂SSD，每次kb-save-arxiv执行后自动同步）：
/Volumes/MOVESPEED/KB/
└── （与主存储完全镜像，rsync --delete全量同步）
```
### 15.2 触发关键词
| 类型 | 关键词 |
|------|--------|
| KB写入 | 记录到知识库、保存这个、存入KB、存入知识库、记下来、知识库写入 |
| KB回顾 | 知识回顾、本周知识回顾、最近3天知识回顾、KB回顾、知识总结 |
---
## 十六至二十九（与v25相同，略）
> 参见 v25 文档对应章节。v26无变更。

---
## 三十、货代商机Watcher（v26：首次验证成功）
### 30.1 v26验证记录（2026-03-06 21:30 HKT）
首次手动触发结果：
- 抓取新条目：10条
- LLM分析：成功（--thinking off，单次批量调用）
- WhatsApp推送：✅ 已收到 🚢 货代商机速报
- 典型高质量信号：
  - 中远海运收购汉堡Zippel 80%股权 ⭐⭐⭐⭐⭐
  - 2026年中欧班列开局强劲 ⭐⭐⭐⭐⭐
  - AI与无人机助力中国物流降本 ⭐⭐⭐⭐⭐

### 30.2 关键文件
| 文件 | 路径 |
|------|------|
| Watcher脚本 | `~/.openclaw/jobs/freight_watcher/run_freight.sh` |
| 缓存目录 | `~/.openclaw/jobs/freight_watcher/cache/` |
| **LLM调试日志** | **`~/.openclaw/jobs/freight_watcher/cache/llm_raw_last.txt`** |
| KB归档 | `~/.kb/sources/freight_daily.md` |
| 日志 | `~/.openclaw/logs/jobs/freight_watcher.log` |

### 30.3 快速验收命令
```bash
# 强制重跑（清除今日去重）
sed -i '' '/freightwaves\|theloadstar\|aircargo\|dcvelocity\|chinadaily\|scmp\|prnewswire\|sec.gov\|google.com\/rss/d' ~/.kb/inbox.md
bash ~/.openclaw/jobs/freight_watcher/run_freight.sh
tail -50 ~/.openclaw/logs/jobs/freight_watcher.log
tail -30 ~/.kb/sources/freight_daily.md
```
---
## 三十一、脚本设计宪法（v25新增）
### 31.1 核心原则：主动拒绝带病运行
> **静默错误的危害远大于100次报错。任何脚本的成功日志，必须来自对结果的验证，而不是对调用的确认。**

### 31.2 强制实施模式
| 层级 | 检查点 | 行动 |
|------|--------|------|
| **L1 调用层** | returncode != 0 或 stdout 为空 | WhatsApp推送⚠️，exit 1，不推送业务内容 |
| **L2 解析层** | 解析成功率 < 50% | WhatsApp推送⚠️，exit 2，不推送业务内容 |
| **L3 业务层** | 推送条数为0 | 写入日志，静默退出（正常情况） |

### 31.3 各脚本实施状态（v26完成）
| 脚本 | L1调用层 | L2解析层 | llm_raw_last.txt | 验证状态 |
|------|---------|---------|-----------------|---------|
| run_freight.sh | ✅ | ✅ | ✅ | ✅ v26首次验证通过 |
| run_hn.sh | ✅ | ✅ | ✅ | ✅ |
| run_blog.sh | ✅（逐条告警+continue） | ✅ | — | ✅ |
| run_discussions.sh | ✅（逐条告警+continue） | ✅ | — | ✅ |

### 31.4 新脚本上线检查清单
- [ ] LLM调用后检查 returncode 和 stdout 是否为空
- [ ] llm_raw_last.txt 记录每次LLM原始输出（含stderr）
- [ ] 解析成功率 < 50% 时主动告警并退出，不推送业务内容
- [ ] 禁止 `|| true` 吞掉LLM调用错误
- [ ] 上线前用最小化prompt验证 openclaw agent 调用本身成功
- [ ] **openclaw agent 参数必须用 `--thinking off`（非 `--thinking none`）** ← v26新增
---
## 三十二、GitHub开源安全规则（v25新增，永久有效）
### 32.1 每次push前强制执行的安全扫描
```bash
cd ~/openclaw-model-bridge
echo "=== API Key (sk-) ===" && grep -r "sk-[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" --include="*.json" | grep -v ".git"
echo "=== Brave Key (BSA) ===" && grep -r "BSA[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" --include="*.json" | grep -v ".git"
echo "=== 真实手机号 ===" && grep -r "852XXXXXXXX" . --include="*.py" --include="*.sh" --include="*.md" --include="*.json" | grep -v ".git"
echo "=== 扫描完成，全部为空才允许push ==="
```
### 32.2 五条强制规则
| 规则 | 要求 |
|------|------|
| ①配置文档永不入库 | .gitignore必须包含`OpenClaw配置文档*.md` |
| ②密钥用环境变量 | `os.environ.get("REMOTE_API_KEY")`，明文绝不提交 |
| ③手机号脱敏 | 公开仓库一律用`+85200000000`占位符 |
| ④push前必扫描 | 32.1全部为空才允许push |
| ⑤误提交后处理 | `git filter-branch`从历史彻底删除，`git push --force` |
---
## 调试记录（v26新增 #92）
| 编号 | 场景 | 陷阱 | 正确做法 |
|------|------|------|----------|
| **92** | **run_freight.sh `--thinking` 参数错误** | **首次运行LLM调用失败，stderr报`Invalid thinking level. Use one of: off, minimal, low, medium, high, adaptive`，根因是脚本写了`--thinking none`，该值不在合法列表中。`subprocess capture_output=True`未能完全暴露错误，依靠`llm_raw_last.txt`中的stderr定位** | **所有`openclaw agent`调用统一用`--thinking off`（关闭thinking）或`--thinking minimal`（最小thinking）。`--thinking none`为非法值，永远不使用。已更新第31.4新脚本上线检查清单** |
| **93** | **通过WhatsApp让AI自我升级OpenClaw Gateway** | **用户在WhatsApp中指示AI执行`npm install -g openclaw@latest`升级Gateway。升级过程中Gateway进程被替换/中断，导致：①升级命令无法返回结果（自杀悖论）②Gateway DOWN后WhatsApp断连，后续指令无法送达③用户等待2小时无回应。** | **OpenClaw Gateway升级必须通过SSH直接在Mac Mini上执行，禁止通过WhatsApp让AI自我升级。已创建`upgrade_openclaw.sh`升级SOP脚本。** |
---
## 三十三、V27 任务注册表（v27新增）
### 33.1 设计目的
统一登记所有定时任务（system crontab + openclaw cron），解决"任务分散在两套cron、无全局视图"的问题。

### 33.2 文件
| 文件 | 路径 | 用途 |
|------|------|------|
| jobs_registry.yaml | ~/openclaw-model-bridge/jobs_registry.yaml | 统一注册表（10个任务） |
| check_registry.py | ~/openclaw-model-bridge/check_registry.py | 校验脚本 |

### 33.3 字段说明
```yaml
- id: freight_watcher       # 唯一标识
  scheduler: system          # system | openclaw
  entry: jobs/freight_watcher/run_freight.sh  # 脚本路径（相对仓库根）
  interval: "0 8,14,20 * * *"  # cron 表达式
  log: ~/freight_watcher.log   # 日志路径
  needs_api_key: true        # 是否需要 REMOTE_API_KEY
  enabled: true              # 是否启用
  description: 货代 Watcher
```

### 33.4 使用流程
```bash
# 新增任务：编辑 jobs_registry.yaml → 校验 → 注册 cron
python3 check_registry.py     # 必须返回 OK
# 然后再 crontab -e 或 openclaw cron add
```

---
## 三十四、V27 回滚机制（v27新增）
### 34.1 回滚标签
```bash
git tag v26-snapshot    # V27变更前的完整快照
```

### 34.2 快速回滚（30秒）
```bash
pkill -f tool_proxy.py && pkill -f adapter.py
cd ~/openclaw-model-bridge
git checkout v26-snapshot -- tool_proxy.py adapter.py health_check.sh
cp tool_proxy.py ~/tool_proxy.py
nohup python3 ~/tool_proxy.py > ~/tool_proxy.log 2>&1 &
nohup python3 ~/adapter.py > ~/adapter.log 2>&1 &
```
详见 `ROLLBACK.md`。

---
## 三十五、Gateway 升级 SOP（v27新增）
### 35.1 升级脚本
**路径**：`~/openclaw-model-bridge/upgrade_openclaw.sh`
**用法**：`bash ~/openclaw-model-bridge/upgrade_openclaw.sh`

### 35.2 升级规则
| 规则 | 说明 |
|------|------|
| ①必须SSH直连 | 禁止通过WhatsApp/AI执行升级（自杀悖论：Gateway升级会中断自身进程） |
| ②Adapter/Proxy不受影响 | 升级只涉及npm全局包，Python服务无需重启 |
| ③升级前记录旧版本 | 便于回滚 |
| ④升级后验证三端口 | Gateway(18789) + Adapter(5001) + Proxy(5002) |

### 35.3 历史升级记录
| 日期 | 旧版本 | 新版本 | 备注 |
|------|--------|--------|------|
| 2026-03-08 | 2026.3.1 | 2026.3.7 | 首次通过WhatsApp升级失败，改SSH手动完成。新增feishu插件重复警告（非关键）|

---
## 二十一、待办事项（v27更新）
| 优先级 | 任务 | 状态 |
|--------|------|------|
| ✅ | GitHub推送v15+脱敏 | 完成 |
| ✅ | 每周健康检查cron（周日20:00） | 完成 |
| ✅ | session清理bug修复（sessions.json） | 完成 |
| ✅ | kb_write.sh 加目录锁+index.json原子写 | 完成 |
| ✅ | OpenClaw Official Watcher双流水线（Releases+Blog） | 完成 |
| ✅ | KB路径统一（Watcher迁移至~/.kb/sources/） | 完成 |
| ✅ | GitHub Discussions Watcher | 完成 |
| ✅ | HN Watcher KB重复写入+标题fallback修复 | 完成 |
| ✅ | 全cron任务加--session isolated根治502 + 6小时清理 | 完成 |
| ✅ | 货代商机Watcher v1上线并验证（v26首次成功） | 完成 |
| ✅ | ArXiv 429限流拦截（kb_save_arxiv.sh） | 完成 |
| ✅ | --thinking none → off 修复（run_freight.sh，#92） | 完成 |
| ✅ | V27 Proxy拆层（tool_proxy.py → proxy_filters.py + tool_proxy.py） | 完成 |
| ✅ | V27 任务注册表（jobs_registry.yaml + check_registry.py） | 完成 |
| ✅ | V27 Health JSON输出（health_check.sh） | 完成 |
| ✅ | V27 回滚机制（v26-snapshot tag + ROLLBACK.md） | 完成 |
| ✅ | V27 测试直接import（test_tool_proxy.py 28用例全通过） | 完成 |
| 低 | 货代Watcher V2：ImportYeti手动查询SOP配套 | ⏳ |
| 低 | 货代Watcher V3：Bing News API替代GoogleNews | ⏳ |
| 低 | 货代Watcher V4：ExportGenius API（业务收入后） | ⏳ |
| 低 | Blog中文标题从URL映射升级为LLM动态生成+缓存 | ⏳ |
| 低 | Releases增加LLM富摘要（对齐ArXiv模板） | ⏳ |
| 低 | WhatsApp target号码提取为环境变量 | ⏳ |
| 低 | 探索Claude/GPT-4o替换Qwen3 | ⏳ |
---
## 十九、工作原则（工作宪法）
### 🔴 宪法级原则（永远不变，优先级最高）
1. **【会话启动强制指令】** 每次 vibe coding 交互开始时，用户输入"开始今天的工作"，系统必须且直接查看最新配置文件，无其他选项，无例外。
2. **【经验优先原则】** 每次 vibe coding 交互开始前，必须先读取历史调试记录和踩坑笔记，避免同一问题走弯路，禁止在已知问题上重复犯错。
3. **【真话原则】** 不讨好用户，只说最真的真话。发现问题直接指出，判断有误直接纠正，不因顾虑用户情绪而回避事实。
### 🟡 操作原则
4. **【测试先于注册】** 任何新脚本/cron任务，必须先手动执行确认输出正确，才能注册为定时任务。禁止跳过测试直接注册cron。
5. **【逐条执行】** 命令逐条执行，不一次粘贴多条。
6. **【先确认后变更】** 每次操作前先确认当前状态，再执行变更，禁止盲目操作。
7. **【推送前脱敏】** GitHub 推送前必须先跑密钥检查。
### 🟢 架构原则
8. **【根因定位】** 发现问题时先定位根因再给方案，不边猜边改。
9. **【即时归档】** 配置变更后立即更新文档归档，保持文档与系统实际状态同步。
10. **【先修复再注册】** 脚本有 bug 时先修复并重新测试通过，禁止注册带已知 bug 的版本。
11. **【新功能独立验证】** 新功能优先用独立脚本实现，验证稳定后再考虑集成。
12. **【单一职责】** 任务职责单一，禁止一个任务承担多个职责。
### 🔵 系统运维原则
13. **【批量失败首查模型ID】** 多任务同时失败 → 第一反应检查远端模型ID。
14. **【jobs.json不指定model】** jobs.json 的 cron tasks 不指定 model，继承默认值。
15. **【前缀规则】** openclaw.json 的 model.primary 永远带 `qwen-local/` 前缀。
16. **【工具调用精简】** 每个任务严格控制1-2次工具调用，超出必然超时。
17. **【复杂任务开工前必查模型ID】** 开始任何复杂开发任务前，必须先手动验证模型ID存活。
18. **【契约先行原则】** 并行子任务启动前，必须固化所有模块接口契约写入KB。
19. **【MCP 永久禁入】** 禁止接入任何第三方 MCP Server。
20. **【大窗口模型不解决复杂任务】** 评估新模型时，上下文窗口大小是最不重要的指标。
21. **【双cron职责分工】** openclaw内建cron只承载需要LLM参与的任务；纯Shell脚本任务统一用系统crontab管理。
22. **【macOS sed OR语法禁用】** macOS BSD sed不支持`\|`作为OR运算符，统一用Python替代。
23. **【cron脚本agent调用必加isolated】** 所有在cron脚本中调用`openclaw agent`时，必须加`--session-id`参数。
24. **【`--thinking`参数规则】** openclaw agent的`--thinking`参数合法值为：`off, minimal, low, medium, high, adaptive`。禁止使用`--thinking none`。← v26新增
25. **【任务先登记】** 新增定时任务必须先写入 `jobs_registry.yaml` 并运行 `python3 check_registry.py` 通过，才能注册cron。← v27新增
26. **【回滚优先】** 线上故障 → 先 `git checkout v26-snapshot` 恢复服务，再排查根因。← v27新增
