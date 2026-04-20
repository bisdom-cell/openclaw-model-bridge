# 案例：kb_evening 告警"HTTP 502: Bad Gateway"真实原因被三层稀释

> **版本**：V37.8.10
> **日期**：2026-04-15（连续 2 天告警后定性为结构性）
> **血案类归属**：MR-4 silent-failure 第 9 次演出（特殊形态：错误没被吞，只是被稀释到无意义）
> **INV**：INV-OBSERVABILITY-001 `llm-error-chain-is-transparent-across-proxy-and-client`

## TL;DR

2026-04-14/15 连续 2 天 22:00 用户收到 `[SYSTEM_ALERT] kb_evening 失败 原因: HTTP 502: Bad Gateway`。真实原因是 **primary Qwen3 circuit breaker OPEN + fallback gemini HTTP 429 quota exhausted**，但在 adapter→proxy→client 三跳中**每一跳都把 upstream 错误 body 丢弃**，告警经过三次稀释完全变成无意义的状态短语。

这不是代码 bug，是**观察性（observability）架构缺陷**：错误链条被中间层"透明化"反而让它**不透明**。

## 1. 完整因果链架构图（原则 #26 步骤一）

```
═══ 基线期（2026-04-11~13 22:00 均成功）═══

22:00:00  [kb_evening cron] 触发
           │
           ├─ [kb_evening_collect.py] 采集 sources+notes，build prompt ~20KB
           ├─ [Proxy :5002] POST /v1/chat/completions
           ├─ [Adapter :5001] 转发 → Qwen3 primary (hkagentx.hkopenlab.com)
           │     → 200 OK, 16-19s 延迟, prompt=3.8-4.2K tokens
           └─ kb_evening 成功 → 推送 WhatsApp+Discord #daily

═══ 故障期（2026-04-14 起，连续 2 天相同模式）═══

某时刻  [Adapter] primary Qwen3 反复失败
           │  → _CircuitBreaker consecutive_errors 累积到阈值
           └─ circuit_breaker 状态切换 closed → OPEN

22:00:00  [kb_evening cron] 触发 [req 0884a6d7]
           │
           ├─ [Proxy :5002] 收请求 → 转 Adapter :5001
           │
           ├─ [Adapter :5001] 检查 circuit_breaker
           │   └─ 状态 OPEN → 跳过 primary, 走 fallback
           │       log: "CIRCUIT BREAKER OPEN: skipping primary, direct fallback chain (1 providers)"
           │
           ├─ [Adapter] FALLBACK_CHAIN = ["gemini"]（**只有 1 个**）
           │   └─ log: "FALLBACK -> gemini (gemini-2.5-flash)"
           │       │
           │       └─ POST → gemini API (848ms)
           │           → HTTP Error 429 Too Many Requests
           │           ↑ gemini quota 被全天 20+ LLM cron 累积耗尽
           │
           ├─ [Adapter] log: "ALL 1 FALLBACKS FAILED"
           │   └─ _send_json(502, body={"error": "gemini: HTTP 429 ..."})  ← 真实原因在 body
           │
           ├─ [Proxy] _safe_urlopen(adapter_url) 抛 HTTPError 502
           │   └─ except Exception as e:
           │       ├─ error_str = str(e)  ← **第 1 次稀释**
           │       │   "HTTP Error 502: Bad Gateway"（e.__str__ 不含 body）
           │       │   body 从未被 e.read() 提取
           │       ├─ proxy_stats.record_error(502, error_str)
           │       ├─ err = json.dumps({"error": error_str})
           │       └─ wfile.write(err)  ← 只写稀释后的字符串到下游
           │
           ├─ [rc.call_llm] except urllib.error.HTTPError as e:
           │   └─ return (False, "", f"HTTP {e.code}: {e.reason}")  ← **第 2 次稀释**
           │       reason = "HTTP 502: Bad Gateway"（e.reason 只是状态短语）
           │       proxy 的 error body 被 e.read() 丢弃
           │
           └─ [kb_evening.sh] send_alert("LLM 晚间整理失败: HTTP 502: Bad Gateway")
               │
               └─ [notify.sh] [SYSTEM_ALERT] → WhatsApp + Discord  ← **第 3 次稀释**
                   用户看到：
                     "[SYSTEM_ALERT] kb_evening 失败
                      时间: 2026-04-15 22:00:00
                      原因: LLM 晚间整理失败: HTTP 502: Bad Gateway"

   用户视角：无从判断是 primary 挂 / gemini quota / 网络问题 / adapter bug
            只知道"LLM 挂了"

═══ 并行的观察盲区 ═══

[Proxy stats 23:58] total=232 / errors=105 (45%) / p95=128s
     ↑ 所有时段都在 erroring，但只有 kb_evening 有 alert 通道
     └─ 其他 17+ LLM cron 都在用 proxy，status 文件只 3 个 → 全天盲点

[Adapter /health] version=0.36.0 ≠ 仓库 VERSION 0.37.8.9
     ↑ adapter 进程自 V36 起未重启；中间 3 个 adapter.py commit 未生效
     └─ 虽然 fallback_chain hot_reload 工作，但 build_fallback_chain()
        最终仍只返回 ["gemini"]，因为 providers.d/ 下其他 provider 缺 API key
```

