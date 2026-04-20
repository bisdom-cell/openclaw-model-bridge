# Case Study: kb_review 6-Issue Silent Degradation

> 2026-04-11 V37.5 血案案例 — 效果层静默降级 bug class 的典型样本。
> 与 V37.4.3 PA 告警污染同属 MR-4 (silent failure is a bug) 血案类：
> **声明层检查全过，status.json 字段全绿，但端到端效果完全失效**。

## 一句话摘要

`kb_review.sh` 一次运行触发 6 个相互掩护的 bug，导致用户连续 N 周收到
"知识回顾"推送，但回顾内容全是 digest 容器标题（例如 `## 今日arXiv精选(2026-04-04)`）
而非论文实体，系统 `status.json` 永远写 `llm: true`——没有任何监控会告警。

## 血案类归属

| 维度 | V37.4.3 PA 告警污染 | V37.5 kb_review silent degradation |
|------|------------------|----------------------------------|
| 血案类 | 数据结构性污染 | 失败路径结构性伪装 |
| 声明层检查 | ✅ 全过 | ✅ 全过 |
| 运行时层 | ❌ 告警进入 LLM 上下文 | ❌ LLM prompt 为空 + 机械 fallback |
| 效果层 | ❌ PA 编造 FDA 指令 | ❌ 回顾内容全是 digest 标题 |
| 监控信号 | 0 告警 (看起来像正常回复) | 0 告警 (llm_status 永远写 true) |
| 元规则 | MR-4 silent failure is a bug | MR-4 silent failure is a bug |
| 修复范式 | 结构隔离 + 行为防线 | fail-fast + registry-driven + H2 drill-down |

## 1. 完整因果链架构图（四维度）

```
时间线    层级                  逻辑/代码                             架构/数据流
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
cron 09:00  [kb_review.sh V29]   触发每日回顾
              │
              ├─ DAYS=7 默认参数
              ├─ 硬编码源列表: [arxiv, hf_papers, semantic_scholar,
              │                 dblp, acl_anthology, freight, hn]   ←  7 源 (遗漏 2)
              ├─ 缺失: ai_leaders_x (X 洞察) + ontology_sources
              │
              ├─ # 采集阶段
              ├─ for src in $SOURCES; do
              │     cat ~/.kb/sources/$src.md \
              │     | python3 -c "..."  # 行级日期匹配
              │   done
              │
              │   ⚠️ 行级匹配 bug:
              │   for line in lines:
              │     if "2026-04-10" in line:  # 只保留含日期的单行
              │       out.append(line)
              │
              │   实际 markdown 结构:
              │   ## 今日arXiv精选(2026-04-10)    ← 日期在 H2 header (保留)
              │   - Paper A: ...                  ← body 无日期 (丢弃！)
              │   - Paper B: ...                  ← body 无日期 (丢弃！)
              │
              │   → 结果: 只留下容器标题, 论文实体全被过滤掉
              │
              ├─ NOTES_CONTENT=""
              ├─ SOURCE_CONTENT="$DATE_MATCHED_LINES"
              ├─ # prompt 构造在 shell 里
              │
              ├─ # LLM 调用 (subprocess)
              ├─ python3 <<EOF
              │   prompt = os.environ.get("NOTES_CONTENT", "")
              │   # ⚠️ BUG 1: export 在这里之后才出现
              │   r = requests.post(PROXY, json=...)
              │   EOF
              │
              ├─ export NOTES_CONTENT="$collected"   ← 太晚!
              ├─ export SOURCE_CONTENT="$matched"
              │
09:02       [Python subprocess]  已经被 fork, env 快照是空的
              ├─ os.environ.get("NOTES_CONTENT")  → ""
              ├─ prompt 始终是空壳
              ├─ LLM 收到: "请分析以下知识: \n\n═══ 笔记 ═══\n(空)\n..."
              │
              ├─ Qwen3 返回: "本期无笔记可分析..." 或 timeout
              │
              ├─ RC=1 / empty_response
              │
              ├─ # ⚠️ BUG 2: 机械 fallback
              ├─ if [ $RC -ne 0 ]; then
              │     # 把采集到的"容器标题"直接拼成 review
              │     cat > review.md << REOF
              │     # 知识回顾 $DATE (LLM 不可用, 基础整理)
              │     $DATE_MATCHED_LINES
              │     💡 回复任何话题可深入讨论
              │     REOF
              │   fi
              │
              ├─ # ⚠️ BUG 3: status.json 永远写 true
              ├─ echo '{"llm": true, "ok": true}' > last_run_review.json
              │
              ├─ # 推送
              ├─ openclaw message send ...
              │   → WhatsApp + Discord 推送的是:
              │     "# 知识回顾 2026-04-10
              │      ## 今日arXiv精选(2026-04-10)
              │      ## 今日HN精选(2026-04-10)
              │      ## 今日 DBLP(2026-04-10)
              │      💡 回复任何话题可深入讨论"  ← 没有任何实质内容
              │
              └─ exit 0  ← 用户感知"正常推送"
                   │
09:03       [用户 WhatsApp]  收到看起来正常的推送
                   │
                   └─ 但点开发现: 全是日期标题, 零论文
                       ↓
                   用户追问 "回复任何话题可深入讨论" → LLM 不知道回顾内容
                   (因为 LLM 本来就没分析过)
```

