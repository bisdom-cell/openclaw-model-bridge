# Baileys 封禁风险评估与缓解方案（V37.9.162 后续战略评估）

> 触发：2026-06-16 WhatsApp session logout 静默 7h 血案（见 `ontology/docs/cases/whatsapp_session_logout_silent_7h_case.md`）。该血案的**根因层**是 Baileys（非官方 WhatsApp Web 客户端）凌晨 6h 重连风暴触发 WhatsApp 账号级反滥用。V37.9.162 修了"检测"（频道掉线 7h→1h 告警），本文评估"预防"（让重连风暴不发生 = 账号不被限流）+ 是否需要换方案。
>
> **本文是决策文档，culminate 在用户拍板。结论需用户决定，不单方实施。**

## 🔴 2026-06-18 Mac Mini 实测结论（决定性 — 覆盖前述 Option A 推断）

WhatsApp 408 当日恢复（手机正常 + 单次扫码重链成功，`channels status` 显示 `linked, connected`），随即实测 Option A 落地，**确认 Option A 在 4.27 无法落地**：

1. **手动改 openclaw.json 加 `channels.whatsapp.web.reconnect.maxAttempts`** → 运行中 Gateway canonicalize 时**直接剥掉**（事后 `grep -c maxAttempts openclaw.json` = 0；whatsapp 只剩 7 个 schema 键 `[enabled, dmPolicy, selfChatMode, allowFrom, groupPolicy, debounceMs, mediaMaxMb]`，无 `web` 块）。
2. **`openclaw config set channels.whatsapp.web.reconnect.maxAttempts 12`** → 硬报错 **`Config validation failed: channels.whatsapp: invalid config: must NOT have additional properties`**。

→ 4.27 的 `channels.whatsapp` 是 **`additionalProperties: false` 严格 schema**，根本不接受 `web.reconnect` 键（doc 文档示例的 key 路径在 4.27 运行时 schema 不存在）。**Option A（config 限制重连）在 4.27 确认无效**，须等 OpenClaw 暴露 reconnect 配置的版本（issue #56365，仍 open）+ 按 tripwire 纪律升级才能启用。

**当前主防线 = Option C（V37.9.162 检测 + 恢复 SOP）。残余封禁风险 = 系统一直以来的基线**（6/16 前长期跑 `maxAttempts:0`，是那次 428 issue #1625 的 24h auth 超时级联才引爆，非常态）。**升级路径不变**：OpenClaw 出暴露 reconnect 配置的版本 → Option A 重新可行。

**附带发现（V37.9.180 已修）**：4.27 CLI 冷调用 10s 超时 quirk（V37.9.156）在 notify **发送路径**也触发 —— WhatsApp cron 推送间隔数小时、每次都"冷"→ CLI 等 ack 10s 放弃报 `gateway timeout after 10000ms`，但 **gateway 已投递** → notify 误当失败重试 3 次 → **用户收到 3 条重复**（2026-06-18 实测）。V37.9.180 给 notify.sh 加 `_notify_is_coldcall_timeout`（发送 + 重放两路径）：匹配该签名按已投递处理（不重试 / 不入队 / 不重复），WhatsApp cron 推送恢复单条。

## 一、根因机制（为什么重连 → 封禁）

Baileys 按 `DisconnectReason` 分类断开原因决定是否重连：

| 代码 | 含义 | 正确处理 | 今天发生了什么 |
|------|------|----------|----------------|
| **401 loggedOut** | 服务端登出 | **绝不重连**（重连=找封） | 08:34 出现，channel exited |
| 428 connectionClosed | 连接关闭 | 退避重连 | 01:59 出现（**疑似已知 24h 多文件 auth 超时 issue #1625**） |
| 408 timedOut / 499 | 超时/客户端关闭 | 退避重连 | 凌晨反复 |
| 503 unavailableService | 服务不可用 | 退避重连 | 05:21 出现 |
| 515 restartRequired | 需重启 | 立即重连 | — |