## 2. 三层根因（步骤二）

| 层级 | 发现 |
|------|------|
| **触发器** | 2026-04-14 某时刻起 primary Qwen3 (hkagentx.hkopenlab.com) 开始不稳定，adapter._CircuitBreaker `consecutive_errors` 累积到阈值 → breaker OPEN。22:00 kb_evening 请求到达时已 skip primary 直接走 fallback。（2026-04-15 23:53 primary `last_success_time` 表明 primary 时好时坏，不是完全宕机。） |
| **放大器** | `FALLBACK_CHAIN = ["gemini"]` **只配了 1 个 fallback**。gemini 2.5-flash 免费层 daily/rate quota 被白天 ~20 个 LLM cron（kb_inject 07:00 / finance_news 07:30 / arxiv 08:00 / daily_ops 08:15 / ai_leaders_x 09:00+21:00 / hf_papers 10:00 / ontology_sources 10:00+20:00 / s2 11:00 / dblp 12:00 / pwc 13:00 / github_trending 14:00 / rss_blogs 18:00 / freight 08/14/20…）持续磨耗。到 22:00 gemini quota 所剩不足 → **链式耗尽**。单一 fallback 在白天就注定撑不到晚上。 |
| **掩护者** | **三重错误链稀释掩盖真实原因**：① adapter → proxy：proxy 的 `except Exception as e: error_str = str(e)` 丢弃 `e.read()` body ② proxy → client：`rc.call_llm` HTTPError 分支 `f"HTTP {e.code}: {e.reason}"` 再次丢弃 body ③ 告警文本稀释：最终 "HTTP 502: Bad Gateway" 完全不告诉用户 "gemini 429 quota 耗尽"。**同时**：其他 17+ LLM cron 没有 `last_run_*.json` 写入，全天 45% 错误率无观察入口，kb_evening 是唯一可观测的失败端。如果 kb_evening 也没 alert 机制，**45% 错误率可能已经持续几天无人察觉**。 |

## 3. 时间线（步骤三）

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-11 22:00 | primary 200 OK, 16s | 基线 |
| 2026-04-12 22:00 | primary 200 OK, 19s, prompt=4202 tokens | 正常 |
| 2026-04-13 22:00 | primary 200 OK, 16s, prompt=3835 tokens | 正常 |
| **2026-04-14 某时刻** | primary 连续错 → breaker OPEN | **状态翻转点**（上游不稳定） |
| **2026-04-14 22:00** | breaker OPEN + gemini 429 → 502 | **首次 kb_evening alert** |
| 2026-04-15 05:48 | 收工 unfinished 登记"22:00 KB晚间应自愈（昨天 502）" | **误判为 transient** |
| **2026-04-15 22:00** | 完全相同模式重演 → 502 | **用户上报，确认结构性** |
| 2026-04-15 23:53 | proxy last_success_time | primary 偶尔恢复（证实时好时坏） |
| 2026-04-15 23:58 | proxy stats: 232 req / 105 err (45%) | **观察盲区的规模首次浮现** |
| 2026-04-16 00:05 | Fix D 执行 bash ~/restart.sh | adapter 0.36.0 → 0.37.8.9 |
| 2026-04-16 00:20 | Fix A 代码完成 + 21 单测 + INV-OBSERVABILITY-001 12/12 绿 | 结构修复落地 |

