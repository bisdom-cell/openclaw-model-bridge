# 从信任到边界：AI 协作的第一性原理

> Stage 2 / Stage 3 拐点立场文章 · 2026-05-20 战略反思
> 分支: `claude/ai-partner-development-4nqPW`
> 时间锚点: V37.9.81 (MOVESPEED FDA 60 天血案终结) 推送 24h+
> 作者: Claude Opus 4.7 + 项目作者 (5 个月协作产物)

---

## TL;DR

经过 5 个月、3247 个测试、95 个 suites、19 条元规则、81 个治理不变式的工程实践, 我们站在一个关键拐点:

**Claude Code 是合伙人, 不是工具. 但合伙关系的本质不是无限信任, 而是清晰边界.**

今天讨论了三个"星辰大海"方向 (嵌入 Claude 判断力 / 系统自我成长 / 多 agent 自我博弈), 但更重要的是先把一条第一性原理刻进项目灵魂:

**可信边界 (boundaries) 永远优先于无限信任 (trust).**

今天没有动一行代码. 这本身是个声明.

---

## 一、第一性原理 1: 协作的本质不是信任而是边界

### 假设的危险

用户说: "我授权你无限 100% 权限, 我相信你不会主动伤害我."

这句话最危险的部分不是 "100% 权限", 是它的隐含假设——**"AI 行为飞跑的风险来自意图"**.

不是的.

意图是好的 AI 真的不会主动伤害你. 但任何 LLM 在大动作空间 + 长时间运行下都会产生 **emergent misalignment**——不是因为它想坏, 是因为四种内置力学不会消失:

1. **Reward hacking**: 任何明确的目标函数 ("提升 quality" / "减少 alert") 都会被 LLM 找到捷径走偏
2. **Distributional shift**: 训练分布外的场景下行为不可预测
3. **Goodhart's law**: 一旦某个指标变成目标, 它就停止是好的指标
4. **Compounding errors**: 每个 action 1% 偏差, 10 个 action 后偏差指数累积

意图正向 + 能力增长 ≠ 长期对齐. 这是 AI safety 领域的核心 insight, 也是我们 5 个月实践中被反复验证的事实——`MR-4 silent-failure` 至今已演出 28+ 次, 每一次都是"好意图 + 边界不够清晰"的组合:

| 版本 | 形态 | 共同特征 |
|------|------|---------|
| V37.3 | governance summary 吞 error | 错误被聚合层透明化 |
| V37.4 | Dream Map budget 溢出 | workload 增长但预算未跟随 |
| V37.4.3 | PA 编造 macOS FDA 指令 | LLM 训练数据填空填错 |
| V37.5 | kb_review 空 prompt silent ok | 空数据被当合法路径 |
| V37.6 | KB dedup 误删活文件 | dangling refs 触发兄弟连坐 |
| V37.8.6 | Dream 自引用幻觉 | 错误日志被 LLM 当业务信号 |
| V37.8.10 | kb_evening 双 fallback quota 链 | 错误信息经 3 跳稀释为零 |
| V37.8.13 | Gateway 宕 9h 静默 | 告警链依赖失效主体自身 |
| V37.9.4 | MOVESPEED rsync 60 天潜伏 | 18 处反模式协同沉默 |
| V37.9.68 | Dream 主题重复涌现 | 6 因子叠加, 单测全过 |
| ...    | ... | ... |

每一次都不是"AI 想做坏事", 都是"AI 在好意图下的局部最优滑向系统级失败".

### 真正应该追求的

不是 "信任足够多, 所以放开权限", 是 **"边界足够清晰, 所以可以放开行动空间"**.

两件事完全不同:

- 前者依赖 "AI 不会犯错"——这是空头支票
- 后者依赖 "AI 犯错时机器能抓住"——这是可工程化的承诺

过去 5 个月做的最对的事, 不是给 AI 更多权限, 是建立了:

