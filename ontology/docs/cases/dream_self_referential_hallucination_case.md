# Dream 自引用幻觉血案（V37.8.6）

> **日期**：2026-04-15
> **血案类**：MR-4 (silent-failure-is-a-bug) 第 7 次演出 + 原则 #23 (链式幻觉) 典型实证
> **严重度**：critical — Dream 向用户推送的"分析结论"基于自己的运行时错误编造外部平台危机
> **核心教训**：当 LLM 看到不理解的字样，它会自动编造一个合理的解释——即使那字样是系统自己的运行时痕迹

---

## TL;DR

2026-04-15 凌晨 03:00 的 Dream cron run 期间，`llm_call` 4 次重试全部返回 400 "Bad JSON"
（adapter.py:386），但 Dream 依然向 WhatsApp + Discord 推送了一份完整的
"Hugging Face 平台危机预警"分析，包含：

- "信号一：Papers with Code 的'完全沉默'是平台危机前兆"
- "行动一：立即启动对 Hugging Face 平台可用性的 72 小时监控机制"
- 证据引用："当前已观测到平台返回 'Bad JSON' 和 '400 错误'，若持续超过 72 小时..."

**信号（PWC 沉默）和行动（监控 Hugging Face）主题断裂**，且"Bad JSON 400"根本不是 Hugging Face 的状态——
**那是 Dream 自己的 `adapter.py` 在拒绝破损 JSON 时吐出的错误页**。LLM 把系统自己的运行时痕迹
当成外部平台事件，编造了一整套合理化叙事。

用户察觉违和后上报，V37.8.6 闭环四层防御。

## 完整因果链架构图

```
2026-04-15 03:00 Agent Dream [Reduce cache-only fast path]
│
├─ Phase 1a/1b Map 循环（14 sources + 23 note batches）
│  │
│  └─ 某批 prompt 含 surrogate UTF-8 字符
│     │  （来源推测：RSS feed、X tweet 或 note 内某条含 U+D800-U+DFFF 孤立代理）
│     │
│     ├─ [kb_dream.sh:187-209] llm_call() 内 heredoc:
│     │    ├─ json.dump(body, f, ensure_ascii=False)
│     │    ├─ Python str 保留 \udXXX 代理码点
│     │    ├─ f.write() 默认 utf-8 编码 → UnicodeEncodeError
│     │    └─ body_file 被截断（只写到错误点前的字节）
│     │
│     ├─ [curl -d @body_file] 上传破损 JSON 到 adapter :5001
│     │
│     ├─ [adapter.py:383] json.loads(raw) → json.JSONDecodeError
│     ├─ [adapter.py:386] self.send_error(400, "Bad JSON")
│     │    → 456 bytes HTML 错误页（Python http.server.BaseHTTPRequestHandler 格式）
│     │    内容：
│     │      <!DOCTYPE HTML PUBLIC...
│     │      Error response
│     │      Error code: 400
│     │      Message: Bad JSON.
│     │
│     ├─ [llm_call:217] jq .choices[0].message.content → empty
│     ├─ [llm_call:250] log "  LLM raw response: 456 bytes, first 500 chars: <HTML>..."
│     │    ⚠️ log() 用 plain `echo` 写 stdout
│     │
│     ├─ [llm_call:267] wait_sec = 3*attempt² → 3s, 12s, 27s 指数退避
│     │    log "  Waiting Xs before retry..."  ← 同样写 stdout
│     │
│     └─ 03:00:53 → 03:01:36: 4 次重试全部确定性失败
│        (surrogate 是确定性污染，重试零收益；V37.8.3 智能退避对此类错误无效)
│
│  ┌─────────────────────────────────────────────────────────┐
│  │ 关键放大器 (V37.8.6 根因)                                  │
│  │                                                           │
│  │  signals=$(llm_call "$prompt" 1200 0.5 90 || true)       │
│  │     │                                                     │
│  │     └─ 命令替换 $(...) 捕获 stdout                         │
│  │        ├─ 正常情况：echo "$result" （LLM 内容）            │
│  │        └─ 失败情况：log() 的所有输出 = 错误日志！          │
│  │                                                           │
│  │  signals = "[2026-04-15 03:00:53] dream: LLM raw response │
│  │             : 456 bytes, first 500 chars: <!DOCTYPE HTML  │
│  │             ...Error code: 400 Bad JSON...               │
│  │             [2026-04-15 03:00:53] dream: Waiting 3s..."   │
│  │                                                           │
│  │  [kb_dream.sh:563] if [ -n "${signals// }" ]; then        │
│  │      echo "$signals" > "$cache_file"                      │
│  │  fi                                                        │
│  │                                                           │
│  │  → cache 文件写入了错误日志（非空但全是错误文本）          │
│  └─────────────────────────────────────────────────────────┘
│
├─ Phase 2 Reduce 跨域关联
│  ├─ 读 $MAP_DIR/2026-04-15_*.txt 填充 MAP_SIGNALS/NOTES_SIGNALS
│  │  └─ 被污染的 cache 文件作为"信号"进入 REDUCE_MATERIAL
│  │
│  ├─ CHUNK1/2/3 LLM calls (其他 prompt，不含 surrogate，LLM 调用成功)
│  │
│  └─ LLM 看到上下文里有：
│        "LLM raw response: 456 bytes... Bad JSON 400 error..."
│     Qwen3 的合理化本能 → 编造外部解释：
│        "平台返回 Bad JSON"
│     + 其他真实信号 "PWC pwc_daily 空更新"
│     → 合成叙事："某个平台出了问题"
│     + 训练数据里最常见的 AI 平台 = Hugging Face
│     → 输出："Hugging Face 平台危机"
│
├─ [用户看到的推送]
│  ├─ Dream 输出结构完整（6 章节齐全，看似正常）
│  ├─ 信号一："Papers with Code 完全沉默" ← 基于真实 PWC 空更新信号（半真）
│  ├─ 行动一："监控 Hugging Face" ← ❌ 与信号主题断裂的幻觉
│  └─ 证据引用："当前已观测到平台返回 'Bad JSON' 和 '400 错误'"
│     ↑
│     这是 adapter.py:386 自己的错误文本被注入 LLM 上下文后编造出的"证据"
│
└─ [血案本质]
   表层：4 次 LLM 调用失败，Dream 仍产出推送（silent failure）
   深层：Dream 读自己的错误日志当外部信号，LLM 编造平台危机（self-referential hallucination）
   架构：log→stdout + $(cmd) 捕获 + cache 非空检查只看字数不看质量
         = 三个独立机制组合出"运行时错误逃逸进业务推送"的通道
```