## 2. 三层根因（触发器 → 放大器 → 掩护者）

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | shell 变量 `export NOTES_CONTENT` 在 `python3` 子进程调用之后才写入 | Python 子进程 fork 时 env 快照已经定型，`os.environ.get()` 始终返回空字符串 |
| **放大器** | 机械 fallback 把"行级日期匹配"的残渣当作"回顾内容"写入文件；硬编码源枚举漏掉 2 个 V37 新增源 (`ai_leaders_x` + `ontology_sources`)；行级日期匹配只保留含日期的单行，把整个 H2 section 的论文 body 过滤掉 | 失败路径不 fail-fast，反而装作成功；源列表漂移无监控；解析粒度错配结构化 markdown |
| **掩护者** | `status.json.llm = true` 永远写入；回顾文件虽然劣质但非空；无效果层不变式检测"回顾文件是否有实质内容"；PA 承诺"回复任何话题可深入讨论"但 LLM 从未真正看过回顾 | 所有监控和告警端点都说"正常"——治理体系从未问过"回顾的内容质量是什么" |

## 3. 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| V29 (2026-03-13) | kb_review.sh 首次上线，shell 变量传递 prompt | 引入 bug 1 (export 时序) |
| V29.x | 硬编码源列表 ≈ 7 个 | 初始无漂移 |
| V30.5 (2026-03-31) | DBLP/S2/HF_Papers 加入——但源列表硬编码更新了 | 维护者手动同步 |
| V37 (2026-04-08) | 对话数据捕获 + MEMORY.md 入 KB | 新源 `ai_leaders_x` 从未进入 kb_review 枚举 |
| V37.1 (2026-04-09) | `ontology_sources` 专属信息源上线 | 第二个源未被 kb_review 看到 |
| 2026-04-04 至 2026-04-10 | 用户每日收到"知识回顾"推送 | 内容降级，但 `status.json` 全绿 |
| 2026-04-11 08:55 | 用户发现问题："推送的回顾没有论文, 只有日期标题" | 第一次人工反馈触发排查 |
| 2026-04-11 09:30 | V37.5 完整修复 + INV-REVIEW-001 | 治理体系加入不变式锁定 |

## 4. 为什么以前没发生（条件组合分析）

| 条件 | 以前 | 现在 |
|------|------|------|
| shell 变量 export 顺序 bug | 存在 (V29) | 依然存在 |
| 硬编码源枚举 | 存在 (V29) | 存在 |
| 机械 fallback | 存在 (V29) | 存在 |
| `status.json.llm=true` 硬写 | 存在 (V29) | 存在 |
| 行级日期匹配 | 存在 (V29) | 存在 |
| 悬空 follow-up 承诺 | 存在 (V29) | 存在 |
| **监控真实效果的不变式** | ❌ 无 | ❌ 无 (v3.4 前) |
| **用户认真读回顾内容** | ⚠️ 忽略 | ✅ 追问 |

**6 个 bug 从 V29 就存在，但直到用户 2026-04-11 认真追问才被发现**——
因为前 6 个条件**单独不致命**（都像"能用"），组合起来才构成 silent degradation。
第 7 个条件（缺失效果层监控）是"掩护者"——在用户追问前，治理体系没任何信号。

## 5. V37.5 结构性修复（三层）

### Layer 1: 消除 bug class 本身

| V29 行为 | V37.5 修复 | 消除的 bug class |
|---------|-----------|----------------|
| shell `export` 传递 prompt | 全 Python 模块 `kb_review_collect.py` | shell scope bug class |
| 硬编码 `SOURCES=(...)` 枚举 | `load_sources_from_registry()` 读 jobs_registry.yaml | 源列表漂移 |
| 行级日期匹配 `if date in line` | `extract_recent_sections()` H2 章节 drill-down | markdown 结构粒度错配 |
| LLM 失败 → 机械 fallback | LLM 失败 → `[SYSTEM_ALERT] + exit 1` | silent degradation 本身 |
| `status.json.llm = true` 硬编码 | `llm_status: ok\|failed\|unknown` | 伪造成功状态 |
| "回复任何话题可深入讨论" | 移除 | 悬空承诺 |