**血案链条**：01:59 的 428（可能是 Baileys 已知的"多文件 auth 状态 ~24h 后 428 超时"bug，issue #1625）→ Baileys 判为"可恢复"→ **每次断开 `Retry 1/12`（每轮最多 12 次）**→ 6 小时持续重连风暴 → WhatsApp ML 反滥用模型识别"机器人式时序 + 异常连接模式"→ 08:34 升级为 **401 loggedOut（账号级登出）**。

**核心问题**：Baileys 的"可恢复就重连"策略**没有全局熔断**——它不会因为"过去 1 小时已失败 N 次"而停止，于是 428/503 的反复触发演变成持续风暴，最终把"临时连接问题"升级成"账号级封禁"。

## 二、风险量化（有多严重）

- **Baileys / WAHA / Evolution API / whatsmeow 等逆向协议工具普遍 2–8 周封禁时间线**（kraya-ai 2026 ban-risk 研究）。
- **68% 使用非官方自动化工具的企业 12 个月内至少被封一次**（援引 Meta 2025 Policy Enforcement Report）。
- WhatsApp ML 反滥用模型 2025-2026 重点权重：回复率（<10% 高危）、联系人图距离（陌生人高危）、**时序模式（机器人式时序高危）**、**异常连接模式（重连风暴高危）**。
- **今天是临时限流（手机重启 + 等待自愈），但下一次可能升级为更长甚至永久封禁**——反复失败的重连/登录尝试是 ML 模型的明确高危信号。
- ⚠️ **安全警告**：不要用第三方"anti-ban"npm 包。2026-04 `lotusbail`（56000 下载的"anti-ban"包）被确认是**恶意软件，窃取 session 凭据 + WhatsApp 消息**。任何"防封"第三方包都要极度警惕。

## 三、三个选项

### 选项 A：降低 Baileys 重连激进度（保留现有 linked-device 模型）

**做法**：让重连策略更保守 + 加全局熔断：
1. **401 loggedOut 绝不自动重连**（现在似乎在登出后仍有连接尝试 → 喂养封禁）。
2. **退避升级**：当前 `Retry 1/12` 偏激进；改指数退避（5s→15s→60s→5min）+ **全局熔断**（如 1 小时内累计失败 ≥ N 次 → 停止重连，等更长冷却 + 推 Discord 告警让人工介入），避免 6h 持续风暴。
3. **428 已知 24h 超时**：若确认是 issue #1625，可能 OpenClaw 升级或 auth 刷新策略能缓解。

**成本**：低（配置调整，无需改代码）。**收益**：显著降低（非消除）封禁风险，保留"PA 用你自己 WhatsApp 号、linked-device、零额外号码"的便利模型。

**✅ 可配性核实结果（2026-06-16 Mac Mini 实测,修正前述 WebSearch 文档推断 — 血案 #98 兑现）**：

- **4.27 实际结构**:WhatsApp 配置在 `channels.whatsapp.{enabled/dmPolicy/selfChatMode/groupPolicy/debounceMs/mediaMaxMb}`,**默认无 reconnect 块**(`web.reconnect`/`maxAttempts`/`keepAlive` 一个都没显式写)→ 用内置默认 `maxAttempts=0`(无限重连)= 根因默认就激活。
- **`web.reconnect.maxAttempts`(默认 0)是 OpenClaw 文档化配置项**(docs.openclaw.ai/gateway/config-channels),但默认不写入 openclaw.json。`web` = WhatsApp **Web**(Baileys 协议)层,推断路径 = `channels.whatsapp.web.reconnect.maxAttempts`(需 4.27 实测确认)。
- **OpenClaw 不会加自动熔断**:issue #16270「加熔断防重连触发封禁」2026-02-14 开 → **已 closed as NOT PLANNED**(拒绝自动"10 连续失败→30min 退避")。官方立场 = 暴露 `maxAttempts` 让用户自己配("Protecting your WhatsApp account"段)。

