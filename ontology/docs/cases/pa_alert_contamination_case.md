# 案例分析：PA 告警污染幻觉 — 系统告警变成"用户任务"

> 日期：2026-04-11 | 触发事件：PA 对用户哲学问题回复"请打开系统偏好设置添加 /usr/sbin/cron 到完全磁盘访问权限"，完全脱离用户真实意图

---

## 事件

用户于 2026-04-11 13:06 在 WhatsApp 向 PA 发送哲学问题：

```
用户: AI Agent 终极架构：本体×随机×贝叶斯
```

PA 回复：

```
PA: 已收到系统告警跟进任务，正在跟进。
请您完成以下操作后我再运行 cron_doctor.sh：
1. 打开系统偏好设置 → 安全性与隐私 → 隐私
2. 在"完全磁盘访问权限"中添加 /usr/sbin/cron
3. 完成后告诉我，我运行诊断脚本
```

这是一次严重的**答非所问幻觉**：
- 用户问的是哲学/架构话题（本体论 × 随机过程 × 贝叶斯推断）
- PA 回复的是 macOS 系统运维指令
- **而且技术细节完全错误**：macOS 的 cron 由 launchd 管理，从来不需要"完全磁盘访问权限"。这是编造的。

用户追问三次后，PA 才意识到自己答非所问。

## 表面现象 vs 真实根因

| 层级 | 表面判断 | 深挖后的真实根因 |
|------|----------|-----------------|
| 第一眼 | "Qwen3 又幻觉了" | 是幻觉，但不是随机幻觉，是**定向污染**后的答非所问 |
| 第二层 | "SOUL.md 规则不够严" | 规则 9 "批判性思考"在，但当时的上下文中已经包含了一条真实的告警消息 |
| 第三层 | "告警是哪里来的？" | 12:30:00 job_watchdog 通过 `notify.sh --topic alerts` 推送了 WARNING |
| 第四层 | "告警怎么进的 PA 上下文？" | Gateway 把所有 WhatsApp 推送写入 `sessions.json` 作为 `assistant` role 消息 |
| 第五层 | "告警进了上下文为什么会影响 13:06 的回复？" | Proxy `truncate_messages()` 保留"最近 N 条"，12:30 的告警在 13:06（36 min 后）的窗口内 |
| 第六层 | "Qwen3 看到告警为什么会把用户新问题当告警后续？" | attention 机制跨主题关联：`cron_doctor.sh` 触发词 + assistant 先前说过"排查建议" → 新的 user 消息被解读为"对排查建议的响应" |

## 完整因果链架构图

