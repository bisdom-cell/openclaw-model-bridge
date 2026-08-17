# QA4AGENTS 2026 投稿 Runbook（作者操作手册）

> 论文：*Silent Failures in a Production LLM Agent Runtime: A Labelled Incident Corpus and a Detection Benchmark*
> 正文源：`qa4agents_workshop_paper.md` ｜ 排版源：`../latex/qa4agents_workshop.tex`
> 已编译：**7 页 / 0 error / 0 overfull hbox / 0 undefined ref**（US Letter, IEEEtran conference）
> 目标：QA4AGENTS 2026（ISSRE 2026 卫星 workshop, Limassol, Cyprus）｜ chairs 邮件给的截止：**2026-08-19**
>
> **本文档的用法**：从 §1 开始，逐项打勾。§1 没做完之前不要做 §3。

---

## 0. 先决问题：投之前必须想清楚的一件事

**IEEE 会议/workshop 的通行规则是：论文被录用后，至少一位作者必须完成注册（通常是全额注册费），
论文才会进入 proceedings 与 IEEE Xplore；多数会议还要求到场报告，no-show 会被从 Xplore 撤下。**

所以在投稿之前先回答：

- [ ] 如果被录用，我是否愿意/能够承担**注册费 + 十月赴塞浦路斯 Limassol** 的成本？
- [ ] 如果不能到场，ISSRE 2026 是否允许**远程报告**？（必须在官网确认，不同年份政策不同）
- [ ] 如果两者都不行，那么被录用反而是负担 → 应改投 **industry talk**（8/23，不进 proceedings，
      但同样要到场）或**放弃本轮**，把精力转到论文 #2。

> 这一条放在最前面，是因为它决定"投不投"，而不是"怎么投"。先想清楚，比投中之后再纠结便宜得多。

---

## 1. 取三个决定性事实（**阻塞项，你来做，约 10 分钟**）

打开 <https://qa4agents.github.io/>，找到 Call for Papers，抄下三件事：

| # | 要确认的事 | 为什么它是阻塞项 | 记录 |
|---|---|---|---|
| 1 | **页数上限**（full / short 各是多少，是否含参考文献） | 现稿 **7 页**。若限 8 页可直投；若限 6 页或 4 页，必须先裁再投。**超页 = desk reject，不给申辩机会。** | ____ |
| 2 | **是否双盲**（double-blind / anonymous） | 现稿是**实名**的：有作者名、GitHub 仓库链接、arXiv 编号。若要求双盲，这三样都必须先移除，否则直接判 desk reject。 | ____ |
| 3 | **截止时间（含时区）+ 投稿入口链接** | chairs 邮件说 8/19，但没给链接与时区。学术界常用 **AoE (Anywhere on Earth, UTC−12)**：8/19 AoE = **香港时间 8/20 20:00**。若不是 AoE 而是本地时间，可能提前近一天。 | ____ |

**顺带确认（不阻塞但有用）**：论文类别（full / short / position / experience report 是否分开投）、
是否需要单独先注册摘要、是否要求 IEEE 模板的特定版本。

> 把这三格填好发我，我按结果执行 §2 的对应分支。我取不到这个页面——本机出站策略挡了 `github.io`。

---

## 2. 按 §1 结果做定稿（**我做，你审**）

### 2a. 页数分支

| §1 查到的限制 | 动作 |
|---|---|
| **8 页（含参考文献）** | 无需裁剪，直接进 §3。现稿 7 页留有 1 页余量。 |
| **6 页** | 我按 `.tex` 头部已写死的顺序裁：①§II 相关工作压到每主题一段 → ②D2–D4 各压到一句 → ③删§VI-C（事故三层结构）→ ④Table I 降为类级汇总。**绝不裁**：Table I 的存在本身、§VII benchmark 章、§VIII threats、GenAI 披露、13/22 计数更正。 |
| **4 页（short）** | 同上全裁，并把 §V 五类各压到两句。此时论文重心退为"语料 + bench 发布"，taxonomy 只留骨架。 |

