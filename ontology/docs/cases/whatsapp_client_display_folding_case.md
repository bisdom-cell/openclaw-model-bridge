# WhatsApp Client Display Folding 架构发现

> **日期**: 2026-05-07（V37.9.33 freight 推送实测发现）
> **版本**: V37.9.33 (触发) → **V37.9.35** (Path A 实施)
> **类型**: 架构层级发现（不是 bug，是 V37.9.21 设计假设的修正）
> **状态**: Path A 保守路径已实施

---

## TL;DR

**V37.9.21 设计假设**: WhatsApp 单消息 ≤ 1400 字符，超长内容必须手动分屏。

**V37.9.33 实测真相**: WhatsApp 协议层支持单条 65,536 字符，**WhatsApp 手机客户端在 ~4000-5000 字符自动分气泡显示**。OpenClaw 一次性发送 8131 字符，用户看到 2 个气泡（**时间戳完全相同 = 单条消息客户端折叠，不是协议层切片**）。

**V37.9.35 Path A 修正**: budget 1400 → 4000（保留手动分屏作为防御 fallback），影响 3 个 source + 3 个 test，信息密度立即提升 **2.86x**，跨平台一致性保留。

---

## 完整发现链路

### Step 1: V37.9.33 freight 三层 LLM 输出实测产生 8131 字符

V37.9.33 升级 LLM prompt 为三层结构化分析（📊 经济晴雨表 + 🏢 运营信号 + 🚢 商机条目），每层 5 条 → 输出长度从 V25 的 ~1500 字暴涨到 8131 字。

代码完全没有分屏逻辑：
```bash
"$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$(cat "$MSG_FILE")" --json
```
直接 `cat $MSG_FILE` 一次性传给 OpenClaw。

### Step 2: 用户观察到 WhatsApp 出现两个气泡

用户原话：「这次生成的内容比较多，WhatsApp是分两个窗口发送的」。

按字面理解，"两个窗口" = 两条独立消息。但用户进一步描述时间戳：
> "时间戳是相同的 都是'下午12:26'"

**关键证据 = 时间戳相同**：如果是协议层真切片（OpenClaw / Baileys 库分两次 send），两次 API 调用之间至少有 1 秒延迟，时间戳必然不同。**时间戳完全相同 = 单条消息**。

### Step 3: 系统化排查切片层

按原则 #28（不凭推测），排查 5 个候选切片层：

| 层 | 探测方法 | 结果 |
|---|---|---|
| OpenClaw `auth-profiles-*.js` 主 bundle | grep `splitMessage\|chunkMessage\|maxLength\|charLimit` | ❌ 无命中（line 42253 的 `DEFAULT_MAX_TEXT_LENGTH=4096` 是 TTS 模块常量，与 WhatsApp 无关）|
| OpenClaw `runtime-whatsapp-outbound.runtime-*.js` (lazy loader 真实实现) | grep `split\|chunk\|slice\|maxLength` | ❌ 无命中（文件最大 1179 bytes 仅 stub） |
| OpenClaw config `~/.openclaw/openclaw.json` | 看 channels.whatsapp 字段 | ❌ 无 maxTextLength 字段 |
| Baileys `@whiskeysockets/baileys 7.0.0-rc.9` 协议库 | 已知行为 | ❌ Baileys 不分片，单条上限 65,536 |
| WhatsApp 客户端显示层折叠 | 时间戳相同 + 跨气泡内容连贯 | ✅ **真正切片层** |

**结论**: WhatsApp 手机客户端在某个长度阈值（实测 ~4000-5000 字符）自动分气泡显示，但底层是单条消息（时间戳相同的根因）。

### Step 4: 验证字符数 vs 折叠阈值

```bash
$ wc -c ~/.openclaw/jobs/freight_watcher/cache/system_message_freight.txt
8131
```

8131 字符 ÷ 2 气泡 ≈ 4065 字符 / 气泡 → 客户端折叠阈值约 **4000-4500 字符**（与 Telegram 4096 字符限制接近，可能 WhatsApp UX 团队参考了同款阈值）。