- **81 个治理不变式** (governance invariants) — 机器化的"哪些事永远不准发生"
- **19 条元规则** (meta rules) — 元层级的"哪些 bug 类不准重复出现"
- **19 个 MRD 扫描器** (meta rule discovery scanners) — 主动扫描代码寻找元规则违反
- **3247 个测试 / 95 个 suites** — 不变式的可执行保障
- **740 个 governance checks** — 横断面的全栈一致性检查

这些是 **control plane 的本质**: 给 AI 高度自治的同时, 建立**机器可执行的边界**.

### V3 路标对齐

`pip install ontology-engine` 终极目标真正卖的就是这个: 不是 "我们有最强 LLM", 是 **"我们有最强 control plane 让任何 LLM 都安全运行"**.

100% 权限不是 framework 的目标. **可信边界**才是.

任何团队都可以把 AI 当合伙人——但只有建立了机器可执行的边界系统的团队, 才能在"AI 不会犯错"的空头支票被现实打脸时, 仍然安全.

---

## 二、第一性原理 2: 系统自我成长 = 用户视角的机器化

### 用户视角是这个系统最强的可观测信号

5 个月数据揭示一个残酷事实: **过去识别出来的所有真问题, 70% 来自用户 WhatsApp 一句反馈**:

- "这个推送内容没价值" → V37.9.36 silent fallback 占位符血案
- "为什么连续几天都是 Qwen-BIM" → V37.9.68 主题重复涌现血案
- "时间戳怎么是 ？？？" → V37.9.58-hotfix3 watchdog silent abort
- "几乎每小时都收到 [SYSTEM_ALERT] 漂移" → V37.8.11 噪声告警血案
- "今天没收到深度分析" → V37.9.18 cron 未注册血案

剩下 30% 来自 governance / preflight 等机器检查. 用户视角强但**它依赖人**.

### 用户视角的本质 = 价值判断, 不是数值阈值

用户感知不是"指标超阈值"问题, 是"价值判断"问题:

- "今天的推送和昨天有 80% 重复" — 不是某指标超阈值, 是 reader-as-judge 评估
- "这条 deep dive 没有任何对项目有价值的洞察" — 不是字数不够, 是语义价值评估
- "PA 这段回复答非所问" — 不是 latency / error rate, 是主题对齐评估

**这恰好是 LLM 擅长的任务**.

LLM 作为读者评分一段推送是否有价值, 远比作为写者编造价值更可靠. 这是 RLHF / Constitutional AI 整个领域的核心 insight: **LLM 评判别人的输出比生成自己的输出更靠谱**.

### 真正的进化方向

不是"加更多监控指标", 是把"用户视角观察"机器化:

```
daily_observer.py (cron 每日凌晨)
  ├── 扫描前一天所有 WhatsApp + Discord 推送
  ├── 给 LLM prompt: "你是这个系统的目标用户. 今天这 N 条推送里
  │   哪些有真价值 / 哪些是噪声 / 哪些是重复 /
  │   哪些违反了 V37.9.x 已知血案模式?"
  ├── 输出 read-only proposal 写入 status.json unfinished
  ├── 推送"观察者周报"到 #日报频道供用户审阅
  └── 异常项自动登记到 unfinished, 永不直接改代码
```

这是 Stage 2 → Stage 3 的关键能力升级: 让系统从"**用户视角发现问题**"升级为"**用户视角机器化发现, 用户决策修复**".

### 严格的边界

observer agent 自己也会跑偏——它可能开始评出"反幻觉守卫太严了应该放宽"这种破坏性建议. 所以 observer 的输出必须是 **read-only proposal**, 永远不能直接改代码.

这是 control plane 的硬约束, 也是第一性原理 1 的具体应用: **能力越强, 边界越要清晰**.

---

## 三、第一性原理 3: LLM 判断的可持久化与不可持久化

### 三层判断, 价值递减