### 2b. 双盲分支

若要求双盲，我需要另出一个匿名版：移除作者块、把仓库链接改为 `[repository URL withheld for review]`、
移除 arXiv 编号与"an earlier report of this study"的自引表述、检查致谢与 GenAI 披露里的可识别信息。
**注意**：匿名会削弱"artifacts 公开"这一优势（R3 上次专门表扬过），所以若为双盲，我会在正文里保留
"artifacts are public and will be linked in the camera-ready"的中性表述。

### 2c. 你的终审

无论哪个分支，定稿前请看四处：**Abstract、§I Introduction、§V-D 的 D1 六步、Fig. 1**。
把要改的地方告诉我（哪怕只是"这句话读着别扭"），我改完重编译再给你 PDF。

---

## 3. EasyChair 提交（**你来做，约 20 分钟**）

你已有 EasyChair 账号（ISSRE 2026 Submission 395 用的那个），流程与上次一致。

1. 打开 §1 记录的投稿链接（通常形如 `easychair.org/conferences?conf=qa4agents2026`）。
2. 若提示加入会议，选 **author** 角色。
3. `New Submission` → 填表：

**Title（粘贴）**
```
Silent Failures in a Production LLM Agent Runtime: A Labelled Incident Corpus and a Detection Benchmark
```

**Author**：Wei Wu ／ Independent Researcher ／ wuweinanonuaa@gmail.com ／ ORCID 0009-0009-1176-7817
（勾选 corresponding author；国家/地区按实际填）

**Keywords（每行一个）**
```
LLM agent systems
silent failures
fault taxonomy
benchmark
```

**Abstract — 完整版（216 词，若表单不限词数用这个）**
```
Conversational agent runtimes fail in a way that classical monitoring does not catch. When an internal error reaches a language model's context, the model does not stop. It writes a fluent, plausible, and wrong message to the user. We call this a fail-plausible failure.

We report an eight-week study of one production personal-assistant runtime. The runtime schedules about 40 jobs, routes across 8 LLM providers, and messages a human user daily. We recorded 22 silent-failure incidents with complete postmortems. We label each incident with a failure mechanism, a silence span, a discovery channel, and the artifact a human could have seen.

Three measurements over this corpus: a human reading the product discovered 13 of 22 incidents (59%); automated checks discovered 3 (14%); silence spans ranged from 36 minutes to 60 days. The corpus yields a five-class mechanism taxonomy. One class, chained hallucination, has no counterpart in the gray-failure literature.

We release the labelled corpus and a runnable benchmark for fail-plausible detectors. A deterministic reference detector flags 6 of 6 regression cases with 0 false positives on 4 clean cases, and misses 4 of 4 held-out cases. That last number is the point: pattern-based detection is a regression engine, not a prediction engine. The benchmark exists so other teams can measure that gap on their own systems.
```

**Abstract — 短版（149 词，若表单限 150 词用这个）**
```
Conversational agent runtimes fail in a way classical monitoring misses. When an internal error reaches a language model's context, the model does not stop; it writes a fluent, plausible, wrong message to the user. We call this a fail-plausible failure.

We report an eight-week study of one production personal-assistant runtime with about 40 scheduled jobs and 8 LLM providers. We documented 22 silent-failure incidents and labelled each with a failure mechanism, a silence span, and a discovery channel. A human reading the product discovered 13 of 22 incidents; automated checks discovered 3. Silence spans ranged from 36 minutes to 60 days. The corpus yields a five-class mechanism taxonomy. One class, chained hallucination, has no counterpart in the gray-failure literature.

We release the labelled corpus and a runnable benchmark for fail-plausible detectors. A deterministic reference detector flags 6 of 6 regression cases, 0 of 4 clean controls, and misses 4 of 4 held-out cases.
```

4. **上传 PDF**：`docs/paper/silent_failures_taxonomy/latex/qa4agents_workshop.pdf`
   （需要我重新生成就说一声；`.gitignore` 不收 PDF，仓库里只有 `.tex` 源码）