---

## V37.9.21 设计假设的来源

V37.9.21 kb_deep_dive 手动分屏的 `_WA_BUDGET_PER_PART = 1400` 是历史经验值：
- 当时主要顾虑"WhatsApp 单窗口字符限制"（设计约束 1）
- 实际是**未实测过 4000+ 字符在 WhatsApp 客户端的表现**
- 假设变成了字面上的"WhatsApp 1400 字符限制"，实际是过度保守

V37.9.33 freight 的 8131 字符**意外触发实测**：客户端真的在 ~4000 字符折叠，但用户阅读体验良好（分气泡显示，内容完整）。这相当于 V37.9.21 设计假设被生产数据修正。

**这是原则 #13/#15 (用户视角观察 / 测试三层) 第 7+ 次正向兑现** — 单测/dev 验证不能替代真实生产数据。

---

## V37.9.35 Path A 实施

### 改动范围 (3 source + 3 test = 6 文件)

| 文件 | 行号 | 改动 |
|---|---|---|
| `kb_review_collect.py` | 518 | `body[:1400]` → `body[:4000]` |
| `kb_evening_collect.py` | 182 | `body[:1400]` → `body[:4000]` |
| `kb_deep_dive.py` | 596, 601 | `_WA_BUDGET_PER_PART = 1400` → `4000` / `_WA_BODY_BUDGET_PER_PART = 1200` → `3800` |
| `test_kb_review.py` | 587 | `assertLessEqual(body_part, 1400)` → `4000` |
| `test_kb_evening.py` | 262-268 | `test_body_truncated_at_1400` → `_at_4000`，`x*5000` → `x*8000`，1500 → 4000 buffer |
| `test_kb_deep_dive.py` | 410, 514, 556, 699 | 4 处 budget assertion + 输入长度调整以触发新 budget 的 multi-part 切片 |

### 保留的防御 fallback

V37.9.21 手动分屏代码**完全保留**：
- `_split_text_into_chunks(text, max_chunk)` 纯函数
- `build_deep_dive_wa_parts(...) -> list[str]` 多段构造
- `kb_deep_dive.sh` mktemp + for chunk_file 循环 + sleep 1 防乱序
- `kb_dream.sh` line 1263-1290 max_chunk=4000 切片

新 budget 4000 在大多数场景**单段足够**（如 freight 8131 自动折叠工作良好）。手动分屏只在 **极长内容 (>8000 字)** 触发，作为：
1. **跨平台兼容性保险** — WhatsApp Web / Desktop / 老版本可能折叠阈值不同
2. **极长内容防御** — 阅读体验下限保证
3. **MR-7 元规则正向兑现** — 治理系统不删除已验证的安全边界

### 不变的设计

- WhatsApp/Discord 双通道分离 (Discord 单条 1900 字单气泡，UX 不同)
- 段间 `sleep 1` 防消息乱序
- KB 归档完整内容（不受 WhatsApp budget 影响）

---

## 跨任务影响矩阵

| 任务 | V37.9.35 前 | V37.9.35 后 | 信息密度 |
|---|---|---|---|
| `kb_review` 周回顾 | 截到 1400 字 | 截到 4000 字 | 2.86x |
| `kb_evening` 晚间整理 | 截到 1400 字 | 截到 4000 字 | 2.86x |
| `kb_deep_dive` 每日深度分析 | 1400 字单段 (V37.9.21 多段切片) | 4000 字单段 (>8000 字才切多段) | 2.86x + 多数情况一气泡 |
| `freight` 货代速报 | 无 budget (V37.9.33) | 无 budget (V37.9.33) | 不变 |
| `kb_dream` 跨域做梦 | max_chunk=4000 | max_chunk=4000 | 不变（已合理）|
| `arxiv` / `hf_papers` / `dblp` 论文监控 | 无截断 | 无截断 | 不变 |

