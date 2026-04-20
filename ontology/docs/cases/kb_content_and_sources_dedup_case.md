# KB 数据层双血案：内容污染 × 源文件重复爆炸

**版本**：V37.6
**日期**：2026-04-11
**血案类归属**：MR-4 (silent failure is a bug) 第三次演出
**相关血案**：
- V37.4.3 PA 告警污染 (`pa_alert_contamination_case.md`) — 同属 content blocks 结构处理盲区
- V37.5 kb_review 六 bug 静默降级 (`kb_review_silent_degradation_case.md`) — 同属"效果层静默失败"

---

## 一、触发事件

2026-04-11 00:15 kb_evening cron 运行后 kb_dedup 自动报告：

```
📊 KB 概览：
   Notes: 286 个文件 / Index: 282 条      ← 4 条孤儿 note
   Sources: 14 个文件 (1282.7 KB)

🔴 精确重复 Notes: 2 个
   [[[{'type': 'text', 'text': 'Qwen3-235B 工具] × 2      ← Python repr 污染

📄 Sources 重复行: 438 行
   ontology_sources.md: 183 行重复          ← 最严重
   dblp_daily.md: 77 行重复
   arxiv_daily.md: 42 行重复
   rss_blogs.md: 40 行重复
   freight_daily.md: 36 行重复
```

两个独立但相关的数据质量 bug 同时暴露。用户问："这个效果如何？是否也可以优化下"。

---

## 二、完整因果链架构图

### Bug 1：Content blocks 字符串化污染 KB notes 标题