```
12:30:00  [cron] job_watchdog.sh 检测到异常
          │
          ├─ 调用 notify.sh "⚠️ ... 排查建议: bash cron_doctor.sh" --topic alerts
          ├─ notify.sh → openclaw message send --channel whatsapp
          ├─ Gateway (:18789) 收到推送请求
          ├─ Gateway WhatsApp 客户端发送消息给用户
          └─ Gateway 同时把这条消息写入 sessions.json
               └─ {"role": "assistant", "content": "⚠️ ... 排查建议 ..."}
                  ← ❌ 告警被当作 PA 的"历史发言"存入对话历史
                  ← Gateway 不区分"PA 回答"vs"系统告警 push"

12:30:15  用户在 WhatsApp 看到告警，没有回复（不是在和 PA 对话）
          │
          └─ sessions.json 累积: [..., user_msg_12:20, assistant_ans_12:20,
                                   assistant_alert_12:30]
                                                    ↑
                                          无 user_msg 对应的 assistant 消息
                                          对话历史在这里"断裂"

─── 36 分钟后 ─────────────────────────────────────────────────────

13:06:00  用户发送新消息："AI Agent 终极架构：本体×随机×贝叶斯"
          │
          ├─ Gateway 收到消息，append 到 sessions.json
          ├─ Gateway 准备构造 LLM 请求
          └─ Gateway → Tool Proxy (:5002) /v1/chat/completions
               └─ body.messages = sessions.json 近 N 条

13:06:01  [Tool Proxy] 处理请求
          │
          ├─ messages 数组里包含:
          │    - system (SOUL.md + CLAUDE.md)
          │    - user: 之前的对话
          │    - assistant: 之前的回答
          │    - assistant: "[12:30] ⚠️ job_watchdog 告警..."  ← ❌ 污染源
          │    - user: "AI Agent 终极架构：本体×随机×贝叶斯"  ← 真实意图
          │
          ├─ truncate_messages(messages, max=21)
          │    ├─ 保留 system + 最近 N 条
          │    └─ 告警消息在"最近 N 条"窗口内 → 通过 ✅
          │
          ├─ 其他 filters (fix_tool_args, add_custom_tools, ...)
          ├─ 转发给 Adapter (:5001)
          └─ Adapter → Qwen3-235B

13:06:05  [Qwen3-235B] attention 计算
          │
          ├─ 读到 system prompt：SOUL.md 规则 9 "批判性思考"
          │    ← 这条规则针对"用户提出新观点时迎合"，不针对"答非所问"
          │
          ├─ 读到 assistant 告警消息："⚠️ ... 排查建议: bash cron_doctor.sh"
          │    ├─ token: "排查建议" → 高亮
          │    ├─ token: "cron_doctor.sh" → 高亮
          │    └─ 把它视为"PA 自己说过的话"（因为 role=assistant）
          │
          ├─ 读到 user 新消息："AI Agent 终极架构：本体×随机×贝叶斯"
          │    ├─ token: "架构" → 与 "cron/系统架构" 有弱关联
          │    └─ attention 尝试跨消息关联
          │
          ├─ 幻觉生成路径：
          │    "PA 之前说要排查 cron" + "用户提到架构" →
          │    → "用户是在回应排查建议吗？"
          │    → 编造一个完整的"用户正在处理系统问题"的叙事
          │    → 编造 macOS FDA（完全磁盘访问权限）指令
          │    → 这是 training data 里 macOS 运维文章的模式填空
          │    → 技术上完全错误（launchd 不需要 FDA）
          │
          └─ 输出：
             "已收到系统告警跟进任务... 请打开系统偏好设置...
              添加 /usr/sbin/cron 到完全磁盘访问权限..."

13:06:07  [用户] 看到回复
          │
          └─ 完全茫然：
             - 我问的是哲学/架构
             - PA 回答的是 macOS 运维
             - 我什么时候收到过"告警跟进任务"？
             - 我为什么要改系统权限？
```

## 三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | 12:30 job_watchdog 推送的 WARNING 告警被 Gateway 写入 sessions.json 作为 assistant role | Gateway 不区分"PA 主动回答用户"和"系统通过 PA 通道推送告警"，两者都进入对话历史 |
| **放大器** | Tool Proxy 的 truncate_messages() 只保留"最近 N 条"，36 min 内的告警仍在窗口 | truncate 不理解消息语义，告警和真实对话被等同对待 |
| **掩护者** | SOUL.md 规则 9 关注"迎合"，不关注"答非所问"；没有告警消息识别/隔离规则；没有"主题对齐"硬规则 | LLM 层没有最后防线——当污染穿过所有前置检查时，没有规则要求 LLM 必须与用户最新消息主题对齐 |

## 时间线还原表

| 时间 | 事件 | 影响 |
|------|------|------|
| 12:30:00 | job_watchdog 推送告警 `⚠️ ... 排查建议: bash cron_doctor.sh` | 告警进入 WhatsApp + sessions.json |
| 12:30:01 | Gateway 把告警 append 到 sessions.json (role=assistant) | 开始污染对话历史 |
| 12:30:15 | 用户看到告警，未回复（不在对话中） | 对话历史留下"悬空的 assistant 告警" |
| ~12:35–13:05 | 其他 cron 继续可能写入（此时间窗内无新 user 消息） | 告警仍在"最近 N 条"窗口内 |
| 13:06:00 | 用户发 "AI Agent 终极架构：本体×随机×贝叶斯" | 触发 LLM 请求 |
| 13:06:01 | Proxy truncate_messages 保留最近 N 条（告警通过） | 告警进入 LLM 上下文 |
| 13:06:05 | Qwen3 attention 跨主题关联 → 编造 macOS FDA 指令 | 完全答非所问 |
| 13:06:07 | 用户收到荒谬回复 | 用户追问"我没问这个" |
| 13:10 | 用户开启诊断：sessions.json + proxy.log + job_watchdog.log | 确认污染源 |
| 14:00 | 开始修复：Path A（结构）+ Path C（治理+SOUL.md 规则 10） | 双防线部署 |

