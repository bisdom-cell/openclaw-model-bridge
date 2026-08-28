# arXiv 提交手把手指南 — Mechanizing the User's Eye（论文 #2）

> 2026-08-28 V37.9.333。四步流程 + arXiv 表单字段可复制版，镜像论文 #1 的 `ARXIV_SUBMISSION_GUIDE.md`（2026-06-11 已走通一遍的同款流程）。
> 遇到任何报错/卡点：把屏幕上的错误文字复制贴给 Claude。
>
> 决策依据（2026-08-28 用户定夺，详见 `DECISIONS_NEEDED.md` 头部）：现在投安静窗版本 / 标题选项 1 / arXiv 直发 cs.SE + cross-list cs.AI / 署名沿用论文 #1。

---

## 第 1 步：引用终核（Mac Mini 终端 + 浏览器，3 分钟）

开发容器出站网络不可达 arxiv.org（策略限制），这一步只能在您那边做——和论文 #1 完全同款。

Mac Mini 终端粘贴执行：

```
curl -s "http://export.arxiv.org/api/query?id_list=2606.14589,2503.13657,2306.05685,2311.05232,2404.13076,2302.03649,2310.10501" | grep -E "<title>|<name>"
```

输出会是 7 组「`<title>` 论文标题 + 一串 `<name>` 作者名」。**把完整输出复制贴给 Claude**，由 Claude 对照 `latex/main.tex` 文末 References（[1] 是论文 #1 自引，其余 6 条带 arXiv ID），不一致就改。您不需要自己对照。

如果 curl 无输出（网络波动），浏览器逐条打开也行，每条只看标题和作者是否与 PDF References 一致：

```
https://arxiv.org/abs/2503.13657
https://arxiv.org/abs/2306.05685
https://arxiv.org/abs/2311.05232
https://arxiv.org/abs/2404.13076
https://arxiv.org/abs/2302.03649
https://arxiv.org/abs/2310.10501
```

---

## 第 2 步：作者信息核对（30 秒）

论文 #2 的 `latex/main.tex` **作者信息已按论文 #1 先例填好**（实名 + 邮箱 + Independent researcher + 标题页脚注 AI disclosure），不需要像论文 #1 那样填占位符。打开 PDF 首页扫一眼姓名拼写即可；要改机构行或措辞，直接在对话里告诉 Claude。

---

## 第 3 步：Overleaf 编译验证（10 分钟）

本地 pdflatex 已真编译通过（18 页 / 0 error / 0 undefined ref / 0 overfull box），这一步是投稿前在您可控环境里再确认一遍。

**3.1** Mac Mini 终端打包（本论文只有一个 .tex 文件）：

```
cd ~/openclaw-model-bridge/docs/paper/mechanizing_the_eye/latex
```

```
zip paper2_latex.zip main.tex
```

**3.2** 浏览器打开 overleaf.com → 登录（论文 #1 时注册的账号）。

**3.3** New Project → Upload Project → 选 `paper2_latex.zip`。

**3.4** 左上角 Menu 确认两项：Compiler = **pdfLaTeX**；Main document = **main.tex**。

**3.5** 点 Recompile。第一遍交叉引用显示 `??`（正常）——**再点一次 Recompile** 变成正确编号。

**3.6** 检查 PDF（对照下面清单）：

- 共 18 页；4 张图渲染正常：Fig.1 两层管道（p.6）/ Fig.2 预注册三次冻结链（p.10）/ Fig.3 部署时间线（p.11，刻度标签上下两行错开、不重叠）/ Fig.4 O1-O8 taxonomy 映射（p.13，图例在整图正下方、不压 O8 框）
- Table 1-6 不超页边（Table 1 信号表和 Table 5 O1-O8 表是小字号，正常）
- 引用编号 [1]-[11] 正常；标题页脚注（AI disclosure）完整
- 末尾 Postscript 段在 Artifact Availability 之前，标注 "added 2026-08-28"

**3.7** 有红色报错 → 复制完整错误文字贴给 Claude。编译成功 → Menu → Download → Source 下载最终 zip（第 4 步上传用这份）。

---

## 第 4 步：arXiv 提交（15 分钟 + 等待 announcement）

