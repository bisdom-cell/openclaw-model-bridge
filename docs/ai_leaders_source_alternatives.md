# ai_leaders_x 数据源替代评估报告

> V37.9.102 — 2026-06-03 调研。触发：V37.9.101 复盘发现 X Syndication API 退化，
> ai_leaders_x 实际产 ~0 新推文。用户选"调研替代源"。本报告供决策是否换源。

---

## TL;DR — 推荐

| 方案 | 月成本 (我们用量) | 可靠性 | 推荐度 |
|------|------------------|--------|--------|
| **TwitterAPI.io（第三方 REST）** | **~$1-6** | 高 | 🥇 **首选** |
| 官方 X API（pay-per-use） | ~$100-200 | 最高 | 🥈 太贵 |
| 自托管 Nitter（Docker） | $0 + 维护成本高 | 低（account pool 被封即死） | 🥉 不推荐 |
| RSSHub / RSS Bridge | $0 | 低（底层仍 Syndication/Nitter） | ❌ 同病 |
| 保持 Syndication + 轮换（现状） | $0 | 退化中（产 ~0） | ⚠️ 临时 |

**一句话**：免费源（Syndication/Nitter/RSSHub）都在 X 收紧政策下死亡或退化；唯一**便宜又可靠**的是第三方付费抓取 API（TwitterAPI.io ~$1-6/月，比官方便宜 96%+）。若 ai_leaders_x 的"AI 大神 X 观点"价值值 ~$2/月 → 换 TwitterAPI.io；否则降频/停用。

---

## 1. 问题陈述（2026-06-03 复盘实测数据）

X Syndication API（`syndication.twitter.com/srv/timeline-profile/...`，我们一直用的非认证端点）**正在退化**：

- **31 账号最新推文年份分布**：2026=6 / 2025=17 / 0推文=6 / no_data(429)=2
- 多数账号（karpathy/ylecun/Hinton/Dario...）天天发推，但 Syndication 快照**冻结在 2025-09~11**（6-10 月前）= API 停止刷新这些账号
- 只有 ~1 个账号（PalantirTech, 4d）有 14 天 cutoff 内的新内容
- 6 个账号返回 200 但 0 本人推文（embed-disabled，Syndication 里真死）
- V37.9.101 轮换修复了 429 限流（真问题，fetch 现在成功），但**治不了数据源冻结**

**根因**：X 自 2023 起持续收紧非认证数据访问（封 guest account、冻结 Syndication 缓存刷新）。这是行业大趋势，我们不控制。

---

## 2. 替代源详评

### 🥇 TwitterAPI.io（及同类第三方：Scrapfly / ScrapingBee / Apify / Bright Data）
- **机制**：第三方维护 account pool + 代理池抓取，封装成 REST API，无需我们持有 X 认证
- **定价**：$0.15/1000 推文 + $0.18/1000 profile。Google 登录送免费起始额度。credit 不过期。无 $200/月门槛
- **我们成本**：31 账号 × 3 推 × 2/天 ≈ 5,580 推/月 × $0.15/1000 = **~$0.84/月**；即使每账号取 20 推过滤也仅 ~$5.6/月
- **优点**：便宜、REST 易集成、无认证负担、有 SLA、免费额度可先试
- **缺点**：仍是第三方（X 政策若进一步收紧，第三方也承压，但他们专业维护 account pool 比我们自抓强）；按量付费需绑卡/充值
- **迁移工作量**：中——重写 fetch 段（curl Syndication HTML + 解析 __NEXT_DATA__ → REST GET + JSON），解析器更简单（官方结构化 JSON vs HTML 抠 __NEXT_DATA__）

### 🥈 官方 X API（pay-per-use，2026-02 起）
- **定价**：免费层已取消。pay-per-use $0.005/读，封顶 2M 读/月。Basic($200/月)/Pro($5000/月) 仅老用户
- **我们成本**：31×20×2×30 ≈ 37,200 读/月 × $0.005 = **~$186/月**
- **优点**：官方、最可靠、合规
- **缺点**：**贵 30-200 倍**于第三方，对个人项目不划算