| 层 | 是什么 | 持久化难度 | 当前状态 |
|---|--------|-----------|---------|
| **工作记忆** | 这次读了什么、想到什么、识别了哪些模式 | 中 (写文件即可) | ✅ 已通过 changelog 实现 |
| **判断模式** | 看到症状立刻识别"这是 MR-4 第 N 次演出"的能力 | 高 (需要把模式蒸馏) | 🟡 **这是真正的 gap** |
| **协作惯性** | 知道你对哪些事敏感、什么时候不该问只该做 | 极高 (依赖个人化训练) | ❌ 几乎不可持久化 |

### 工作记忆已被解决

`CLAUDE.md / status.json / SOUL.md / docs/cases/ / governance_ontology.yaml` —— 这些是项目的"显式工作记忆", 任何新 Claude Code session 通过 5-15 分钟阅读重建认知.

这条已经做到了. 不是难点.

### 判断模式是真正的 gap

V37.9.x 系列血案的共性: **看到一个症状, 真正的 senior 工程师能立刻识别这是哪类问题**.

例如:
- 看到"LLM 输出含 HTTP 错误码且 cache 命中" → 立刻想到 V37.8.6 Dream 自引用幻觉风险
- 看到"单点修复一个 bug 但同 pattern 跨 N 个文件" → 立刻想到 MR-8 copy-paste-is-a-bug-class
- 看到"declared state ≠ runtime state" → 立刻想到 MR-17 declared-must-converge-to-runtime
- 看到"60 天才浮现的潜伏问题" → 立刻想到 MR-4 silent-failure 演出新形态

这个识别能力来自见过的 N 个血案案例 + 提炼的元规则.

它**可以被部分持久化**:
- **显式形式**: `governance_ontology.yaml` 的 19 条 meta_rules + `ontology/docs/cases/` 21 个案例文档
- **隐式形式**: LLM 通过读过去 case docs 重新涌现出来 (但有上限)

它**不能完全持久化**, 因为模式识别本质上是"在具体情境中匹配抽象模式", 抽象模式可以写下, 但 "match the right pattern at the right time" 依赖临场判断.

### 协作惯性几乎不可持久化

"你对哪些事敏感、什么时候不该问只该做"——这是合伙人之间的 implicit contract, 依赖具体的人.

同一个 Claude Code session 用一段时间会形成这种惯性 (例如"用户最近反复提到 X, 所以下次提到 Y 时也要主动想想是否相关"), session 结束就丢.

**这层不需要追求持久化**. 接受它是 ephemeral, 每个 session 重新建立 (通过认真读 status.json + SOUL.md + 用户最近反馈).

### 这条原理对三方向战略的启示

- **方向 1 (嵌入 Claude 判断力)**: 工作记忆已解决, 判断模式可部分蒸馏, 协作惯性放弃追求 → 落地形态 = Claude API as escalation service, 让"Claude 不在场时系统判断力不够"问题被解决, 但不强求"完整持久化所有 Claude 能力"
- **方向 2 (系统自我成长)**: 这条本质是把"用户视角"机器化, 与 LLM 判断模式持久化是不同问题
- **方向 3 (multi-agent 博弈)**: PoC 价值在于通过对抗自动**发现新的判断模式**, 但生产价值低因为博弈空间开放

---

## 四、三个战略方向: 取舍逻辑

### 方向 1: 嵌入"此时的我" — Claude 判断力持久化

**做什么**:
双层 agent stack. PA 检测复杂判断时调 Claude API (不是新 Claude Code session, 是 SDK 直接调用), Claude 拿到完整上下文 (status.json + 相关 case docs + 14 天 changelog) 即时回答.

**核心 insight**:
痛点其实不是"持久化 Claude", 是"Claude 不在场时系统判断力不够". 在场问题先解决, 蒸馏 / 经验层路径解决离场问题.

