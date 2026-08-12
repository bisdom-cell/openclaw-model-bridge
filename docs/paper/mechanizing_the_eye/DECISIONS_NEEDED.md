# 待用户决策项 — Observer 论文（论文 #2）

> 第一稿（`draft.md` v0.1, 2026-08-12）已完成。以下决策不阻塞正文打磨，但**阻塞投稿**。
> 镜像论文 #1 `DECISIONS_NEEDED.md` 流程（7 项决策 → 用户 2026-06-11 一次性定夺的先例）。
> 论文 #1 已有先例的项目默认沿用，仅需确认；真正的新决策是 **#1 时机**。

## 1. 🔴 投稿时机（本论文独有的核心决策）

生产窗口是安静窗（on 期 12 观测日 fired=0）：H1 live precision 0 分母未定义、H2 无 live 样本、H3 零显化 FN。§9.2.5 预注册承诺"负结果可发表"，但**何时**发表是用户的判断：

| 选项 | 内容 | 优劣 |
|---|---|---|
| **(a) 现在投（安静窗版本）** | 以"方法 + 工具 + 预注册负结果"定位挂 arXiv | 最快建立 pre-registration-for-observability 话语权；论文自身践行 §9.2.5（不等正例=不受 file-drawer 激励）；弱点=零 live TP，H1/H2 全构造性 |
| (b) 延长观察窗后投 | 按 §9.2 协议以新 cutoff 重新机械落表（append），等首个 fired verdict 或再 N 周 | live 数据可能变强；风险=可能继续安静（era mismatch 混淆），且"等正例再发"正是预注册要打破的激励——需明确预注册延长规则再等，避免 motivated waiting |
| (c) 先投 workshop/短文再扩 | 方法学部分先行（§6 pre-registration + §7 observer incident log） | 快速反馈；碎片化风险 |

Claude 推荐 **(a)**：论文的主张结构已经把安静窗写成方法学卖点而非缺陷（§6.6 三次规则压制便利 + §8.3），且 corpus 侧证据链完整（6/6 · 0/4 · 4/4 + sabotage）。若选 (b)，建议现在就把"延长到什么信号为止"写成预注册修订（如"至首个 fired verdict 判定完成，或至 2026-Q4 复核日，先到者为准"），否则违背 §9.2 精神。

## 2. 标题（三选一或另提）

1. **Mechanizing the User's Eye: Pre-Registered Deployment of a Sabotage-Validated Fail-Plausible Observer in a Production LLM Agent Runtime**（当前工作标题：开放问题钩子 + 方法学卖点前置）
2. From Description to Prediction: Building, Auditing, and Honestly Reporting an LLM Observer for Silent Failures（叙事弧线优先）
3. The Judge Inherits the Taxonomy: A Fail-Plausible Observer and Its Own Incident Log（§7 差异化亮点优先）

Claude 推荐 1：与论文 #1 的"概念钩子 + 实证副题"风格一致；"Pre-Registered"+"Sabotage-Validated"是本文两个方法学卖点，标题即摘要。

## 3. venue（论文 #1 先例：arXiv 直发）

- 默认沿用：**arXiv 直发 cs.SE（cross-list cs.AI）**，实名，与论文 #1 形成系列。
- 会议版候选（后续）：ICSE/FSE SEIP、ISSRE（论文 #1 已投 Industry Track，通知 8-12——**若论文 #1 被 ISSRE 录用，#2 的会议版策略可能联动**，建议等通知落地后再定会议路线）。
- 需确认：是否等 ISSRE 通知（本周）再定 venue。

## 4. 署名与 AI 披露（论文 #1 先例：直接沿用）

- 用户实名单一作者 + Acknowledgments 致谢 AI 协作者 + Contributions 分工段（论文 #1 决策 3 原样沿用）。
- 本论文 §8.5/§9 已把"作者判定自己系统的 TP/FP"写为 threat + 缓解——披露本身仍是叙事的一部分。
- 待用户确认沿用即可（预计零新决策）。

## 5. 内容取舍（需要用户学术判断）

1. **§7 Observer 自身事故表（O1-O8）**是本文最差异化的一节，也是最"自曝"的一节——8 条里 6 条是我们自己的工程失误。当前写法把它作为 thesis 证据（judge inherits taxonomy）。保留全表 / 精简到 4-5 条 / 移附录？Claude 推荐保留全表（这是论文 #1 "two bugs in itself" 的完整续篇，审稿人会认可诚实密度）。
2. **§6.6 "三次规则压制便利"**：写法直白（"the paper you are reading complies"）。保留这种自指语气还是改中性？Claude 推荐保留（与论文 #1 结尾风格一致）。
3. fail-plausible 与论文 #1 的关系表述：当前把本文完全定位为 follow-up（标题不含 fail-plausible 品牌词）。是否希望标题带 fail-plausible 强化品牌？（选项 3 部分满足）
4. figures 计划（LaTeX 阶段）：Fig.1 两层管道架构（§3 ASCII 有底稿）；Fig.2 时间线（shadow→flip→on→cutoff，§6.1）；Fig.3 O1-O8 observer 自身事故的 taxonomy 映射图；Fig.4 regime 对账 + 判据核对流程图。数量与论文 #1 相当。

## 6. 与仓库工件的引用关系

- 论文引用 design doc §9.1/§9.2（预注册文本）作为 artifact——投稿前这两节需要英文版吗？选项：(a) 保持中文 + 论文内英文转述（当前）(b) 出英文对照版入 repo。Claude 推荐 (a)（artifact 是"存在性证明"，语言不影响可验证性；审稿人核对的是时间戳与规则-数据顺序）。
- `data_inventory.md` 已建（每数字→仓库溯源）；投稿前照论文 #1 流程重新对表。

## 7. 下一步路线（Claude 可自主推进部分）

- ✅ 第一稿 + data_inventory + 本决策文档（2026-08-12）。
- 可自主：正文第二轮精磨 / 相关工作补充检索（LLM observability、AIOps LLM-judge 生产部署有无先例——本稿 §2.3 声称 "we know of no prior report"，投稿前需一轮认真检索加固或软化措辞）/ §3 ASCII 架构图。
- **需决策后**：LaTeX 转换 + figures + arXiv 元数据（等 #1/#2/#3 决策）。
- **球在用户：#1 时机（核心）+ #2 标题 + #3 venue 确认；#4-#6 确认沿用即可。**