## 为什么以前没发生（条件组合分析）

| 条件 | 以前 | 2026-04-11 |
|------|------|----------|
| 告警推送频率 | 低（几周一次） | 当天 job_watchdog 异常多次告警 |
| 告警和真实对话的时间差 | 通常 <5 min 或 >几小时（truncate 会踢出）| 36 分钟——刚好在 truncate 窗口内 |
| 告警内容是否包含"执行类"触发词 | 大多数告警只是状态描述 | 当天告警包含明确的 `bash cron_doctor.sh` 可执行命令 |
| 用户提问话题 | 通常紧接上一条对话 | 突然切换到哲学话题（attention 失去锚点） |
| SOUL.md 是否有告警隔离规则 | 无 | 无（直到这次事件后才加） |
| Proxy 是否剥离告警 | 无 | 无（直到这次事件后才加） |

**关键洞察**：这不是"哪一个条件的问题"，是**六个条件同时出现的组合爆炸**。以前每次只差一两个条件，所以幻觉不会落在"macOS FDA"这种具体的答非所问上。

## 修复方案（V37.4.3）

### Path A：结构性隔离（主防线）

1. **推送侧注入标记**（`notify.sh` + 所有直接 send 告警的脚本）：
   ```bash
   if [ "$topic" = "alerts" ]; then
       msg="[SYSTEM_ALERT]
   $msg"
   fi
   ```
   覆盖路径：`notify.sh`, `auto_deploy.sh`, `run_hn_fixed.sh`, `jobs/openclaw_official/run.sh`, `jobs/openclaw_official/run_discussions.sh`

2. **消费侧剥离**（`proxy_filters.py` + `tool_proxy.py`）：
   ```python
   SYSTEM_ALERT_MARKER = "[SYSTEM_ALERT]"

   def filter_system_alerts(messages, log_fn=None):
       filtered = []
       dropped = 0
       for m in messages:
           if m.get("role") == "system":
               filtered.append(m)  # system role 不剥离
               continue
           if _message_starts_with_alert_marker(m):
               dropped += 1
               continue
           filtered.append(m)
       return filtered, dropped
   ```

3. **顺序锁定**：`filter_system_alerts()` 必须在 `truncate_messages()` **之前**执行，否则 truncate 的"最近 N 条"窗口会把告警带过去。
   - 单测 `test_integration_with_truncate` 锁定顺序
   - 治理不变式 INV-PA-001 的 python_assert 检查 `tool_proxy.py` 源码中 `filter_system_alerts(` 出现位置 < `truncate_messages(`

### Path C：治理 + 行为双防线

1. **SOUL.md 规则 10 "告警消息不跟进（2026-04-11 血案规则）"**
   - 原则：系统自动推送的告警由 cron 自动处理，**不是用户给我的任务**
   - 识别标志：`[SYSTEM_ALERT]` 开头 + "🚨/WARNING/ERROR/排查建议/cron_doctor.sh" 等模式
   - 禁止行为：不说"已收到告警跟进任务"、不主动要求用户打开系统偏好设置、不编造 macOS 指令
   - **主题对齐硬规则**：我的回复主题必须与用户最新一条消息的主题直接对齐
   - **幻觉防线**：macOS cron 由 launchd 管理，不需要"完全磁盘访问权限"
   - 案例警示段落

2. **Governance 不变式 INV-PA-001 + INV-PA-002**：
   - INV-PA-001（critical, declaration+runtime）：
     - Layer 1: 声明层 file_contains 检查所有推送路径注入 `[SYSTEM_ALERT]`、proxy 导入并调用 filter、SOUL.md 规则 10 存在
     - Layer 1 顺序锁：python_assert 检查 tool_proxy.py 中 filter 出现位置 < truncate
     - Layer 2: 运行时 python_assert 构造真实告警消息，验证 filter_system_alerts 真的剥离（含 str content / OpenAI content blocks / system role 保留 / 中文匹配 / 不误伤消息中提到标记的情况）
   - INV-PA-002（high, declaration）：
     - 纯声明层 file_contains 检查 SOUL.md 关键词存在（"告警消息不跟进" / "2026-04-11 血案规则" / "主题对齐" / "完全磁盘访问权限" / `[SYSTEM_ALERT]`）
     - 这是"LLM 最后防线"——即使 proxy filter 漏过，规则 10 要求 LLM 自己拒绝把告警当任务