## 4. 条件组合分析（步骤四）

六个条件同时出现才爆炸，只有一个变量在 2026-04-14 才翻转：

| 条件 | 2026-04-13 前 | 2026-04-14 后 |
|------|---------------|----------------|
| primary Qwen3 稳定性 | ✅ 稳定（16-19s 成功） | ❌ 不稳定，breaker 频繁 OPEN |
| fallback_chain 长度 | 1（仅 gemini） | 1（仅 gemini）— **未变** |
| gemini 白天调用密度 | ~20 cron/天 | ~20 cron/天 — **未变** |
| call_llm HTTPError body 暴露 | ❌ 丢 | ❌ 丢 — **未变** |
| proxy HTTPError body 暴露 | ❌ 丢 | ❌ 丢 — **未变** |
| last_run 观察覆盖 | 3 job | 3 job — **未变** |
| adapter 进程版本 | 0.36.0 | 0.36.0 — **未变** |

**元洞察**：六个条件里只有"primary 稳定性"翻转了。**一旦这一个条件翻转，其余五个"一直是这样"的条件同时暴露为结构缺陷**。这印证了原则 #21（对抗审计）——"什么坏了我们发现不了"的正确答案是**primary 稳定性滑坡** + **fallback 单薄** + **observability 不全**三者叠加。系统在 2026-04-13 之前一直带病运行但"看起来正常"。

## 5. 喂养本体工程（步骤五）

### 5.1 新不变式

- **INV-OBSERVABILITY-001** `llm-error-chain-is-transparent-across-proxy-and-client`
  - meta_rule=MR-4（silent-failure is a bug — 特殊形态"错误被稀释而非吞"）
  - severity=high, verification_layer=[declaration, runtime]
  - 12 checks 覆盖：proxy 侧 helper 定义 + MAX 常量 + read body + fail-open + tool_proxy import guard（MR-8 禁止重定义）+ client 侧 helper 镜像 + 两层 runtime python_assert 真跑血案场景

### 5.2 架构契约（MR-8 正向兑现）

`compose_backend_error_str` 放在 `proxy_filters.py`（纯函数无网络依赖），而非 `tool_proxy.py`（import 会启动 HTTP server）。原因：
- `test_tool_proxy.py` 从 `proxy_filters` import 测试（tool_proxy 不可测）
- helper 留在 `tool_proxy.py` 就无法单测 → 无法有 runtime check → 违反 MR-6 多层验证深度

### 5.3 MR-4 silent-failure 演出史更新

| 次数 | 版本 | 场景 |
|------|------|------|
| 1 | V37.3 | governance summary 吞 error 状态 |
| 2 | V37.4 | Dream Map budget 溢出 |
| 3 | V37.4.3 | PA 告警污染（结构+行为双防线） |
| 4 | V37.5 | kb_review 空 prompt 静默降级 |
| 5 | V37.6 | KB content dedup + sources 重复 |
| 6 | V37.7 | audit_log 双跑 prev_hash 级联 |
| 7 | V37.8.6 | Dream 自引用幻觉（log→stdout→cache） |
| 8 | V37.8.7 | ontology_sources 位置解析级联 |
| **9** | **V37.8.10** | **LLM 错误链三层稀释（本案例：错误没被吞，只是被稀释到无意义）** |

**本次新形态**：前 8 次 silent-failure 都是"错误发生了但没被发现"。第 9 次是**错误被发现了但原因不可见**——这是更隐蔽的变种，因为用户**看到了告警**，误以为观察机制工作正常，实际信息密度已经是零。

### 5.4 登记未解决项（不和本血案捆绑）

按原则 #28 最小修复原则，以下事项登记为 V37.8.11+ 候选，不在本次修复范围：

1. **B. 扩展 fallback_chain 到 ≥2 provider**（需用户决策：API key 可用性）
   - Mac Mini 重启后 /health 显示 `fallback_chain: ["gemini"]`——V3 capability routing 真跑但无二线 provider 被发现
   - 需要配置 `providers.d/` YAML + 对应 API key 环境变量
