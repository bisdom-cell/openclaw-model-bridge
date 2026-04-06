# Tool Ontology 差异对比报告
> 生成时间: 2026-04-06 11:44:59
> 宪法第四条：强制差异对比表格

## 总览
| 指标 | 值 |
|------|-----|
| 对比项总数 | 81 |
| ✅ 一致 | 81 (100.0%) |
| ⚠️ 偏差 | 0 |
| ❌ 缺失 | 0 |
| 一致率 | **100.0%** |

## ✅ 工具白名单 (16/16)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | agents_list | ✓ | ✓ |  |
| ✅ | cron | ✓ | ✓ |  |
| ✅ | edit | ✓ | ✓ |  |
| ✅ | exec | ✓ | ✓ |  |
| ✅ | image | ✓ | ✓ |  |
| ✅ | memory_get | ✓ | ✓ |  |
| ✅ | memory_search | ✓ | ✓ |  |
| ✅ | message | ✓ | ✓ |  |
| ✅ | read | ✓ | ✓ |  |
| ✅ | sessions_history | ✓ | ✓ |  |
| ✅ | sessions_send | ✓ | ✓ |  |
| ✅ | sessions_spawn | ✓ | ✓ |  |
| ✅ | tts | ✓ | ✓ |  |
| ✅ | web_fetch | ✓ | ✓ |  |
| ✅ | web_search | ✓ | ✓ |  |
| ✅ | write | ✓ | ✓ |  |

## ✅ 前缀匹配 (1/1)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | browser | ✓ | ✓ |  |