V37.9.35 Path A 实际只影响 **3 个 task** (kb_review / kb_evening / kb_deep_dive)，其他 task 没有 1400 字截断设计。这进一步证明 1400 字 budget 是 V37.9.21 单点假设而非全局设计。

---

## 元教训

### 1. 字符数限制要看真实生产数据，不是 documented limit

V37.9.21 当时未实测就采用 1400 字"安全值"，错过 WhatsApp 客户端折叠 UX feature。

**改进**: 任何 UI/UX 约束类参数（字符数、消息长度、超时等）必须有**生产实测数据**支撑。文档（包括 official docs）可能落后于实际客户端行为。

### 2. 用户视角观察是不可替代的最终验证层

- V37.9.31: 修复破坏 caller 契约（用户视角发现）
- V37.9.32: import API 错配（用户视角发现）
- V37.9.34: bash 字符串嵌套（用户视角发现）
- **V37.9.35: WhatsApp 客户端折叠假设（用户视角发现）**

四次同源教训：dev 单测/治理/preflight 全过 ≠ 生产 UX 良好。原则 #13/#15 是硬规则。

### 3. 保守路径（Path A）vs 激进路径（Path B/C）

Path A 保留手动分屏作为防御 fallback，付出 ~150 行代码维护成本，换得：
- 跨平台一致性保证（不同 WhatsApp 客户端折叠阈值可能不同）
- 极长内容（>8000 字）阅读体验保底
- MR-7 治理元规则正向兑现（不删除已验证的安全边界）

**对应原则**: 不删除已经工作的防御代码 (V37.9.21 手动分屏)，即便上层假设修正后部分失效。

### 4. budget 提升的边际效用 > 代码删除

Path A vs Path C (全删手动分屏)：
- Path A: +6 文件改 / +200 行测试调整 / 信息密度 2.86x
- Path C: -200 行代码删 / 信息密度 5.7x / 但跨平台风险

V37.9.35 选 Path A 因为**信息密度提升的边际效用 (2.86x) 已经足够**，再激进 (5.7x) 边际效用递减但风险递增不划算。

---

## 下次 V37.9.36+ 候选

1. **24-48h V37.9.35 实测**: kb_review (周日晚) / kb_evening (每日 22:00) / kb_deep_dive (每日 22:30) 推送在 4000 字 budget 下是否产生更深度内容
2. **跨平台一致性测试**: 用户在 WhatsApp Web / Desktop / 旧版手机客户端验证 4000+ 字符显示行为
3. **如跨平台都一致** → V37.9.36+ 评估 Path B (移除 kb_deep_dive 手动分屏)
4. **如某平台异常** → 维持 Path A，并把异常平台行为登记到本案例文档

---

## 相关原则

- 原则 #13 (定期像用户用) — 第 7 次正向兑现
- 原则 #15 (测试三层 — 单测/preflight/WhatsApp 业务验证) — WhatsApp 实测是不可替代的最终层
- 原则 #18 (补证据而非补功能) — 不删除已工作的防御代码
- 原则 #28 (理解再动手) — 三问全过：之前存在吗 (V37.9.21 1400 假设错) / 哪个改动引入 (V37.9.21 没实测) / 最小修复 (Path A 提升 budget 不删代码)
- MR-7 (治理系统不删除已验证的安全边界)

---

## 与其他案例的关系

- **V37.9.21 kb_deep_dive 手动分屏**: 引入此假设的原始设计，被 V37.9.35 数据修正
- **V37.9.33 freight 三层 LLM 输出**: 触发本发现的 8131 字符产物
- **V37.9.34 bash double-quote hotfix**: 同日 V37.9.33 部署的另一个 silent failure 修复（一日 5 个版本最高产出）
- **未来 V37.9.36+**: 一周观察期决定是否进 Path B

V37.9.x 系列至此累计 6 个版本（V37.9.30~35）+ 1 hotfix，全部围绕"数据驱动诊断方法论"的不同维度展开。V37.9.35 是"用户 UX 实测推翻设计假设"的方法论加固。