2. **C. 其他 17+ LLM cron 加 `last_run_*.json`**（统一 observability 入口）
3. **proxy_stats auto_recovery_rate_pct bug**（显示 194.1%，数学错误，次要问题）
4. **MR-4 新候选 meta_rule MR-13**：`error-chain-must-preserve-upstream-cause-across-layers`（把本血案的教训升级为元规则）
5. **gemini quota 消耗审计**：日内 ~20 cron 中哪些可以降频/合并/改用非-LLM 路径

## 6. 修复验证

### 6.1 声明层（12/12 check pass）

```
✅ 🟠 [INV-OBSERVABILITY-001] llm-error-chain-is-transparent-across-proxy-and-client
  governance_checker.py: 39/39 invariants, 180/180 checks (11 skipped runtime-only), 12/12 meta rules
```

### 6.2 运行时层（21/21 单测 pass）

- `test_kb_review.TestComposeHttpReason` 10 个
- `test_tool_proxy.TestComposeBackendErrorStr` 11 个（含 `test_tool_proxy_imports_helper_from_filters` source-level guard）
- `test_call_llm_integration_http_error_with_body` 端到端：HTTPError + body → reason 正确拼接 upstream

### 6.3 效果层（待 Mac Mini 2026-04-16 22:00 观察）

如果明日 22:00 kb_evening 再次失败，告警应该包含形如：
```
[SYSTEM_ALERT] kb_evening 失败
原因: LLM 晚间整理失败: HTTP 502: Bad Gateway | upstream: HTTP Error 502: Bad Gateway | upstream: ALL 1 FALLBACKS FAILED: gemini (gemini-2.5-flash) HTTP 429: Too Many Requests
```

如果告警显示的仍是裸 "HTTP 502: Bad Gateway"，说明修复未生效，需二次排查。

## 7. 结构教训

1. **错误链透明度是架构属性，不是单一函数属性**。任何中间层（proxy/gateway/middleware）在 catch-then-rethrow 时必须保留 upstream body，否则下游诊断能力被它**主动破坏**。
2. **`str(Exception)` 是陷阱**。对 HTTPError 调 `str()` 只给状态短语；真实信息在 `.read()` body 里。这是 Python urllib 的长期 API 设计问题，需要 defensive programming。
3. **Fail-open observability**。观察性增强代码**绝不能**成为新的故障源。`e.read()` 抛异常时必须 fallback 到原行为（try/except 包裹）。
4. **单一 fallback = 没有 fallback**。`FALLBACK_CHAIN = ["gemini"]` 在 primary 不稳时只能提供一次救援机会；遇到 quota exhausted 就整个链路死亡。至少 2 个 provider 才能算有 fallback。
5. **观察盲区的规模通常大于你以为的**。本案 proxy_stats 显示 45% 全天错误率，而 kb_evening 只是其中一个可见出口。**如果你只有一个观察点，你看到的不是系统真实状态，而是那一个点周围的状态**。

---

## 8. 后续章节 V37.8.11：告警噪声也是 observability 问题（MR-4 第 10 次演出）

V37.8.10 闭环后同晚（2026-04-15 夜），用户反馈：

> "现在几乎每个小时都会收到这个告警 `[SYSTEM_ALERT] 漂移检测: 修复 1 个部署文件不一致，已自动覆盖`"

三问验证：
- **问题存在吗？** 是。22:00 + 23:00 两个样本 + 用户确认"存在很久了，只是没注意"。
- **哪个改动引入？** **不是** V37.8.10 引入。追溯到 V37.8.1：preflight_check.sh 加了 status.json 合法分叉豁免，但 auto_deploy.sh 的等价 drift 循环**没有同步加**。遗漏至今。
- **最小修复？** auto_deploy drift 循环镜像同款豁免。

### 8.1 新血案因果链（与 V37.8.10 对比）