## ✅ Schema (15/15)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | agents_list | props=[], req=[] | props=[], req=[] |  |
| ✅ | cron | props=['action', 'name', 'schedule', 'sessionTarge | props=['action', 'name', 'schedule', 'sessionTarge |  |
| ✅ | edit | props=['path', 'old_text', 'new_text'], req=['path | props=['path', 'old_text', 'new_text'], req=['path |  |
| ✅ | exec | props=['command'], req=['command'] | props=['command'], req=['command'] |  |
| ✅ | memory_get | props=['key'], req=['key'] | props=['key'], req=['key'] |  |
| ✅ | memory_search | props=['query'], req=['query'] | props=['query'], req=['query'] |  |
| ✅ | message | props=['to', 'text'], req=['to', 'text'] | props=['to', 'text'], req=['to', 'text'] |  |
| ✅ | read | props=['path'], req=['path'] | props=['path'], req=['path'] |  |
| ✅ | sessions_history | props=['sessionId'], req=['sessionId'] | props=['sessionId'], req=['sessionId'] |  |
| ✅ | sessions_send | props=['sessionId', 'message'], req=['sessionId',  | props=['sessionId', 'message'], req=['sessionId',  |  |
| ✅ | sessions_spawn | props=['agent', 'message'], req=['agent', 'message | props=['agent', 'message'], req=['agent', 'message |  |
| ✅ | tts | props=['text'], req=['text'] | props=['text'], req=['text'] |  |
| ✅ | web_fetch | props=['url'], req=['url'] | props=['url'], req=['url'] |  |
| ✅ | web_search | props=['query'], req=['query'] | props=['query'], req=['query'] |  |
| ✅ | write | props=['path', 'content'], req=['path', 'content'] | props=['path', 'content'], req=['path', 'content'] |  |

## ✅ 参数集合 (21/21)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | agents_list | [] | [] |  |
| ✅ | browser_click | ['profile', 'selector', 'target'] | ['profile', 'selector', 'target'] |  |
| ✅ | browser_navigate | ['profile', 'target', 'url'] | ['profile', 'target', 'url'] |  |
| ✅ | browser_snapshot | ['profile', 'target'] | ['profile', 'target'] |  |
| ✅ | browser_type | ['profile', 'selector', 'target', 'text'] | ['profile', 'selector', 'target', 'text'] |  |
| ✅ | cron | ['action', 'command', 'id', 'job', 'name', 'payloa | ['action', 'command', 'id', 'job', 'name', 'payloa |  |
| ✅ | data_clean | ['action', 'file', 'fix_case_cols', 'ops'] | ['action', 'file', 'fix_case_cols', 'ops'] |  |
| ✅ | edit | ['new_text', 'old_text', 'path'] | ['new_text', 'old_text', 'path'] |  |
| ✅ | exec | ['command'] | ['command'] |  |
| ✅ | memory_get | ['key'] | ['key'] |  |
| ✅ | memory_search | ['query'] | ['query'] |  |
| ✅ | message | ['text', 'to'] | ['text', 'to'] |  |
| ✅ | read | ['path'] | ['path'] |  |
| ✅ | search_kb | ['query', 'recent_hours', 'source'] | ['query', 'recent_hours', 'source'] |  |
| ✅ | sessions_history | ['sessionId'] | ['sessionId'] |  |
| ✅ | sessions_send | ['message', 'sessionId'] | ['message', 'sessionId'] |  |
| ✅ | sessions_spawn | ['agent', 'message'] | ['agent', 'message'] |  |
| ✅ | tts | ['text'] | ['text'] |  |
| ✅ | web_fetch | ['url'] | ['url'] |  |
| ✅ | web_search | ['query'] | ['query'] |  |
| ✅ | write | ['content', 'path'] | ['content', 'path'] |  |

## ✅ 参数别名 (4/4)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | exec.command | ['bash', 'cmd', 'script', 'shell'] | ['bash', 'cmd', 'script', 'shell'] |  |
| ✅ | read.path | ['file', 'file_path', 'filename', 'filepath'] | ['file', 'file_path', 'filename', 'filepath'] |  |
| ✅ | web_search.query | ['keyword', 'q', 'search', 'search_query'] | ['keyword', 'q', 'search', 'search_query'] |  |
| ✅ | write.content | ['body', 'data', 'file_content', 'text'] | ['body', 'data', 'file_content', 'text'] |  |

## ✅ 自定义工具 (2/2)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | data_clean | ✓ schema一致 | ✓ schema一致 |  |
| ✅ | search_kb | ✓ schema一致 | ✓ schema一致 |  |

## ✅ 浏览器约束 (2/2)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | valid_profiles | ['chrome', 'openclaw'] | ['chrome', 'openclaw'] |  |
| ✅ | default_profile | openclaw | openclaw |  |

## ✅ 策略值 (3/3)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | max_request_bytes | 200000 | 200000 |  |
| ✅ | media_max_age_seconds | 300 | 300 |  |
| ✅ | max_tools | 12 | 12 |  |

## ✅ Rationale覆盖 (17/17)

| 状态 | 项目 | 硬编码 | 本体 | 备注 |
|------|------|--------|------|------|
| ✅ | tool_admission.whitelist_only | N/A | 有 |  |
| ✅ | tool_admission.max_tools | N/A | 有 |  |
| ✅ | tool_admission.no_tools_marker | N/A | 有 |  |
| ✅ | request_limits.max_request_bytes | N/A | 有 |  |
| ✅ | request_limits.media_max_size_bytes | N/A | 有 |  |
| ✅ | request_limits.media_max_age_seconds | N/A | 有 |  |
| ✅ | parameter_healing.alias_resolution | N/A | 有 |  |
| ✅ | parameter_healing.browser_profile_default | N/A | 有 |  |
| ✅ | parameter_healing.extra_param_stripping | N/A | 有 |  |
| ✅ | routing.has_tools_is_complex | N/A | 有 |  |
| ✅ | routing.no_tools_marker_is_simple | N/A | 有 |  |
| ✅ | routing.long_conversation_is_complex | N/A | 有 |  |
| ✅ | routing.multimodal_is_complex | N/A | 有 |  |
| ✅ | routing.short_simple_conversation | N/A | 有 |  |
| ✅ | context_management.aggressive_truncation | N/A | 有 |  |
| ✅ | context_management.moderate_truncation | N/A | 有 |  |
| ✅ | context_management.system_msg_truncation | N/A | 有 |  |

## 宪法合规检查

| 宪法条款 | 状态 |
|----------|------|
| 第一条：非破坏性引入 | ✅ proxy_filters.py 保留全部硬编码 |
| 第二条：一致性安全网 | ✅ 100% 一致 |
| 第三条：每条规则有 rationale | ✅ 17/17 规则有 rationale |
| 第四条：差异对比表格 | ✅ 本报告 |