**4.1** arxiv.org 登录（论文 #1 的账号，已有发文记录，cs.SE 不会再触发 endorsement）→ **START NEW SUBMISSION**。

**4.2 License**：**CC BY 4.0**（论文 #1 同款）。

**4.3 分类**：Primary = **cs.SE** (Software Engineering)；Cross-list = **cs.AI**。（本篇不加 cs.DC——内容重心是可观测性方法学而非分布式系统，与论文 #1 的取舍不同是刻意的。）

**4.4 上传文件**：上传 Overleaf 下载的 zip（或单个 main.tex）。arXiv 自动编译后查看它生成的 PDF 预览，确认与 Overleaf 一致。

**4.5 元数据表单**（以下可直接复制）：

Title:
```
Mechanizing the User's Eye: Pre-Registered Deployment of a Sabotage-Validated Fail-Plausible Observer in a Production LLM Agent Runtime
```

Authors: 您的姓名（arXiv 格式 First Last，与论文 #1 一致）。

Abstract（已去 LaTeX 化的纯文本版）:
```
In a previous longitudinal study of silent failures in a production LLM agent runtime (When Errors Become Narratives, arXiv:2606.14589) we reported an uncomfortable finding: roughly 70% of silent failures were ultimately discovered by a human looking at the product as a user, while thousands of unit tests and hundreds of governance checks stayed green -- and we posed mechanizing even part of what the human eye does as an open problem. This paper reports our attempt. We built an automated user-viewpoint observer targeting the taxonomy's most dangerous class, fail-plausible failure, in which the system transforms an internal error into fluent, plausible output delivered to the user. The observer is a two-layer pipeline: five deterministic signals distilled from incident postmortems escalate to an LLM judge whose verdicts must cite verbatim evidence from the artifact or be discarded. Ground truth comes from 24 labeled production postmortems, with explicit honesty boundaries -- 16 of 24 incidents are structurally invisible to any content-reading observer, and we say so rather than claim them. Offline, the deterministic layer achieves 6/6 regression detection with 0/4 false positives, every detector proven load-bearing by sabotage; held-out recall on novel patterns is 0/4. The observer is, so far, a regression engine, and the scorecard reports that without spin. Deployment followed a protocol borrowed from experimental science: pre-registration. Shadow mode caught and retired one systematic false positive on its first production run, after which the registered 26-day shadow window ran clean; flip criteria were registered before the shadow data was read; the analysis protocol -- regime rules, exclusion rules, a negative-results commitment -- was frozen before the enforcing-mode window opened. That window (12 observed days) fired zero verdicts: per the pre-registered path we report live precision as undefined (zero denominator) rather than narrating quiet as success. Meanwhile the observer produced eight silent failures of its own during its development -- including polluting its own evidence file and an enforcing-mode integration that was silently inert -- empirically confirming the prior paper's warning that the judge inherits the taxonomy it judges. We release the labeled corpus, detector, and scorecard as a community-runnable bench, and argue that what mechanization buys today is retiring the human's regression scanning so the human eye can specialize in novelty -- prediction remains open, but it is now measurable, under rules already frozen.
```

Comments（建议填）:
```
Follow-up to arXiv:2606.14589. 18 pages, 4 figures. Labeled incident corpus, detector source, sabotage-validation harness, and pre-registration texts publicly available at https://github.com/bisdom-cell/openclaw-model-bridge
```

**4.6 提交**：Preview 确认 → Submit。工作日 14:00 (ET) 前提交通常次日 announcement。

**4.7 上线后回来告诉 Claude arXiv ID**，Claude 做发布配套：README/status.json/CLAUDE.md 链接更新 + design doc Stage 表收敛 + data_inventory 终版对表归档 +（可选）中文科普版。

---

## 卡点速查

| 症状 | 处理 |
|---|---|
| Overleaf 编译红色报错 | 复制完整错误文字贴给 Claude |
| arXiv 编译失败但 Overleaf 成功 | 把 arXiv 的 log 尾部贴给 Claude |
| 引用终核发现标题/作者不一致 | 贴输出给 Claude，改 main.tex 后重走第 3 步 |
| 表单某字段不确定 | 截图或描述贴给 Claude |