### 🥉 自托管 Nitter（Docker）
- **2026 现状**：原 Nitter 项目休眠，公共实例基本死（account pool 被封即停）。自托管 Docker 是唯一可行但**需自备 X 凭证 + 频繁换号 + 维护**
- **成本**：$0 现金，但**运维成本高**（凭证被封要换、实例要盯）
- **结论**：把"X 收紧"问题转嫁到我们自己维护 account pool，比第三方更脆弱。不推荐

### ❌ RSSHub / RSS Bridge
- **2026 现状**：RSSHub 已"恢复"，RSS Bridge 能生成 X feed
- **致命缺陷**：底层 twitter 路由仍依赖 Nitter/Syndication → **同样退化**。公共实例限流
- **结论**：换汤不换药，同一个病根

### ⚠️ 保持现状（Syndication + V37.9.101 轮换）
- 免费、已部署、轮换防 429
- 但**产 ~0 有用推文**（数据冻结）。临时可接受，长期是僵尸 job

---

## 3. 决策矩阵

| 你看重 | 选 |
|--------|-----|
| 便宜 + 可靠 + 想要真实时 X 观点 | **TwitterAPI.io**（~$2/月，先用免费额度试） |
| 完全不想花钱 + 接受 job 退化 | 保持现状 + 清 6 死账号 + 降频 1/天 |
| 完全不想花钱 + job 已无价值 | disable ai_leaders_x，靠 arxiv/hf/rss/hn 覆盖 AI 动态 |
| 合规优先不在乎成本 | 官方 X API pay-per-use |

**关键判断**：ai_leaders_x 的**独特价值** = "AI 大神在 X 上的即时观点/态度"（vs 已有的 arxiv/hf 是论文、rss/hn 是文章）。这个价值是否值 ~$2/月？
- 若是 → TwitterAPI.io 是唯一便宜可靠路径
- 若否（其他源已够覆盖 AI 动态）→ disable，不必维护一个退化 job

---

## 4. 若选 TwitterAPI.io 的迁移路径（V37.9.103+）

1. Google 登录 twitterapi.io 拿 API key（免费额度先试）→ 存 `TWITTERAPI_IO_KEY` env（plist，遵 INV-PLIST-ENV-001）
2. 重写 run_ai_leaders_x.sh fetch 段：`curl syndication.twitter.com/...` → `curl twitterapi.io/twitter/user/last_tweets?userName=X` + Bearer key
3. 解析器简化：HTML 抠 __NEXT_DATA__ → 直接 JSON（更稳）
4. **保留 V37.9.101 轮换 + 5s 节流**（第三方也有速率限制，轮换仍是好习惯）+ FAIL-OPEN
5. 保留健康分类（ai_leaders_rotation.py classify_account）——第三方返回也能判 stale/alive
6. 先免费额度试跑 1 周验证数据新鲜度（newest_tweet 应是近期），再决定是否充值
7. 单测 + Mac Mini E2E + 成本监控（每月 credit 消耗）

---

## 元价值

这是**外部依赖死亡的典型案例**：免费薅羊毛的非认证端点终将被平台收紧。X Syndication 是 V27 起 ai_leaders/finance_news 的基石（原则 #27"X 是高质量实时数据第一选择"），如今进入退化期。教训：**关键数据源不能长期依赖未文档化的免费端点**——要么接受退化，要么转付费/合规通道。finance_news 也用同款 Syndication，同样面临此风险（待评估）。

---

*Sources（2026-06 调研）：xpoz.ai / postproxy.dev / twitterapi.io / docs.x.com / blog.thefix.it.com / simple-web.org / en.wikipedia.org/wiki/Nitter*