5. 若表单有 **"previously published / preprint"** 一栏：如实填 arXiv:2606.14589 是本工作早期版本的预印本。
   IEEE 允许预印本，但**要求披露**。瞒报比披露风险大得多。
6. 提交后 EasyChair 会发确认邮件并给一个 Submission 号 → **记下号码**。

### 提交后到截止前

EasyChair 允许在截止前反复 `Update file` 覆盖 PDF。所以策略是：**先占坑，再优化**——
表单填完先传当前版本拿到号码，之后若还想改，截止前重传即可。

---

## 4. 提交之后

- **通知日**：CFP 上会写（workshop 通常比主会短，多为 2–4 周）。记进日历。
- **这期间不用做任何事**。不要发邮件催 chairs。
- 与此同时：IEEE Software 的 SW-2026-06-0312 仍在审，两者互不冲突（不同论文、不同版本）。

---

## 5. 如果被录用（这是你还没走过的一段路）

顺序大致固定，每一步都有硬截止，漏一步论文就进不了 Xplore：

1. **Camera-ready 修订**：按审稿意见改，通常给 1–3 周。
2. **IEEE 版权表（eCF, electronic Copyright Form）**：在会议系统里在线签署。签之前确认你要的授权类型
   （标准 IEEE copyright / open access APC）。**开放获取要额外付费**，非必须。
3. **PDF 合规检查（IEEE PDF eXpress）**：会议会给一个 PDF eXpress 站点与会议 ID。上传你的 PDF，
   它会检查字体嵌入等 Xplore 兼容性，产出"Xplore-compatible"版本。**这一步常见坑：TikZ/矢量图字体
   未嵌入会被打回。** 我们的图是 TikZ 原生绘制、字体来自 IEEEtran 的 Times，通常没问题，
   但真被打回就把图另存为嵌入字体的 PDF 再 `\includegraphics`，告诉我我来做。
4. **作者注册**：至少一位作者按会议要求完成注册（见 §0）。**这是论文进 proceedings 的硬条件。**
5. **报告**：准备 workshop 报告（通常 10–20 分钟 + 问答）。到时我可以帮你做讲稿与幻灯片。
6. **上线**：会后数周至数月，论文出现在 IEEE Xplore。届时更新简历 / arXiv 页面的 journal-ref。

---

## 6. 如果被拒（先想好，比事后慌张好）

已经确认可用的备选，按性价比排序：

1. **ReSAISE 2026**（同为 ISSRE 卫星 workshop，同截止 8/19）——可作为本轮的第二志愿；
   注意不要**同时**投两个 workshop（一稿多投是学术不端），要投就是这个拒了再投下一个窗口。
2. **industry talk**（8/23）——不进 proceedings，换现场曝光。
3. **论文 #2《Mechanizing the Eye》**——已有初稿，结构上恰好回应了这次三审的批评
   （预注册防事后诸葛、sabotage 验证、公开 bench），可投下一个窗口。
4. **arXiv 更新**——本版本的改进（逐条语料表、显式 RQ、13/22 计数更正）本身就比已发表版好，
   即使不投任何会议，也值得作为 v2 更新到 arXiv:2606.14589。**这条无论录用与否都建议做。**

---

## 7. 一页速查：这次最容易踩的五个坑

| 坑 | 后果 | 防法 |
|---|---|---|
| 页数超限 | desk reject，不看内容 | §1 先查；裁剪顺序已在 `.tex` 头部写死 |
| 双盲却投了实名版 | desk reject | §1 先查；要匿名我半小时内出版本 |
| AoE 时区误判 | 晚一天 = 没投上 | 按**香港时间 8/20 20:00** 倒推，别卡最后一小时 |
| 预印本不披露 | 事后可能撤稿 | 表单里如实填 arXiv:2606.14589 |
| 录用后没注册/没到场 | 从 Xplore 撤下，白做 | §0 投之前就想清楚 |

---

*本 runbook 随论文一起版本化。§1 的三个空格填好后，这份文档就是可执行的；在那之前它是一份待办。*