```
HH:MM  [WhatsApp 用户] 多模态问题（图片 + 文字）
       │
       ├─ Gateway (:18789)
       │  └─ 接收 WhatsApp → 转发 OpenAI chat/completions 请求
       │
       ├─ Tool Proxy (:5002)
       │  │
       │  ├─ tool_proxy.py:947 — 消息捕获热路径
       │  │  elif m.get("content"):
       │  │      log(f"TEXT: {len(str(m['content']))} chars")   ← BUG：str()
       │  │      _capture_conversation_turn(
       │  │          body.get("messages", []),
       │  │          str(m["content"])                            ← 记录到 JSONL
       │  │      )
       │  │
       │  ├─ m["content"] 为 OpenAI 多模态规范的 list：
       │  │  [{"type": "text", "text": "Qwen3-235B 工具路由问题"}]
       │  │
       │  ├─ str([{"type": "text", "text": "..."}]) = ?
       │  │  Python 标准行为：返回 repr 字符串：
       │  │  "[{'type': 'text', 'text': 'Qwen3-235B 工具路由问题'}]"
       │  │
       │  └─ _capture_conversation_turn 把 repr 字符串写入
       │     ~/.kb/conversations/YYYY-MM-DD.jsonl 作为 user message
       │
HH:MM  [kb_harvest_chat cron 06:00 每日提炼]
       │
       ├─ kb_harvest_chat.py 读取 conversations JSONL
       ├─ MapReduce 分段提炼 + 去重 + LLM key_points 抽取
       ├─ line 238: content = f"[{date_str}对话精华] {key_points}"
       │  └─ key_points 来自 LLM 对脏数据的总结
       │
       ├─ LLM 看到 `[{'type': 'text', 'text': 'Qwen3'}]` 这种 repr
       │  无法区分 "Python 数据结构" 和 "用户要讨论的内容"
       │  生成 note title: `[[{'type': 'text', 'text': 'Qwen3-235B 工具路由`
       │
       └─ 写入 ~/.kb/notes/ + index.json
          Title 里永久留下 Python repr 污染
          ↓
HH:MM  [kb_dedup cron 检查]
       │
       └─ 两天不同对话产生完全相同的 repr 污染 title
          → 精确 summary 匹配 → 报 "exact duplicate × 2"
          
          用户第一次看到这条 note 的标题：
          `[[{'type': 'text', 'text': 'Qwen3-235B 工具] × 2`
          ↑ 才意识到被污染
```

### Bug 2：Sources 文件 H2 重复追加爆炸

```
HH:MM  [cron N] 14 个 job 任意一个触发（ontology_sources 最严重）
       │
       ├─ jobs/ontology_sources/run_ontology_sources.sh
       │  RSS 抓取 → LLM 摘要 → $MSG_FILE 生成
       │  DAY="$(date '+%Y-%m-%d')"  ← 只精度到天
       │  │
       │  └─ line 411:
       │     {
       │         echo ""
       │         echo "## ${DAY}"           ← 同一天多次运行都是同一个 marker
       │         cat "$MSG_FILE"
       │     } >> "$KB_SRC"                 ← 直接追加，零幂等检查
       │
HH:MM  [同一天 20:00 run]（ontology_sources 2x/day, freight 3x/day）
       │
       ├─ SEEN_FILE 缓存过滤了已推送的 URL（只防重复 WhatsApp 推送）
       ├─ 但 $MSG_FILE 仍然非空（有新 RSS 条目）
       │
       └─ 再次追加 `## 2026-04-11` section 到 sources 文件
          ├─ 文件里现在有两个 `## 2026-04-11` section
          ├─ 两个 section 的内容不同（第二次是新 RSS 条目）
          └─ 但 H2 marker 重复 → kb_dedup 算法视角下产生"重复"
          
HH:MM  [kb_evening cron kb_dedup 扫描]
       │
       ├─ find_duplicate_source_lines 用 file-level seen set (BUG)
       │  seen = set()
       │  for line in lines:
       │      if line.startswith("##"): continue     ← header 跳过
       │      if line in seen: removed += 1          ← 但 content 仍 file 级 dedup
       │      seen.add(line)
       │
       ├─ 438 行被计为 "duplicate"
       │  ↑ 其中两种混合：
       │  (a) V37.6 前 job 反复 append 产生的真重复（bug）
       │  (b) RSS 跨日期合法 rolling 产生的跨 H2 重复（非 bug）
       │
       └─ --apply 会把两种一起删掉 → 历史丢失
```

---

## 三、三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | OpenAI 多模态规范把 content 设计成 `str \| list[block]` union type；14 个 cron job 长期使用 `} >> $KB_SRC` 反模式 | 类型歧义 + 14 处重复 bug class |
| **放大器** | `str()` 对 list 产生 Python repr 而非文本（Python 标准库 API）；SEEN_FILE 只防 WhatsApp 推送不防源文件追加；kb_dedup file-level seen set 把合法跨 H2 重复当 bug；kb_dedup 只扫 index.entries 漏掉孤儿 note | 暴力转换 + 缓存层错配 + 算法盲区 + 扫描遗漏 |
| **掩护者** | KB 污染无任何 error code / 告警；sources 文件无 watchdog 监控行数爆炸；kb_dedup.py 本身算法有 bug 导致"事后清理"也不正确；test_tool_proxy.py 从未用过多模态 content 构造测试 | 静默污染 + 补救工具自身损坏 + 测试盲区 |

---

## 四、时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| ~3 月 | V30.3 添加多模态图片理解（Qwen2.5-VL-72B 路由）+ proxy 消息捕获热路径 | content 变成 list 但捕获路径用 str() |
| ~3 月 | 14 个 cron job 复制粘贴相同的 `} >> $KB_SRC` pattern 作为"永久归档"步骤 | 14 处同一 bug class |
| 2026-04-08 | kb_harvest_chat V37.1 MapReduce 升级，开始从 conversations 提炼 KB notes | 脏数据流入 KB 通路建立 |
| 2026-04-10 | kb_evening 首次报告：2 条精确重复 note (含 repr 污染) + 438 行 sources 重复 | 数据质量拐点 |
| 2026-04-11 00:15 | 第二次 kb_evening 报告：同样模式，证明是持续性 bug 而非一次性 | 用户警觉 |
| 2026-04-11 白天 | 用户要求 "A → B 而且还要彻底检查之前代码的潜在bug" | V37.6 闭环启动 |

---

## 五、为什么以前没发生（条件组合分析）

| 条件 | V37.6 之前 | V37.6 血案爆发时 |
|------|-----------|------------------|
| 多模态对话频率 | 很少图片消息 | 用户开始大量用多模态问答 |
| content 类型 | 大部分是纯 str | list of blocks 占比上升 |
| KB 索引覆盖 | 282/286 基本一致 | 4 条孤儿 note 持续存在 |
| cron job 数量 | V27 前只有 5-6 个 | V37.x 扩展到 14 个共享同一 bug pattern |
| kb_harvest_chat | 未启用（V37.1 才上线） | 开始自动提炼 → 污染有了通路 |
| kb_dedup 扫描频率 | 手动偶尔运行 | kb_evening 每天自动运行 |

六个条件同时出现才让血案浮出水面：多模态使用上升 × `str(list)` 类型陷阱 × 14 个复制粘贴 cron × 新上线的 harvest → KB 通路 × kb_dedup 自动运行 × kb_dedup 算法本身有 bug。

---

## 六、V37.6 三层修复

### 修复 1：Content blocks 压平（`proxy_filters.flatten_content`）

```python
def flatten_content(content):
    """把 OpenAI message.content 压平成纯字符串。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type", "text") != "text":
                continue  # image_url / tool_use 过滤
            text = block.get("text", "")
            if isinstance(text, str) and text:
                parts.append(text)
        return " ".join(parts)
    return ""  # 未知类型安全兜底
```

`tool_proxy.py:947-958` 替换 `str(m["content"])` → `flatten_content(m["content"])`。

### 修复 2：Sources 写入源头幂等（`kb_append_source.sh`）

```bash
# grep -Fxq 整行精确匹配，处理 marker 中的 regex meta 字符
if grep -Fxq "$H2_MARKER" "$KB_SRC" 2>/dev/null; then
    _log "skip: already exists"
    cat >/dev/null  # drain stdin 避免上游 SIGPIPE
    exit 0
fi

# flock 保护并发 cron 同写
(
    flock -w 30 200
    cat >> "$KB_SRC"
) 200>"${KB_SRC}.lock"
```

14 个 cron job 全部切换到 `} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"` pipeline。特殊情况：
- `ontology_sources` (2x/day) → 用 `"## ${DAY} ${HH:MM}"` marker 让两次 slot 不冲突
- `freight_watcher` (3x/day) → `DAY` 本身已含 HH:MM，天然不冲突

### 修复 3：kb_dedup 算法正确性（`kb_dedup.py`）

```python
def find_duplicate_source_lines(sources_dir):
    """H2 section-scoped dedup."""
    for fname in os.listdir(sources_dir):
        seen = set()
        for line in lines:
            if line.startswith("## "):  # H2 boundary → 重置
                seen = set()
                deduped.append(line)
                continue
            if not stripped or line.startswith("# "):
                deduped.append(line)
                continue
            # ### sub-heading 不触发重置
            if stripped in seen:
                removed += 1
                continue
            seen.add(stripped)
            deduped.append(line)

def find_duplicate_notes(index):
    """Include unindexed notes in dedup scan."""
    entries = list(index.get("entries", []))
    indexed_paths = {e.get("file", "") for e in entries}
    for fname in os.listdir(NOTES_DIR):
        if fname.endswith(".md"):
            rel = os.path.join("notes", fname)
            if rel not in indexed_paths:
                entries.append({"file": rel, "summary": "", "__unindexed__": True})
    # fuzzy hash pass 现在也能捕获孤儿 note
```

---

## 七、治理层防线：3 个新不变式

| ID | 名称 | 深度 | 检查数 |
|----|------|------|--------|
| `INV-KB-001` | content-blocks-flattened-before-kb-write | declaration + runtime | 7 |
| `INV-SRC-001` | sources-writes-are-idempotent-at-source | declaration + runtime | 7 |
| `INV-DEDUP-001` | kb-dedup-is-h2-scoped-and-scans-unindexed-notes | declaration + runtime | 5 |

共 19 个新 check，其中 5 个是 runtime 级：subprocess 驱动真实 kb_append_source.sh、构造 content blocks 调用 flatten_content、构造双 H2 文件验证 kb_dedup 不误判。

---

## 八、元规则进展

**MR-4 (silent failure is a bug) 第三次演出**：
- V37.4.3：PA 告警污染对话上下文（行为层）
- V37.5：kb_review 六 bug 静默降级（效果层）
- **V37.6：KB 数据层双血案（数据层）**

三次血案的共同 pattern：声明层检查全过 → 运行时看起来正常 → 效果层污染/失败 → 用户视角才发现。每次都需要结构层修复 + 治理层锁定 + runtime 级 grep 守卫升级。

**MR-6 (critical invariants need depth)**：三个新不变式全部声明+运行时双层覆盖，不留 grep-only 盲区。

---

## 九、血案元认知

1. **类型系统歧义是暴力转换的温床**：OpenAI `content: str | list` union 是 API 灵活性的代价。任何消费方（日志、审计、KB 写入）都不能假设 content 是 str。
2. **复制粘贴的 cron pattern 是 14 次 bug，不是 1 次**：shared helper 应该早在 V27 任务注册表化时建立，而不是等到第 15 个 job 添加时才抽象。
3. **"事后清理工具"自身必须正确**：kb_dedup.py 作为 V30.x 的补救工具，如果算法本身有 bug，会把补救变成二次破坏。
4. **自动化的 kb_dedup 报告 = 最好的数据质量雷达**：如果没有每日 kb_evening 的自动 dedup 报告，这两个 bug 可能继续潜伏数月。持续运行的自动审计 > 一次性的手动扫查。

---

## 十、后续追踪

- **P1**：kb_evening 本身的深度质量升级（参考 V37.5 kb_review 的 registry-driven + H2 drill-down + LLM + fail-fast 架构）
- **P2**：14 个 cron job 的 shared library 抽象 — 不止 `kb_append_source.sh`，还有 `log()`, `lock`, `seen_file` 等 pattern
- **P2**：conversations JSONL 的 schema 验证 — 写入前检查 content 字段类型
- **MR-8 候选**：`copy-paste-is-a-bug-class` — 3+ 处相同 pattern 必须抽象成 shared helper（V37.6 用了 14 处才意识到这是一个元规则）