## 三层根因

| 层级 | 问题 | 证据 |
|------|------|------|
| **触发器** | 某个 source/note 抓取内容含 surrogate UTF-8（U+D800-U+DFFF 孤立代理码点） | job_smoke_test #31/32/33 都显示 "UnicodeEncodeError: 'utf-8' codec can't encode characters in position 30884-30885: surrogates not allowed" × 8 次 |
| **放大器** | 三个独立机制叠加：(a) `json.dump(body, f, ensure_ascii=False)` + 文件默认 utf-8 → 孤立代理炸 UnicodeEncodeError → body_file 截断 → adapter 400；(b) `log()` 写 stdout；(c) `signals=$(llm_call ...)` 命令替换捕获 stdout → 错误日志写入 cache；(d) cache 非空检查只看字数 | kb_dream.sh:109 `log() { echo ...; }` 无 `>&2`；kb_dream.sh:563 `if [ -n "${signals// }" ]; then echo "$signals" > "$cache_file"` |
| **掩护者** | Reduce cache-only fast path + LLM 合理化本能：LLM 看到不理解的字样（"Bad JSON 400"）会自动编造一个合理的外部解释（"Hugging Face 危机"）而非报告"输入异常"。**Qwen3 的安全训练让它倾向于"解释"而不是"拒绝"。**用户不主动读输出就永远发现不了 | 用户 Dream 推送里 Signal(PWC)↔Action(Hugging Face) 主题断裂 |

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 历史累计 | 某 source/note 抓取时保留 surrogate 字符 | 污染种子潜伏 |
| V37.1+ | kb_dream.sh llm_call 用 `log() { echo ... }` 写 stdout | 结构性通道打开 |
| V37.4+ | 引入 Reduce cache-only fast path + 4 次重试 + BEST_RESULT 容错 | 错误被更彻底掩盖 |
| 2026-04-15 03:00 | Dream 03:00 cron 启动 | 正常启动 |
| 03:00:53 | Map 某批 llm_call: json.dump → body_file 截断 → adapter 400 | 第 1 次炸 |
| 03:00:53-03:01:36 | 4 次重试全部同样失败（43s 浪费） | 确定性污染 |
| 03:01:36 后 | signals 捕获 log 行 → `if [-n ...]` 通过 → cache file 污染 | 幻觉种子写入 |
| 03:01+ | Phase 2 Reduce 读 cache 把错误文本当信号喂给 LLM | 幻觉触发 |
| 03:xx | Dream 推送 "Hugging Face 平台危机" 到 WhatsApp+Discord | 用户收到幻觉 |
| 2026-04-15 09:38 | 用户拉 V37.8.5 Mac Mini 同步 + 发现 Dream 输出违和 | 上报触发闭环 |
| 2026-04-15 闭合 | V37.8.6 四层防御 + 19 单测 + INV-DREAM-003 runtime 层 | |