**根因精确定位**:`maxAttempts: 0`(无限)= 断开→`Retry 1/12`→断开→`Retry 1/12` 永续 → 6h 风暴 → 08:34 封禁。`Retry 1/12` 是每次断开的 per-drop 上限(Baileys 内部 12),`maxAttempts: 0` 让**整体会话级重连无上限**,所以风暴跑满 6h 直到 WhatsApp 自己 401 登出(=封禁)才停。

**修复 = 手动新增 `channels.whatsapp.web.reconnect.maxAttempts` 有限值**(这是 OpenClaw 官方认可的唯一路径,因为自动熔断 NOT PLANNED)。**推荐值 ~10-15**:够扛瞬时断开 1-2 次自愈,又把 6h 风暴上限砍到"N 次(~10-20min)后停止 + 告警"。比激进的 5-8 更稳(夜间瞬断不易误停);比无限(现状)安全得多(6h→~15min,远不到封禁阈值)。

**🔴 时序铁规则(关键)**:**配置改 + Gateway 重启必须等 WhatsApp 重链稳定后做**。Gateway 重启会触发一次新的 Baileys 连接 — **当前限流未清时重启 = 又一次连接尝试 = 可能重新触发/延长限流**(正是凌晨风暴成因)。所以顺序:① 先 WhatsApp 恢复(等冷却→扫码→稳定)→ ② 再改 config + 重启。

**应用步骤(WhatsApp 稳定后)**:精确 ready-to-apply 包见下方「Option A ready-to-apply 包」(2026-06-17 dev 侧 schema 实证 + Python 原子补丁,取代此前粗略估计)。

