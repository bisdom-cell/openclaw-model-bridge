# 待用户决策项 — Silent Failures Taxonomy 论文

> 第一稿（`draft.md`）已完成，以下决策点不阻塞继续打磨正文，但**阻塞投稿**。
> 协作模式（unfinished #5 登记）：用户提供领域洞察 + 学术经验，Claude 提供工程数据整理 + 论文结构组织。

## 1. 标题（三选一或另提）

1. **When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime**（当前工作标题 — 概念钩子前置）
2. Silent Failures in LLM Agent Systems: An Eight-Week Longitudinal Study of 22 Production Incident Postmortems（朴素实证风格）
3. Fail-Plausible: How LLM Agent Systems Turn Errors into Believable Output（术语品牌优先）

Claude 推荐 1：审稿人/读者第一眼抓住 Class D 的独特性，副标题保住实证定位。

## 2. 作者署名与 AI 贡献披露

- 用户真名 + 单位/独立研究者身份如何署？
- Claude 的角色如何披露？选项：(a) acknowledgments 致谢 AI 协作者（当前主流且最稳）(b) Contributions 段详细披露分工 (c) 部分 venue 允许/讨论 AI 共作者 — arXiv 本身无限制但学术惯例倾向 (a)+(b)。
- **建议**：(a)+(b) 组合 — 致谢 + 明确分工段。论文 §7 已经把"AI 协作下的可靠性工程"作为一个发现来写，披露本身是论文叙事的一部分，藏反而减分。

## 3. 目标 venue 与匿名化

| 选项 | 节奏 | 匿名要求 | 适配度 |
|---|---|---|---|
| **arXiv 直发 (cs.SE, cross-list cs.AI/cs.DC)** | 随时 | 无 | 最快建立话语权 + PyPI artifact 同步引用；可后续再投会议 |
| ICSE/FSE SEIP (经验报告 track) | 按 CFP | 双盲 → 需匿名化系统名/repo | 实证 SE 社区最对口 |
| HotOS / SoCC / OSDI 类系统会 | 按 CFP | 部分单盲 | gray failure 谱系的本家，但更偏 infra |
| EuroSys / NSDI 类 | 按 CFP | 双盲 | 同上 |

**建议**：先 arXiv 直发（系统名/repo 实名，artifact 链接齐全），再视反响决定会议版。这也符合 unfinished #5 的时机判断（"ontology-engine pip 包就位后投稿" — 已就位）。

## 4. 系统/用户身份暴露度

- repo 实名 `openclaw-model-bridge` + PyPI `openclaw-ontology-engine` 已公开 → arXiv 路线无矛盾。
- 若走双盲会议：需要匿名化分支（系统化名 + anonymized repo）。决策后再做，工作量 ~半天。
- 注意：论文不含任何 API key / 手机号 / 内网 IP（已按仓库安全规则写作；终稿前跑一次安全扫描照常）。

## 5. 语言与格式

- 当前：英文 Markdown 第一稿。
- LaTeX 转换（arXiv 标准 two-column 或 preprint 单栏）：venue 决定后做，~半天（含 causal-chain 图的 TikZ/figure 化 — 现 ASCII 图需重绘 2-4 张正式 figure）。
- 是否同步出中文版（知乎/公众号传播层）：建议论文先行，中文科普版作为发布配套。

## 6. 内容取舍（需要用户学术判断）

1. **§7 "AI-assisted operation of an AI system" 段**保留多重？这是差异化亮点但也可能被部分审稿人视为离题 — 当前写法是一段克制的 observation。用户判断。
2. **fail-plausible 术语**：是否检索确认无撞名（Claude 初查未见学术撞名，但建议用户在学术圈内再确认一次）。
3. **figures 计划**（LaTeX 阶段）：Fig.1 三平面架构 + 监测面；Fig.2 taxonomy 树；Fig.3 D1 幻觉链 causal chain（从 surrogate byte 到推送的完整链路图 — 最有传播力的一张）；Fig.4 潜伏期 vs 机制层散点；Fig.5 三步跃迁示意。
4. 是否补一节 **defense framework 的可复现实验**（用 PyPI 包在 WeatherBot demo 上跑 governance 的 walkthrough）— 提高 artifact evaluation 评分，+1 页。

## 7. 下一步路线（Claude 可自主推进部分）

无需决策即可继续：TBV 引用核实（4 个 arXiv 作者列表）/ 补三类 related work（hallucination survey、AIOps、chaos engineering）/ 正文打磨第二轮 / data_inventory 投稿前重新对表。