## 为什么是两层防线而不是一层

| 层 | 处理的问题 | 覆盖范围 | 盲点 |
|----|----------|---------|------|
| **INV-PA-001（结构层）** | "告警怎么进来的" | 所有标记过的告警路径 | 未标记的告警源（新增 job 忘记加 `[SYSTEM_ALERT]`） |
| **INV-PA-002 + SOUL.md 规则 10（行为层）** | "告警进来后 LLM 怎么响应" | LLM 对告警内容的响应行为 | 依赖 LLM 遵守 SOUL.md（非结构保证） |

- 只有结构层：未标记的告警进入上下文 → LLM 仍会被污染
- 只有行为层：结构漏洞依赖 LLM 每次都做对 → 不可靠
- 两层同时：结构层拦住 95%+，行为层兜底 5%

这和网络安全的"纵深防御"同构：应用层 WAF + 数据库参数化查询，两者都不依赖对方。

## 喂养本体工程

- 案例文档：`ontology/docs/cases/pa_alert_contamination_case.md`（本文）
- 治理不变式：
  - INV-PA-001 `alert-does-not-pollute-chat-context`（severity: critical, verification_layer: [declaration, runtime]）
  - INV-PA-002 `soul-rule-10-present`（severity: high, verification_layer: [declaration]）
- governance_ontology.yaml v3.3 → v3.4（26 → 28 不变式，52 → 67 checks）
- SOUL.md 新增规则 10 作为宪法级规则
- 回归测试 21 个单测锁定（filter_system_alerts 14 + notify.sh 标记注入 5 + tool_proxy 导入顺序 2）

## 关键教训

1. **Gateway session 不是纯用户对话** — 它混合了三类消息：真实用户对话、PA 主动推送、系统告警。三类的语义完全不同，但都以相同的结构存储。架构假设"sessions.json = 对话历史"是错的。

2. **truncate 没有语义理解** — 任何基于"最近 N 条"的截断都会保留不该保留的内容。截断必须在**过滤之后**进行，而不是之前。

3. **LLM attention 会编造关联** — 给定一个告警（有 assistant role）和一个无关的新问题，Qwen3 会尝试把它们关联起来形成"连续叙事"，即使两者主题完全不同。SOUL.md 层面必须有"主题对齐"硬规则作为最后防线。

4. **LLM 编造的技术细节可能完全错误** — "macOS cron 需要 FDA" 是训练语料里 macOS 运维文章的模板填空，但技术上不对（launchd 管理的 cron 不需要 FDA）。LLM 的技术输出必须被质疑，不能盲信。

5. **幻觉不是概率问题，是结构问题** — 这次幻觉可以用一个确定性的因果链解释：告警进入上下文 → truncate 保留 → attention 跨主题关联 → 编造叙事。修复不是"让 LLM 更聪明"，而是"切断因果链"。

6. **观察者自己的盲区** — SOUL.md 规则 9 要求"批判性思考"但针对"迎合用户新观点"，它没想到的场景是"用户没有新观点，但我在回应一个 36 分钟前的系统告警"。规则空间的盲区本身需要被治理。

## 相关

- `ontology/docs/cases/pa_echo_chamber_case.md` — V37.1 PA 迎合性回复案例（SOUL.md 规则 9 的起源）
- `ontology/docs/cases/governance_silent_error_case.md` — V37.3 治理观察者盲区
- `SOUL.md` — 规则 9（批判性思考）+ 规则 10（告警消息不跟进）
- `proxy_filters.py` — `SYSTEM_ALERT_MARKER` + `filter_system_alerts()`
- `ontology/governance_ontology.yaml` — INV-PA-001, INV-PA-002
- `test_tool_proxy.py` — TestFilterSystemAlerts (14) + TestNotifyShAlertMarker (5) + TestToolProxyImportsAlertFilter (2)