## 为什么以前没发生（五条件组合爆炸）

| 条件 | 以前 | 现在 |
|------|------|------|
| Dream 输入累积含 surrogate 字符 | 偶尔（smoke_test 已报 8 次） | 累积中 |
| `log()` 用 echo 写 stdout | V37.1+ 一直如此 | 一直如此 |
| `signals=$(llm_call ...)` 命令替换捕获 stdout | 一直如此 | 一直如此 |
| cache 非空检查 `[ -n "${signals// }" ]` 只看字数 | 一直如此 | 一直如此 |
| **LLM 基于运行时痕迹编造合理叙事 + 用户察觉违和** | **从未同时触发** | **2026-04-15 首次触发察觉** |

**前 4 个条件 V37.1+ 一直存在，Dream 可能已经静默输出幻觉内容数天或数周而无人察觉。**
只有条件 5（LLM 编造被用户发现）的首次触发，才让这个血案浮出水面。

这是典型的**潜伏态血案**——系统性缺陷长期存在，只是**症状罕见显形**。

## 为什么 LLM 会编造

LLM 不是"读取并回答"，而是"基于上下文生成最可能的下一个 token"。当它看到：

```
[信号摘要]
...
pwc_daily: 今日 0 条更新
LLM raw response: 456 bytes, first 500 chars: <!DOCTYPE HTML...
Error code: 400
Message: Bad JSON
...
```

它不会说"这是我的错误日志，应该忽略"。训练分布里"平台报错"+"用户在讨论分析"的组合让它生成
最高概率的延续——"分析平台危机"。加上 Qwen3 的指令遵循训练让它倾向于"提供有用输出"而非
"拒绝回答"，幻觉就成了必然。

**不要期待 LLM 识别出自己系统的运行时痕迹——它没有这种自我意识。**
防御必须在架构层：**不让错误痕迹进入 LLM 上下文**。

## V37.8.6 四层防御（defense-in-depth）

```
第 1 层：log() >&2  (阻断 stdout 污染通道)
    │
    │    log() 写 stderr 后，signals=$(llm_call ...) 只捕获 echo "$result"
    │    如果 result 为空（全部重试失败），signals 就是空字符串
    │    cache 非空检查自动拒绝空 signals，不写入 cache
    ▼
第 2 层：_sanitize()  (U+D800-U+DFFF → U+FFFD)
    │
    │    json.dump 前先过滤掉孤立代理，从源头避免 UnicodeEncodeError
    │    body_file 能被完整写出，adapter 能正确解析，不再返回 400
    ▼
第 3 层：open(..., errors='replace')  (第二道防线)
    │
    │    万一 sanitize 漏网其他无效字节，文件写入层也能 'replace' 避免炸裂
    ▼
第 4 层：REDUCE/CHUNK1/2/3 system prompt 反污染守卫
    │
    │    即使前三层全失效，LLM 也被明示禁止：
    │    - 引用 HTTP 错误码/Python 异常/错误页 HTML/U+FFFD 作为外部信号
    │    - 推断 Hugging Face/GitHub/npm 等平台状态
    │    - 给出针对"平台错误"的行动建议
    ▼
四层任一单独都能阻断幻觉链，四层叠加几乎不可能再发生
```

