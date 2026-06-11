# arXiv 提交手把手指南 — Silent Failures Taxonomy 论文

> 2026-06-11 V37.9.139。四步流程 + arXiv 表单字段可复制版。
> 遇到任何报错/卡点：把屏幕上的错误文字复制贴给 Claude。

---

## 第 1 步：引用终核（Mac Mini 终端，2 分钟）

打开 Mac Mini 终端，粘贴执行：

```
curl -s "http://export.arxiv.org/api/query?id_list=2511.07424,2508.07935,2602.11749,2603.05637,2606.05339,2508.14231" | grep -E "<title>|<name>"
```

输出会是 6 组「`<title>` 论文标题 + 一串 `<name>` 作者名」。**把完整输出复制贴给 Claude**，由 Claude 对照 `main.tex` 文末 References 第 4/5/6/7/9/10 条，不一致就改。您不需要自己对照。

---

## 第 2 步：填作者姓名 + 邮箱（1 分钟）

`latex/main.tex` 有两个占位符：`[AUTHOR NAME]`（第 52 行）和 `[email]`（同段脚注内）。另外 `\small Independent researcher` 是单位行，可改成您的机构或保留。

**最省事的方式**：在对话里直接告诉 Claude「作者名：XXX，邮箱：XXX」，Claude 改好提交，您合并即可。（姓名邮箱本来就会公开印在 arXiv 论文上，不属于敏感泄漏。）

自己改也行，Mac Mini 终端：

```
nano ~/openclaw-model-bridge/docs/paper/silent_failures_taxonomy/latex/main.tex
```

nano 内操作：Ctrl+W 搜索 `AUTHOR NAME` → 改两处 → Ctrl+O 回车保存 → Ctrl+X 退出。改完需要 commit + push（或告诉 Claude 远端同步）。

---

## 第 3 步：Overleaf 编译验证（10 分钟）

**3.1** 在 Mac Mini 终端打包 LaTeX 工程：

```
cd ~/openclaw-model-bridge/docs/paper/silent_failures_taxonomy/latex
```

```
zip paper_latex.zip *.tex
```

**3.2** 浏览器打开 overleaf.com → 注册/登录（免费账号够用）。

**3.3** 首页点 **New Project → Upload Project** → 选刚才的 `paper_latex.zip`。

**3.4** 项目打开后，左上角 **Menu** 确认两项：Compiler = **pdfLaTeX**；Main document = **main.tex**。

**3.5** 点 **Recompile**。第一遍编译完，交叉引用会显示 `??`（正常）—— **再点一次 Recompile**，`??` 会变成正确编号。

**3.6** 检查 PDF：
- 5 张图渲染正常（Fig.1 三平面 / Fig.2 分类树 / Fig.3 幻觉链 / Fig.4 潜伏期 / Fig.5 三步跃迁）
- Table 1 / Table 2 不超出页边
- 引用编号 [1]-[14] 正常
- 标题页脚注（AI disclosure）完整

**3.7** 如有红色报错：点报错条目展开 → 复制完整错误文字 → 贴给 Claude 修。
编译成功：**Menu → Download → Source** 下载最终 zip（第 4 步上传用这份）。

---

## 第 4 步：arXiv 提交（20 分钟 + 等待 announcement）

**4.1 注册**：arxiv.org → Login → Create account。

> ⚠️ **Endorsement 提示**：arXiv 对新账号在部分分类（含 cs.SE）可能要求一位已有 arXiv 发文记录的研究者背书（endorsement）。提交时若出现该提示，系统会给一个 endorsement code —— 可找有 arXiv 记录的同行/导师输入该 code 完成背书。用学术机构邮箱注册可降低触发概率。遇到卡点贴给 Claude 一起想办法。

**4.2 开始提交**：登录后 **START NEW SUBMISSION**。

**4.3 License**：推荐 **CC BY 4.0**（最利传播引用；保守可选 arXiv 默认 non-exclusive license）。

**4.4 分类**：Primary = **cs.SE** (Software Engineering)；Cross-list = **cs.AI** + **cs.DC**。

**4.5 上传文件**：上传 Overleaf 下载的 zip（或本地 9 个 .tex）。arXiv 自动编译 —— 它和 Overleaf 同为 TeXLive，Overleaf 能过这里基本能过。查看 arXiv 生成的 PDF 预览确认无误。

**4.6 元数据表单**（以下可直接复制）：

Title:
```
When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime
```

Authors: 您的姓名（按 arXiv 格式 First Last）。

Abstract（已去 LaTeX 化的纯文本版）:
```
Large language model (LLM) agent systems are increasingly deployed as long-running, autonomous runtimes -- orchestrating scheduled jobs, calling tools, maintaining memory, and pushing results to humans over messaging channels. We present a longitudinal empirical study of silent failures in one such system: a personal-assistant agent runtime in continuous production since March 2026, comprising roughly 40 scheduled jobs, 8 LLM providers, a tool-governance proxy, and a knowledge-base memory plane, defended by 4,286 unit tests and 827 declarative governance checks. Over an eight-week window we documented 22 incidents with full root-cause postmortems, within which a single meta-pattern -- a failure whose error signal never reaches a human in actionable form -- manifested at least 28 times. From these postmortems we derive a five-class, mechanism-oriented taxonomy of silent failure: (A) environment and platform quirks, (B) design-assumption mismatches, (C) error swallowing and dilution, (D) chained hallucination and fabrication, and (E) operational omission and forensic blind spots. Class D is, to our knowledge, specific to LLM-based systems and the most dangerous: the system does not merely fail to report an error -- the LLM actively transforms the error into fluent, plausible narrative content delivered to the user. We term this behavior fail-plausible, and position it as the LLM-era escalation of gray failure's differential observability: the observer is not just blind, it is being convincingly lied to by the failure itself. Three cross-cutting findings challenge common assumptions about agent reliability engineering: roughly 70% of silent failures were ultimately caught by human user-view observation rather than by tests or audits; a retrospective audit of 15 incidents found a 0% ex-ante prevention rate but an 87% ex-post regression-blocking rate -- audits are regression engines, not prediction engines; and incident latency (13 hours to 60 days) correlates with failure mechanism, not code complexity -- the longest-lived failures lived in the seams between components, where no test runs. We describe the defense framework that emerged (meta-rules, mechanized scanners, sabotage-validated invariants, a declared-state convergence engine, and layered anti-fabrication guards), and distill design principles for engineering LLM agent systems whose failures are loud, attributable, and boring. All 22 postmortems, the governance engine, and the defense framework are publicly available.
```

Comments（可选，建议填）:
```
22 incident postmortems and all defense-framework artifacts publicly available at https://github.com/bisdom-cell/openclaw-model-bridge; governance engine on PyPI (openclaw-ontology-engine)
```

**4.7 提交**：Preview 确认 → Submit。工作日 14:00 (ET) 前提交通常次日 announcement。上线后拿到 arXiv ID（如 2606.XXXXX）。

**4.8 上线后回来告诉 Claude arXiv ID**，Claude 做发布配套：README/status.json 链接更新 + 中文科普版（知乎传播层）+ data_inventory 终版对表归档。

---

## 卡点速查

| 症状 | 处理 |
|---|---|
| Overleaf 编译红色报错 | 复制完整错误文字贴给 Claude |
| arXiv 要求 endorsement | 找有 arXiv 发文记录的同行输入 code；或贴给 Claude 想办法 |
| arXiv 编译失败但 Overleaf 成功 | 把 arXiv 的 log 尾部贴给 Claude |
| 引用终核发现作者不一致 | 贴输出给 Claude，改 main.tex 后重新走第 3 步 |