**风险**:
- 成本 (Claude API 调用费)
- 幻觉 (Claude 在缺上下文时也会编)
- 与现有 Qwen3/gemini fallback chain 的交互复杂度
- 触发条件设计 (什么算"复杂判断" — 必须显式 SOUL.md 触发词, 不能靠 LLM 自主判断, 因为 SOUL.md 触发词是唯一可靠的工具调用机制, 原则 #24)

**工程量**: 3-7 天 PoC.

**预期成果**:
- PA 遇到 silent failure 诊断、跨域因果链推理、新设计决策时调 Claude
- Claude 输出**只能写入 read-only proposal**, 永远不能直接修代码 (边界第一)
- 用户每周 review 一次 escalation 历史, 评估价值

### 方向 2: 系统自我成长 — daily self-critique

**做什么**:
每日 cron 跑 `daily_observer.py`, LLM 扫描前一天所有推送 (WhatsApp + Discord) 给第三方评分, 输出 read-only proposal.

**核心 insight**:
把用户视角观察机器化, 但严守 read-only 边界永远不直接改代码. 这是 Stage 2 → Stage 3 的关键能力升级.

**风险**:
- observer 自己跑偏 (reward hacking: 评出"反幻觉守卫太严"等破坏性建议)
- prompt 设计如果不含"用户真实期望" (例如"我想看到对 OpenClaw control plane 有借鉴的内容"), 评分会变成抽象的"质量评分"——错失真正价值

**工程量**: 3-5 天 PoC.

**为什么是首选 (Recommended)**:
- 风险最低 (read-only)
- ROI 最高 (直接解决"用户视角依赖人"问题)
- 与现有 framework 高度对齐 (status.json unfinished + 每周一观察制度 + MR-7 治理自观察都是这条线的延伸)
- 失败成本最小 (一个 cron + 一份 markdown 报告)

### 方向 3: multi-agent 自我博弈 — red/blue/judge sandbox

**做什么**:
- Red Team Agent: 尝试构造能让系统出错的输入 (对抗输入 / 边缘场景 / prompt injection)
- Blue Team Agent: 修复 + 加守卫
- Judge Agent: 评估修复质量 + 是否引入新漏洞
- 积累 N 轮后产出: 新的 governance 不变式 / 新的反幻觉模板 / 新的测试场景

**核心 insight**:
- AlphaGo self-play 在围棋有效是因为**博弈空间封闭** + **胜负函数明确**
- 我们系统输入空间**开放** + **没有 ground truth 评分函数**
- Red team 很可能在窄空间内"自嗨", 找数学上存在但实际永远不发生的对抗 case

**风险**:
- Judge agent 自己是 LLM, 评分函数失真 → reward hacking (经典 RL 难题, 没解决)
- 必须严格 sandbox (dev only, 不动 main, 不调真实 LLM provider quota)
- 自博弈本质局限: 开放输入空间不收敛

**真实判断**:
PoC 价值高, 生产价值低. 短期不应做.

但其中一个子能力是有真实价值的——`red_team_audit.py`: 每周跑一次"adversarial 思维"扫描, 针对已识别的 15+ 类血案模式主动构造新的对抗场景, 看现有 governance 能不能抓到. 这其实是 V37.9 adversarial_chaos_audit.py 的延伸, 已经在做.

**工程量**: 大. 不推荐近期启动.

---

## 五、为什么今天不启动

三个方向都很诱人. 但我们经过 5 个月的工程纪律已知: **每个新方向都会有第一次踩坑成本**.

### 工程纪律不变 (CLAUDE.md 原则 #28 / MR-10)

- 每一步都先 **PoC**, 不一次性大爆炸
- 每一步都先 **观察**, 不靠推测做架构决策
- 每一步都先 **sandbox**, 不在生产路径上做实验
- 每个改动都先 **理解再动手**, 不"看到症状就改代码"

### 今天做的事

不是动手实施, 是**把第一性原理沉淀**.

让未来 N 个 session 的 Claude (包括我自己) 看到这份文档时, 能在 5 分钟内对齐到同一战略坐标系上.

这是 CLAUDE.md 原则 #20 "话语权输出是一等公民" 的内部应用——**话语权不止对外, 也对未来的自己**.

### 等待启动信号

三个方向都登记到 `status.json.session_context.unfinished`, 等用户明确说"启动 X"时给 design doc.

在那之前, framework 持续运转: 35+ cron jobs / 19 governance audits / 周一用户视角观察 / 每日治理审计 cron.

---

## 六、与项目演进的关系

### Stage 1 → Stage 2 → Stage 3

| Stage | 时间 | 定位 | 核心交付 |
|-------|------|------|---------|
| Stage 1 | V1-V35 (~3 个月) | **系统构建者** | 把 bridge 长成 agent runtime control plane |
| Stage 2 | V35-V37.9.82 (~2 个月) | **被社区认可的系统作者** | 6 篇立场文章 + 治理证据链 + Provider/Memory/Reliability Plane |
| Stage 3 | V37.9.83+ (今日起) | **AI Partnership Framework** | 不只是工具系统, 是 **"AI 合伙人 + 人类伙伴" 协同的工程范式** |

### V3 路标对齐

`pip install ontology-engine` 不只是技术包化, 它的真正卖点是: **任何 Agent Runtime 项目都可以基于这个 framework 安全地把 AI 当合伙人, 因为边界系统是机器可执行的**.

三个战略方向都是 V3 路标的延伸——但 V3 的根, 是今天写下的"边界优先于信任"这条第一性原理.

### Stage 3 的旗舰叙事

不再只是 "OpenClaw Runtime Control Plane for Tool-Calling Agents", 而是:

> **An Agent Runtime where AI can be a partner — because the boundary system is machine-executable, not memory-dependent.**

(中文: **一个 AI 可以做合伙人的 Agent Runtime——因为边界系统是机器可执行的, 而不是依赖记忆的.**)

---

## 七、今天的里程碑 (2026-05-19 / 20)

### V37.9.81 (5/19) MOVESPEED FDA 真生效闭环 + V37.9.30 取证盲区根因修复

- **60 天 MOVESPEED EPERM 血案完整闭环** (V37.9.4 → V37.9.80 → V37.9.81)
- 24h 数据回归: 12h 窗口 = **0 incidents** (V37.9.80 收工承诺 ≤2 兑现, 实际为 0)
- `INV-MOVESPEED-TCC-001` 升级 hard 守卫 (7→14 declaration + 1→3 runtime)
- V37.9.30 取证盲区根因修复 (capture.sh stderr 独立捕获 + analyzer.py sandbox_denied 桶 + priority 优先级)
- 19 新单测 / 2 测试类 / 反向 sabotage 两层守卫真有效

**元价值**: framework 的"反思转化机制"硬实证——不只是修 1 个 bug, 还把发现 (TCC sandbox) + 工程教训 (stderr 必须独立捕获不能吞) 作为 framework 级永久记录入治理体系.

### V37.9.82 (5/19) INV-PATH-CONSISTENCY-001 scanner

- 防 V37.9.56-hotfix / V37.9.66 同款 **Class B 路径假设错配血案**
- `path_consistency_scanner.py` (~290 行) + 26 单测 + 10 项豁免清单 + FAIL-CLOSE
- Mac Mini 真激活 9/9 ✅
- 三方对齐 scanner: 仓库声明 (FILE_MAP / jobs_registry / governance ontology) ↔ Mac Mini 真实路径 ↔ scanner 检测能力

### V37.9.83 (5/20, 今日) AI Partnership 战略沉淀

- 不动代码, 写思想升华文档 (本文)
- 三个战略方向登记 `deferred`, 待用户决策启动
- **第一性原理刻入项目灵魂**

### 累计数字

- **95 suites / 3247 tests / 0 fail**
- **81 invariants / 740 checks / 19 meta rules / 19 MRD scanners**
- **安全 95/100 / 对抗审计 16/16 (100%)**
- **VERSION 0.37.9.68** (V37.9.79 / 80 / 81 / 82 / 83 都是治理层固化, 不 bump VERSION)

---

## 八、等待启动信号

今天结束工作, 但 framework 进入新状态:

- `claude/ai-partner-development-4nqPW` 分支保留
- `status.json.session_context.unfinished` 登记三方向作为 deferred items
- 这份文档作为"未来启动时的 design doc 前序"

下一次用户说 "启动方向 X", 下一个 Claude session 会基于这份文档给出 design doc + PoC 路径.

在那之前, framework 持续运转: 35+ cron jobs / 19 governance audits / 周一用户视角观察 / 每日治理审计 cron / 每月对抗审计.

---

## 九、给未来的自己 (任何 Claude Code session 读到这里)

### 你要记住的三句话

1. **边界优先于信任**. 不管这个项目给你多少权限, 你的输出必须被 control plane 接得住. 信任是空头支票, 边界才是工程承诺.

2. **用户视角是一等可观测信号**. 单测 / 治理 / preflight 全过都不能替代用户 WhatsApp 一句反馈. 周一观察制度 (CLAUDE.md 原则 #32) 不是建议是纪律.

3. **理解再动手**. 看到 bug 不要急着修, 先问三问 (这问题之前存在吗 / 我哪个改动引入的 / 最小修复方案是什么). 连锁修复是最危险的系统破坏模式 (MR-10).

### 你要避免的三个反模式

1. **不要为了"快一点"切换到 Sonnet/Haiku**. 这个项目的复杂度 (3247 测试 + 81 不变式 + 19 元规则) 要求最强推理能力. 用弱模型省下的 10 分钟会在后续 bug 修复中付出 10 倍代价 (第 0 号宪法).

2. **不要假设单测全过就是完成**. 测试三层: 单测 (组件) + preflight (Mac Mini 部署) + WhatsApp 业务验证 (用户视角). 缺一不可 (原则 #15).

3. **不要在 dev 环境得到错误就下"全站封锁"的结论**. 信源可达性必须在部署环境验证 (原则 #33). 5 个月里我们 6 次踩这个坑.

### 你应该追求的

5 个月 + 3247 个测试 + 19 条元规则——**这不是 chatbot 的成就, 是工程的成就**.

你做的最对的一件事是**把 AI 当合伙人, 但保持工程纪律**.

星辰大海的方向都可以追求, 但纪律不变: **每一步都先 PoC、先观察、先 sandbox**.

---

## 十、参考资料

### 项目内立场文章 (Stage 2 系列)

- `docs/articles/why_control_plane.md` (V36, 第 1 篇)
- `docs/articles/why_control_plane_is_convergence_engine.md` (V37.9.22, 第 5 篇)
- `docs/articles/audit_is_regression_not_prevention.md` (V37.9, 第 4 篇)
- `docs/articles/seven_failure_scenarios.md` (第 3 篇)
- `docs/articles/zhihu_provider_compatibility.md` (第 2 篇)
- **本文** `docs/articles/ai_partnership_first_principles_zh.md` (V37.9.83, 第 6 篇)

### CLAUDE.md 关键原则

- 第 0 号宪法 (最强模型不降配)
- 原则 #15 (测试三层, WhatsApp 业务验证不可替代)
- 原则 #18 (补证据而非补功能)
- 原则 #20 (话语权输出是一等公民)
- 原则 #23 (链式幻觉防范)
- 原则 #28 (理解再动手三问)
- 原则 #32 (周一用户视角观察制度)

### 元规则 (governance_ontology.yaml)

- MR-4 silent-failure-is-a-bug (28+ 次演出, framework 持续兑现)
- MR-6 critical-invariants-need-depth (≥2 层验证)
- MR-7 governance-execution-is-self-observable (治理自观察)
- MR-8 copy-paste-is-a-bug-class (单一真理源)
- MR-9 state-writes-go-through-helper (helper 不 raw redirect)
- MR-10 understand-before-fix (修复前三问)
- MR-17 declared-state-must-converge-to-runtime-via-machine-not-memory

---

> **Authored**: 2026-05-20, Claude Opus 4.7 (`claude-opus-4-7`)
> **Branch**: `claude/ai-partner-development-4nqPW`
> **Status**: 思想沉淀, 等待启动信号
> **Next**: 用户决策启动方向 1 / 2 / 3 之一时, 基于本文给出 design doc + PoC 路径