### Layer 2: 治理层锁定（INV-REVIEW-001）

9 个声明层 check + 5 个运行时 check：

- **声明层**：V37.5 版本标记 / [SYSTEM_ALERT] 存在 / 不含机械 fallback / 不含悬空承诺 / `load_sources_from_registry` 函数定义 / `extract_recent_sections` 函数定义 / `call_llm` 函数定义 / jobs_registry.yaml 声明 kb_source_file / fail-fast 顺序锁
- **运行时层**：registry 真实发现 >=12 源（含 ai_leaders_x + ontology_sources）/ H2 parser 过滤正确性 / mock LLM 失败 → status=llm_failed 不伪装 / call_llm 最小内容阈值存在（80 chars）

### Layer 3: 测试回归锁定（test_kb_review.py 44 单测）

- TestLoadSourcesFromRegistry (7): 注册表发现契约
- TestExtractRecentSections (8): H2 parser 边界条件
- TestCollectNotes (4): 时间过滤 + frontmatter 剥离
- TestCallLlm (6): 空/短/HTTP 错误 fail-fast 契约
- TestRunOrchestrator (6): llm_caller 注入 + 失败路径不产出产物
- TestBuildOutputs (4): 输出格式契约
- TestCollectSources (1): registry→文件系统集成
- TestKbReviewShellGuards (8): kb_review.sh 源文件断言锁

## 6. 给本体喂养的启示

### 6.1 MR-4 (silent failure is a bug) 血案类持续扩展

| 案例 | 版本 | 机制 | 修复范式 |
|------|------|------|---------|
| notify.sh stderr 吞掉 | V37.1 | 通知失败无诊断 | MRD-ERROR-001 |
| Dream retry silent truncation | V37.4.2 | cache server 命中 | 变体 prompt + retry |
| governance summary 吞 error | V37.3 | `failed = count("fail")` 漏 `error` | MR-7 + INV-GOV-001 |
| PA 告警污染 | V37.4.3 | 告警 → 上下文 → LLM 跨主题 | INV-PA-001/002 |
| kb_review silent degradation | V37.5 | 6-bug 相互掩护 | INV-REVIEW-001 |

**共同特征**：声明层检查全过 → 运行时/效果层失败 → 用户追问才发现。

**共同修复模式**：
1. 消除触发器（底层 bug）
2. 加结构契约（fail-fast / 顺序锁 / 标记隔离）
3. 加治理不变式（运行时 python_assert）
4. 加回归测试（可重复验证）
5. 写案例文档喂养本体

### 6.2 对元规则的启示

- **MR-6** (critical 不变式 ≥2 层深度): INV-REVIEW-001 声明 + 运行时双层，符合
- **MR-4** (silent failure is a bug): INV-REVIEW-001 是 MR-4 的第 5 个血案样本
- 建议新增元规则候选 **MR-8** (data flow must be single-source-of-truth):
  硬编码枚举 vs registry-driven 的漂移是本案例核心放大器；同样的漂移在
  crontab vs jobs_registry.yaml（已有 INV-CRON-003/004）和源列表 vs registry
  都出现过。是否抽象为"所有跨文件枚举必须声明单一真理源"？

### 6.3 对验证深度的启示

- 当前 INV-REVIEW-001 是 declaration + runtime (2 层)
- **效果层（Layer 3）依然空白**：没有任何不变式验证"推送的 review 内容质量 > 阈值"
- 下一步候选：添加"最近 7 天 review 文件字符数分布" 效果层检查
  （已有 MRD-NOTIFY-002 日志活动检查作为效果层模板）

## 7. 参考

- **修复 commit**: V37.5 (2026-04-11)
- **相关不变式**: INV-REVIEW-001 (governance v3.5)
- **相关血案**:
  - `pa_alert_contamination_case.md` (V37.4.3) — 数据结构性污染血案类
  - `governance_silent_error_case.md` (V37.3) — 治理自观察血案
  - `dream_map_budget_overflow_case.md` (V37.4) — 预算+缓存稳定性
- **原则引用**: 宪法 #26 (异常分析必须输出完整因果链架构图)
- **测试文件**: `test_kb_review.py` (44 单测)
- **源代码**: `kb_review_collect.py` + `kb_review.sh` (thin wrapper)