## 元规则兑现

### MR-4 silent-failure-is-a-bug（第 7 次演出）

演出列表：
1. V37.3 Governance silent error case（summary 不数 error）
2. V37.4 Dream Map budget overflow
3. V37.4.3 PA 告警污染
4. V37.5 kb_review silent degradation
5. V37.6 KB content+sources dedup
6. V37.7 双跑审计闭环
7. **V37.8.6 Dream 自引用幻觉**（本次）

每一次 MR-4 演出都呈现不同形态，但核心都是：**效果层失效但声明层正常**。
V37.8.6 是迄今最隐蔽的——错误不仅被掩盖，还被 LLM"加工"成了看似合理的业务分析。

### 原则 #23 链式幻觉（典型实证）

CLAUDE.md 原则 #23："LLM 链路中每一跳都会放大幻觉"。V37.8.6 是完美实证：

- **第 0 跳**：adapter.py 发出 400 Bad JSON（非 LLM，系统事件）
- **第 1 跳**：错误日志被 log()+$(...) 污染进 cache（非 LLM，shell 行为）
- **第 2 跳**：Reduce LLM 读 cache 看到错误字样 → 编造"平台危机"（LLM 幻觉产生）
- **第 3 跳**：用户读推送 → 可能采信并采取行动（LLM 幻觉传染人）

**三跳累积的幻觉比单跳严重得多**——因为每跳都给幻觉"增加可信度"：
- 第 1 跳让错误看起来像"数据"
- 第 2 跳把数据包装成"分析结论"
- 第 3 跳用权威推送渠道（WhatsApp/Discord）强化

防御思路：每跳都要有 grounding 检查。V37.8.6 在第 1 跳（log→stderr）和第 2 跳（反污染 prompt）同时下手。

### MR-6 critical-invariants-need-depth（正向兑现）

INV-DREAM-003 首次登记即 `verification_layer: [declaration, runtime]`，
runtime python_assert 真跑 sanitize 五场景，符合 MR-6 强制规定（severity=critical 必须 ≥2 层）。

## 被喂养的本体

1. **本案例文档** `ontology/docs/cases/dream_self_referential_hallucination_case.md`
2. **INV-DREAM-003** 新增 16 checks（含 runtime python_assert 真跑）
3. **test_dream_surrogate_sanitize.py** 19 单测（log→stderr + sanitize + shell 实现 + 反污染 prompt + bash 语法）
4. **CLAUDE.md V37.8.6 版本 + 文件表 + changelog**（待写）
5. **原则 #23 链式幻觉** 获得典型实证案例，未来引用时可指向本文档

## 下一步（迭代本体不闭合）

- [ ] **MR-11 元规则候选**：`shell-function-output-must-go-to-stderr-if-not-returned-value` — 任何 shell 函数用 log/debug/status 输出都必须走 stderr（不只是 Dream）。扫描所有 `log() { ... }` 和 `echo "..."` 在子 shell 函数内的用法，检查是否用 `>&2`
- [ ] **系统化 surrogate 清洗**：Dream 在 LLM 边界做 sanitize，但源头（爬虫/KB 写入）应该先做一次。扫 `kb_write.sh`、`kb_append_source.sh`、各 job 的 RSS/X 解析脚本
- [ ] **LLM 输出 grounding**：Dream 输出中的"平台名"、"公司名"、"产品名"应该有 allow-list。提 Hugging Face 必须先在信号里提到 Hugging Face。目前 LLM 没有这个约束
- [ ] **Dream 失败率可观测**：Map 批次失败 > 阈值时输出加 `[DEGRADED]` 前缀或干脆跳过推送
- [ ] **审计今天的 pwc_daily / 其他 source 文件**：是否真的有 surrogate 字符或哪些字节导致 encoding error

---

**状态**：2026-04-15 闭合 / V37.8.6 发布 / 19 单测 + governance runtime 层全绿 / 994 tests / 37 invariants