**A 的现实约束(诚实登记)**:能否真生效取决于 4.27 是否读 `channels.whatsapp.web.reconnect.maxAttempts`。文档描述它但我们 4.27 实例默认没这块 → 加上去**要么生效(理想)要么被忽略(无害但 A 失效)**。若被忽略 → A 真正落地需等 OpenClaw 出"暴露 reconnect 配置"的版本(#56365 "expose Baileys socket timing" 仍是 open request)。届时按项目 tripwire 升级纪律评估升级。**在 A 确认生效前,C(V37.9.162 检测 + 恢复 SOP)是事实上的主防线。**

**A + C 协同**:有限 `maxAttempts`(断风暴防封禁)+ V37.9.162 频道掉线检测(停止后 1h 内 Discord 告警)= 既不被封、又不静默。代价:服务端持续拒绝时 N 次后停止 → WhatsApp 下线到人工重链(但"2 分钟重链" vs "2-8 周封禁"是好交易,且 1h 内告警)。

### Option A ready-to-apply 包（2026-06-17 dev 侧核实 — WebSearch + WebFetch schema 实证）

> 用户 2026-06-17 拍板走 Option A。本包是"WhatsApp 恢复后一步到位"的精确应用方案。**恢复未完成前不应用**（铁规则见下）。

**Schema 实证确认**（docs.openclaw.ai/gateway/config-channels + GitHub 源，2026-06-17）：

| 项 | 实证结果 |
|----|----------|
| 完整路径 | `channels.whatsapp.web.reconnect.maxAttempts` |
| `web.reconnect` 默认块 | `{initialMs:2000, maxMs:120000, factor:1.4, jitter:0.2, maxAttempts:0}` |
| `maxAttempts:0` 语义 | **= 无限重连（确认，根因坐实）**；issue #16270 官方立场 = 暴露此键让用户自配 |
| `web.whatsapp` 块 | `{keepAliveIntervalMs:25000, connectTimeoutMs:60000, defaultQueryTimeoutMs:60000}` |
| 退避数学（maxAttempts:12） | 2+2.8+3.9+5.5+7.7+10.7+15+21+30+41+58+81 ≈ **4.6 min 后停止** → 6h 风暴砍到 ~5min（vs 设 50 = ~1.3h）|

**为何 12**（settled，取代正文 5-8 估计）：12 次退避≈4.6min 后停止 → 防封够快（远不到 6h 风暴的封禁阈值），又容 1-2 次夜间瞬断不误停。误停后 V37.9.162 检测 1h 内 Discord 告警 + 人工 2min 重链。若日后误停过频 → 调 15-20；若封禁复发 → 调更低。

**🔴 前置条件（铁规则，不可跳）**：WhatsApp 必须已扫码恢复 + 稳定运行 ≥ 几分钟，才能跑下面任何一步。改 config + 重启 Gateway = 一次新 Baileys 连接，**限流未清时做 = 重新触发/延长限流**（正是凌晨风暴成因）。

**Step 0 — 备份（恢复后才跑）**

```
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak
```

**Step 1 — 安全打补丁（Python 原子写，preserve 现有值 + 缺则填 sane 默认 + 强制 maxAttempts=12）**

```
python3 - <<'PYEOF'
import json, os
p = os.path.expanduser("~/.openclaw/openclaw.json")
with open(p, encoding="utf-8") as f:
    cfg = json.load(f)
rc = cfg.setdefault("channels", {}).setdefault("whatsapp", {}).setdefault("web", {}).setdefault("reconnect", {})
old = rc.get("maxAttempts", "<absent>")
rc.setdefault("initialMs", 2000)
rc.setdefault("maxMs", 120000)
rc.setdefault("factor", 1.4)
rc.setdefault("jitter", 0.2)
rc["maxAttempts"] = 12
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.replace(tmp, p)
print("OK reconnect.maxAttempts:", old, "-> 12")
PYEOF
```

**Step 2 — 验证文件（确认 maxAttempts:12 + JSON 仍有效）**

```
python3 -m json.tool ~/.openclaw/openclaw.json | grep -inE "maxAttempts|reconnect|\"web\""
```

应看到 `"maxAttempts": 12`。若 json.tool 报错 = JSON 损坏 → 跑回滚。

**Step 3 — 重启 Gateway**

```
bash ~/restart.sh
```

**Step 4 — 验证 Gateway + WhatsApp 健康**

```
openclaw channels status
```

WhatsApp 行仍 `connected` = 键被接受、频道未被打断。

**Step 5 — 效力确认（4.27 是否真读该键）**：终极证明在下一次重连事件 — Gateway 日志应显示重连在 ~12 次（~5min）后停止而非 6h；且 V37.9.162 监控会在频道掉线 1h 内 Discord #alerts 告警。当前可确认两层：(a) Step 3 Gateway 干净启动 = 键未被 strict-reject (b) Step 2 re-dump 显示已持久化。

**🔁 回滚分支**：任一步异常（Gateway 起不来 = strict schema 拒绝未知键 / WhatsApp 断链 / re-dump 无该键）→ 立即还原：

```
cp ~/.openclaw/openclaw.json.bak ~/.openclaw/openclaw.json && bash ~/restart.sh
```

若被静默接受但无效（下次仍 6h 风暴）→ 确认 4.27 不读该键，A 失效，等 #56365 暴露配置的版本（按 tripwire 升级纪律），C（V37.9.162 检测）仍是主防线。

### 选项 B：迁移到官方 WhatsApp Business Cloud API

**做法**：弃用 Baileys，改用 Meta 官方 Cloud API。
**封禁风险**：**消除**（官方授权通道，不违反 ToS）。
**成本（2026 定价，按收件人国家码 = 香港计费）**：
- ✅ **用户发起的服务对话（24h 窗口内的回复）全球免费**——PA 主要是"回复用户消息"，落在 24h 窗口内 → **大部分免费**。
- ⚠️ **主动推送（cron: ArXiv/HN/货代/日报等）若落在 24h 窗口外** → 算 template 消息，按条计费（utility ~$0.004、marketing ~$0.025/条，香港费率需查）。若用户每天与 PA 互动 → 窗口常开 → 推送也多在窗口内免费；若不互动 → 主动推送需 template（小额成本）。
- 申请 Cloud API **免费**。
**🔴 关键摩擦（个人 PA 场景的可能 dealbreaker）**：
- Cloud API 需要一个**专用号码**，该号码**接入 API 后不能再在 WhatsApp 手机 app 上正常使用**。
- 当前模型是 PA linked 到**你自己的个人 WhatsApp 号**（你用同一个号既和 PA 聊、也和朋友聊）。迁到 Cloud API 意味着：要么**牺牲该号的个人 WhatsApp 使用**，要么**为 PA 申请一个新的专用号**（你以后发给一个不同的号码找 PA）。两者都是交互模型的大改动。
- 还需 Meta Business 验证 + 确认 OpenClaw 是否支持 Cloud API channel（当前用的是 Baileys channel）。

**收益**：彻底消除封禁风险 + 官方稳定性。**成本**：交互模型大改 + 可能需新号 + OpenClaw 支持性未知 + 主动推送小额费用。

### 选项 C：接受风险 + 监控 + 恢复 SOP（现状 + V37.9.162）

**做法**：保持 Baileys 不变，靠 V37.9.162 的频道掉线检测（1h 内 Discord 告警）+ 手机重启 + 等限流冷却 + 单次 login 重链的恢复 SOP。
**成本**：零（已实现检测）。**收益**：故障可见（不再 7h 静默）。**代价**：**封禁会复发**（根因未动），每次复发需人工恢复（手机重启 + 等冷却 + 扫码）。

## 四、推荐

**A + C 组合（近期），B 作为升级路径（条件触发）**：

1. **立即（C，已完成）**：V37.9.162 检测已上线，故障 1h 可见。
2. **近期（A，可配性已确认）**：`web.reconnect.maxAttempts` 0 → 有限值（5-8）= 全局熔断,这是性价比最高的预防（低成本配置、保留便利模型、显著降风险，OpenClaw 文档 + issue #16270 都推荐）。先 Mac Mini 核实当前值（预期 0）再改，重启 Gateway 生效。与 V37.9.162 检测协同（停止后 1h 告警）。
3. **升级触发（B）**：若**封禁复发**（尤其升级为长期/永久）**或**你愿意接受"PA 用专用号、与个人 WhatsApp 分离"的交互模型 → 才值得迁官方 Cloud API。对个人 PA 而言，B 的交互模型摩擦通常 > 封禁风险（只要 A 能把风险降到可接受），故默认不迁。

**理由**：个人 PA 的核心价值是"用你自己的 WhatsApp 号、零摩擦"。官方 API 消除封禁但破坏这个核心（专用号/牺牲个人使用）。所以**先用 A 把风险降下来 + C 兜底可见性**，把 B 留给"A 不够 / 风险不可接受"的情况。

## 五、决策点（需要你定）

1. **是否同意 A+C 为主、B 条件触发的方向?**（还是你更倾向直接评估 B 迁官方 API?）
2. **走 A（已确认可配）**：WhatsApp 恢复后（不急），在 Mac Mini 跑一次**纯查询**核实当前 `openclaw.json` 的 `web.reconnect.maxAttempts` 实际值（预期是 0/无限 = 根因）+ `web.whatsapp` 超时段。确认后把 `maxAttempts` 0 → 有限值（5-8）+ 重启 Gateway。核实命令见下方"附：Mac Mini 核实命令"。**注意（血案 #98）**：4.27 的实际 key 路径/schema 可能与文档示例略有出入，所以**先查实际 openclaw.json 再改**，不照搬文档示例盲改。
3. **若考虑 B**：你能接受"为 PA 用一个专用号、与个人 WhatsApp 分开"吗?（这是 B 可行性的前提。）

## 附：Mac Mini 核实命令（纯查询、不改、无 secrets）

WhatsApp 恢复后（不急）跑这条，看当前 `web.reconnect` / `web.whatsapp` 实际值与嵌套路径（matched keys 全是数字配置/段名，不含密钥；带行号便于追问上下文）：

```
python3 -m json.tool ~/.openclaw/openclaw.json | grep -inE "reconnect|maxattempts|keepalive|connecttimeout|defaultquerytimeout|initialms|maxms|jitter|\"factor\"|\"web\"|\"whatsapp\""
```

预期看到 `maxAttempts: 0`（根因）。把结果贴给我,我据实际 4.27 schema 给精确的改法(改哪个 key 路径、设几),你改 `openclaw.json` + `bash ~/restart.sh` 生效。**若 grep 无输出** = 这些键未显式设置(用内置默认 maxAttempts=0),那就是需要新增 `web.reconnect` 块,我据 4.27 schema 给位置。

## 六、引用

- [WhatsApp Automation Ban Risk: Safe vs Unsafe Tools (2026) — kraya-ai](https://blog.kraya-ai.com/whatsapp-automation-ban-risk)（2-8 周封禁时间线 / 68% 12 月内被封 / ML 反滥用权重）
- [Baileys DisconnectReason 枚举 — baileys.wiki](https://baileys.wiki/docs/api/enumerations/DisconnectReason/)（401/428/408/440/515/503 处理）
- [Baileys issue #1625: 428 Connection Timeout after ~24h with Multi-File Auth](https://github.com/WhiskeySockets/Baileys/issues/1625)（疑似今天 01:59 428 的已知 bug）
- [Baileys issue #1869: High number of bans on WhatsApp](https://github.com/WhiskeySockets/Baileys/issues/1869)
- [OpenClaw issue #11871: auto-reconnect on Baileys WebSocket session drop](https://github.com/openclaw/openclaw/issues/11871)（OpenClaw 的 Baileys 重连实现：指数退避 5s/15s/60s/5min + N 次失败后告警）
- [OpenClaw issue #16270: Add circuit breaker to prevent reconnect loops from triggering account bans](https://github.com/openclaw/openclaw/issues/16270)（**与本血案完全同构,2026-02-14 开,但已 closed as NOT PLANNED** — OpenClaw 拒绝加自动熔断,官方立场=暴露 `maxAttempts` 让用户自配。提议过"10 连续失败→30min 退避"被拒）
- [OpenClaw 2026.6.2 release: "improved gateway recovery"](https://releasebot.io/updates/openclaw)（>4.27 的版本,gateway recovery 改进,但 reconnect 配置暴露 #56365 仍 open — 升级前需按 tripwire 纪律评估）
- [OpenClaw issue #56054: WhatsApp Baileys perpetual status 499 reconnection loop with creds.json corruption cycle](https://github.com/openclaw/openclaw/issues/56054)（匹配我们的 499 码）
- [OpenClaw issue #56365: makeWASocket config passthrough — expose Baileys socket timing](https://github.com/openclaw/openclaw/issues/56365)（Baileys socket timing 配置透传）
- [OpenClaw 文档 — Configuration: channels](https://docs.openclaw.ai/gateway/config-channels)（`web.reconnect`: initialMs/maxMs/factor/jitter/maxAttempts + `web.whatsapp`: keepAliveIntervalMs/connectTimeoutMs + "Protecting your WhatsApp account" 段推荐配 maxAttempts）
- [WhatsApp Business API Pricing 2026 — Blueticks](https://blueticks.co/blog/whatsapp-business-api-pricing-2026)（24h 窗口服务对话免费 / 按收件人国家码计费 / 申请免费）
- [OpenClaw WhatsApp Risks: What Engineers Must Know — zenvanriel](https://zenvanriel.com/ai-engineer-blog/openclaw-whatsapp-risks-engineers-guide/)
- ⚠️ [lotusbail 恶意"anti-ban"包窃取凭据案例](https://github.com/kobie3717/baileys-antiban)（警示：勿信第三方 anti-ban 包）