```
每小时整点 (minute<2)  [auto_deploy cron]
     │
     ├─ for each file in FILE_MAP:
     │    md5(repo/SRC) vs md5(runtime/DST)
     │
     ├─ SRC = "status.json"
     │    ├─ runtime 由 kb_status_refresh cron 每小时重写 health/quality → 永远和 repo 不等
     │    ├─ preflight_check.sh:207 已豁免 ✅
     │    └─ auto_deploy.sh:319 **未豁免** ❌
     │
     ├─ md5 不一致 → cp repo/status.json -> runtime/.kb/status.json
     │    ↑ **清空 runtime 数据**（覆盖 health/last_refresh/stale_jobs 等）
     │
     ├─ DRIFT=1 → quiet_alert("⚠️ 漂移检测: 修复 1 个...")
     │    ↓ [SYSTEM_ALERT] 前缀（V37.4.3）
     │    ↓ WhatsApp + Discord 推送
     │
     └─ 下个整点 kb_status_refresh 再次写入 → 再次 md5 不等 → 恶性循环
```

### 8.2 MR-4 silent-failure 演出史新形态

| 次数 | 场景 | 形态 |
|------|------|------|
| 1-9 | 历次血案 | 错误被吞 / 错误被稀释 |
| **10** | **本次 V37.8.11** | **expected-behavior 被系统错分类为 error，产出噪声告警** |

关键洞察：**"告警"不等于"有问题"**。如果一个系统把正常现象标为异常，告警就成为噪声；噪声多到一定程度，用户对真正的告警麻木（boy-who-cried-wolf）。这**不是 observability 不足，是 observability 分类错误**——同样会腐蚀系统的自我感知能力。

### 8.3 修复 + INV-DEPLOY-003

`auto_deploy.sh:319` 加入：

```bash
# V37.8.11: status.json 合法分叉豁免（mirror V37.8.1 preflight 豁免）
if [[ "$SRC" == "status.json" ]]; then
    continue
fi
```

**保留** new-commit 同步路径（CHANGED_FILES 循环），确保 Claude Code 的 intent 变更（priorities / unfinished / recent_changes）仍能单向下传到 runtime。契约：

> **one-way intent flow (repo → runtime via new-commit) + exempt from two-way drift detection**

INV-DEPLOY-003 5 checks：
1. preflight 豁免守卫 `if SRC == status.json continue`
2. preflight 豁免 pass 消息
3. auto_deploy 豁免关键字 `V37.8.11.*status.json`
4. auto_deploy `if SRC == status.json continue` 语法结构
5. FILE_MAP 仍含 status.json（intent 通道不能丢）

### 8.4 元教训

**系统性扫描而非单点修复**。V37.8.1 只给 preflight 加豁免，没扫描 FILE_MAP 的其他消费者（auto_deploy、job_smoke_test、可能还有其他）。未来任何给 drift/deploy 检测加豁免时，应该：

1. 列出 FILE_MAP 的**所有**消费者
2. 判断每个消费者是否需要同款豁免
3. 批量应用或显式记录为什么某个消费者不需要

登记为**原则扩展候选**（下次收工时考虑加入原则列表）：

> **原则 #31 候选**：任何 FILE_MAP 或 REQUIRED_VARS 类检查规则的豁免/配置变更，应在**所有消费者**（preflight / auto_deploy / job_smoke_test / 其他扫描脚本）同步更新。修改单点时应显式列出未修改的消费者及其理由。

### 8.5 原则 #15 反思

CLAUDE.md 原则 #15 说：

> **定期像用户一样使用系统** — 不是跑单测，而是在 WhatsApp 上实际问 PA 问题。

本案正是这条原则的反面教材：**开发环境所有单测/治理/preflight/full_regression 全绿，但生产环境用户每天被 24 条噪声告警折磨**。我们的自动化观察不包括"噪声体感"这个维度——只有人真的每天看 WhatsApp 才能感知。

登记为**每周一次 WhatsApp 观察 session（30 min）**的运维动作：专门看告警噪声 / 推送延迟 / 信息密度 / 用户感知。

### 8.6 Mac Mini E2E 验证（待明日）

下一个整点 2026-04-16 00:00（约 30 min 后，如果 auto_deploy 能在 V37.8.11 合并后的 2 分钟内 git pull 到更新）：

- **期望**：auto_deploy 运行但不输出"⚠️ 漂移修复(不一致): status.json..."日志行，不推送 [SYSTEM_ALERT] 告警
- **验证命令**：`grep "$(date +'%Y-%m-%d')" ~/.openclaw/logs/auto_deploy.log | grep -c "漂移修复.*status.json"` 应为 **0**
- **若仍有告警**：表明 git pull 未及时，或我的豁免语法有问题——二次排查

