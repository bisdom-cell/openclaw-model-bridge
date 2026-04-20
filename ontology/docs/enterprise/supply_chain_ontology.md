# 供应链本体：从货代实践到行业标准

> 核心论点：供应链是本体论最成熟的工业应用场景之一。从我们的货代 Watcher 出发，可以连接到全球标准化体系。

## 为什么供应链需要本体

### 行业痛点

供应链是一个**多方协作、信息密集、术语混乱**的领域：

```
同一个概念，不同叫法：
  "集装箱" = Container = Box = TEU = Unit（在不同系统中）
  "客户"   = Shipper = Consignor = Booking Party = Account
  "港口"   = Port = Terminal = Facility = Hub

同一个术语，不同含义：
  "ETA" = Estimated Time of Arrival（到港）还是 Estimated Time Available（可用）？
  "Free Time" = 免箱期 还是 免堆期？具体天数因港口而异
  "CY" = Container Yard 还是 Calendar Year？上下文决定
```

**没有共同语义框架，供应链协作 = 翻译接力赛。** 每个参与方都在猜对方的意思。

### LLM 在供应链中的困境

货代 Watcher 监控的数据来自多个来源（新闻、公告、运价平台），LLM 分析时面临：
1. **术语歧义**：不同来源用不同术语描述同一事物
2. **关系模糊**：运价变动的原因链（地缘政治→运力→运价→货量）不显式
3. **规则隐式**：哪些变动需要告警？阈值是什么？专业判断未形式化
4. **历史缺失**：模型不知道"这条航线去年同期运价是多少"

本体论 = 为 LLM 提供货代领域的"字典 + 语法 + 常识"。

## 供应链本体的层次结构

### 按四层框架组织

```
Level 4: 顶层本体
         UFO-A（实体）+ UFO-B（事件）+ UFO-C（社会）

Level 3: 供应链领域本体
         ├── 物流子域：运输模式、路线、设施、设备
         ├── 贸易子域：买方、卖方、商品、贸易条款
         ├── 金融子域：支付、信用证、保险
         └── 监管子域：海关、检验检疫、制裁

Level 2: 货代任务本体
         ├── 订舱任务：揽货→报价→订舱→确认
         ├── 运输任务：装箱→报关→运输→卸货→交付
         ├── 单证任务：提单/舱单/发票/箱单/原产地证
         └── 监控任务：运价追踪→异常检测→告警→建议

Level 1: OpenClaw 应用本体
         ├── 货代 Watcher：运价信号→LLM分析→KB写入→推送
         ├── search_kb：语义检索历史运价和分析
         └── Dream 引擎：跨时间维度趋势发现
```

## 核心概念模型

### 物流核心概念

```
TransportService（运输服务）
├── OceanFreight（海运）
│   ├── FCL（整箱）
│   └── LCL（拼箱）
├── AirFreight（空运）
├── RailFreight（铁路）
└── TruckTransport（陆运）

Route（航线/路线）
├── origin: Port
├── destination: Port
├── transitPorts: Port[]（中转港）
├── transitTime: Duration
└── serviceType: {Direct, Transshipment}

Vessel（船舶）
├── vesselName: String
├── IMONumber: String（唯一标识）
├── vesselType: {Container, Bulk, Tanker, ...}
├── TEUCapacity: Integer（集装箱船容量）
└── flag: Country

Container（集装箱）
├── containerNumber: String（唯一标识，格式 XXXX1234567）
├── containerType: {20GP, 40GP, 40HC, 20RF, ...}
├── tareWeight: Weight
└── maxPayload: Weight
```

### 商业核心概念

```
Booking（订舱）
├── bookingNumber: String
├── shipper: Party（托运人）
├── consignee: Party（收货人）
├── commodity: Commodity（货物）
├── route: Route
├── containers: Container[]
├── freightTerms: {Prepaid, Collect}
└── incoterms: {FOB, CIF, EXW, DDP, ...}

FreightRate（运价）
├── route: Route
├── containerType: ContainerType
├── baseRate: Money
├── surcharges: Surcharge[]（附加费）
│   ├── BAF（燃油附加费）
│   ├── CAF（货币调整因子）
│   ├── THC（码头操作费）
│   ├── PSS（旺季附加费）
│   └── GRI（一般运价上调）
├── effectiveDate: Date
├── expiryDate: Date
└── carrier: Carrier

RateChange（运价变动）——货代 Watcher 核心关注
├── route: Route
├── previousRate: FreightRate
├── newRate: FreightRate
├── changePercent: Decimal
├── changeReason: ChangeReason
│   ├── MarketDemand（市场供需）
│   ├── GeopoliticalEvent（地缘政治）
│   ├── FuelPrice（燃油价格）
│   ├── PortCongestion（港口拥堵）
│   ├── SeasonalAdjustment（季节性调整）
│   └── RegulatoryChange（监管变化）
└── impactAssessment: String（LLM 分析结果）
```

### 参与方概念

```
Party（参与方）——UFO-C 的 Agent 实例化
├── Carrier（承运人）——船公司
│   ├── SCAC: String（标准承运人代码）
│   └── services: TransportService[]
├── Shipper（托运人）
├── Consignee（收货人）
├── FreightForwarder（货代）——我们的用户
│   ├── clientRelationships: Party[]
│   ├── preferredCarriers: Carrier[]
│   └── tradeRoutes: Route[]（常用航线）
├── CustomsBroker（报关行）
├── TerminalOperator（码头运营商）
└── InsuranceProvider（保险公司）
```

## 与现有标准的对齐

### 国际标准

| 标准 | 组织 | 覆盖范围 | 对我们的价值 |
|------|------|---------|-------------|
| **UN/CEFACT** | 联合国 | 贸易便利化、电子单证 | 术语标准化（如 UN/LOCODE 港口代码） |
| **GS1** | GS1 | 产品标识、追溯 | 物品级别的唯一标识（GTIN, SSCC） |
| **DCSA** | 数字集装箱航运协会 | 集装箱航运数据标准 | API 标准 + 事件模型（最直接相关） |
| **ISO 28000** | ISO | 供应链安全管理 | 安全维度的本体化 |
| **WCO** | 世界海关组织 | HS 编码、海关数据模型 | 商品分类本体 |
| **IATA** | 国际航空运输协会 | 空运标准 | 如果扩展到空运 |

### DCSA 深度对齐

DCSA（Digital Container Shipping Association）由 MSC、Maersk、CMA CGM 等九大船公司联合成立，发布了集装箱航运的数据标准：

```
DCSA 核心概念 → 我们的本体映射：

DCSA Transport Document → Booking + BillOfLading
DCSA Shipment Event    → ShipmentStatusChange（事件本体）
DCSA Equipment Event   → ContainerStatusChange
DCSA Transport Event   → VesselMovement（船舶动态）
DCSA Location          → Port / Terminal / Facility
DCSA Party             → Party（我们的参与方体系）
```

对齐 DCSA = 我们的本体可以直接消费船公司 API 返回的标准化数据。

## 本体驱动的货代 Watcher 升级

### 当前架构（无本体）

```
新闻/公告/运价平台 → 爬虫 → 原始文本 → LLM 分析 → KB 写入 → 推送
                                          ↑
                                    全靠 LLM 理解
                                    没有领域知识约束
```

**问题**：LLM 可能把 PSS（旺季附加费）和 GRI（一般运价上调）混为一谈；可能不知道某条航线的历史运价区间。

### 目标架构（本体辅助）

```
数据源 → 爬虫 → 原始文本
                   │
                   ▼
            ┌──────────────┐
            │ 实体识别+关系抽取 │ ← 供应链本体（概念+关系+同义词表）
            │ （LLM + 本体约束） │
            └──────┬───────┘
                   ▼
            结构化运价信号
            {route, carrier, rate, change%, reason, date}
                   │
                   ▼
            ┌──────────────┐
            │ 异常检测+趋势分析 │ ← 历史基线（KB 中的结构化数据）
            │ （规则 + LLM 解读） │ ← 阈值本体（什么算"异常"）
            └──────┬───────┘
                   ▼
            结构化分析报告 → KB + 推送
```

### 本体带来的具体改进

| 环节 | 无本体 | 有本体 |
|------|--------|--------|
| 实体识别 | LLM 自由判断 | 受限于本体中的合法实体类型 |
| 同义词处理 | 靠 LLM 通用知识 | 显式同义词表（Container=Box=TEU） |
| 运价结构 | 扁平数字 | base rate + surcharges 结构化分解 |
| 变动原因 | LLM 猜测 | 从预定义原因枚举中选择，可追溯 |
| 历史对比 | 靠 search_kb 粗搜 | 结构化查询：同航线同季节同箱型的历史运价 |
| 告警阈值 | 硬编码百分比 | 按航线/季节/箱型动态阈值（本体定义） |

## 供应链本体的实现路径

### Phase 1：术语标准化（当前可做）

创建 `supply_chain_terms.yaml`（或 JSON-LD）：

```yaml
# 概念定义 + 同义词 + 约束
concepts:
  Container:
    synonyms: [Box, Unit, Equipment]
    types:
      20GP: {teu: 1, maxPayload: "28.2t"}
      40GP: {teu: 2, maxPayload: "26.5t"}
      40HC: {teu: 2, maxPayload: "26.2t"}
      20RF: {teu: 1, maxPayload: "27.0t", refrigerated: true}

  Surcharge:
    types:
      BAF: {fullName: "Bunker Adjustment Factor", varies_with: "fuel_price"}
      CAF: {fullName: "Currency Adjustment Factor", varies_with: "exchange_rate"}
      THC: {fullName: "Terminal Handling Charge", varies_with: "port"}
      PSS: {fullName: "Peak Season Surcharge", seasonal: true}
      GRI: {fullName: "General Rate Increase", description: "Carrier initiated rate increase"}

  Port:
    standard: "UN/LOCODE"
    examples:
      HKHKG: {name: "Hong Kong", country: "HK", timezone: "Asia/Hong_Kong"}
      CNSHA: {name: "Shanghai", country: "CN", timezone: "Asia/Shanghai"}
      NLRTM: {name: "Rotterdam", country: "NL", timezone: "Europe/Amsterdam"}
```

### Phase 2：关系与约束（中期）

```yaml
relations:
  - {subject: Route, predicate: connectsPort, object: Port, cardinality: ">=2"}
  - {subject: Booking, predicate: placedBy, object: Party, cardinality: "==1"}
  - {subject: FreightRate, predicate: appliesTo, object: Route, cardinality: "==1"}

constraints:
  - name: "TEU 非负"
    axiom: "∀ Container: TEUCapacity > 0"
  - name: "有效期约束"
    axiom: "∀ FreightRate: effectiveDate < expiryDate"
  - name: "大额审批"
    axiom: "∀ Booking: value > 100K USD → creditCheck = true"
  - name: "危险品限制"
    axiom: "∀ Booking: commodity.isDangerous → requires(DGCertificate)"
```

### Phase 3：LLM 集成（长期）

将本体注入货代 Watcher 的 LLM 分析 prompt，提升分析质量：

```
[System] 你是一个供应链分析助手。请使用以下领域术语标准：
- 运价组成：base rate + surcharges（BAF/CAF/THC/PSS/GRI）
- 变动原因必须从以下枚举中选择：MarketDemand/GeopoliticalEvent/FuelPrice/PortCongestion/SeasonalAdjustment/RegulatoryChange
- 港口使用 UN/LOCODE 标准代码
- 分析时必须给出同航线历史对比（如有 KB 数据）
```

## 与 Ontology KB 其他模块的关系

```
supply_chain_ontology.md（本文件）
  │
  ├─→ foundations/schools_comparison.md
  │   └── 推荐 UFO 作为顶层本体（UFO-C 的 Agent/Service 概念直接适用）
  │
  ├─→ architecture/ontology_llm_agent.md
  │   └── 供应链 = Ontology+LLM+Agent 三角架构的最佳行业实例
  │
  ├─→ architecture/neuro_symbolic.md
  │   └── 货代 Watcher 升级路径 = Neuro-Symbolic 范式一（本体指导检索）的落地
  │
  ├─→ enterprise/governance.md
  │   └── 供应链合规（海关/危险品/制裁）= AI 治理本体的行业实例化
  │
  └─→ cases/openclaw_as_ontology.md
      └── 货代 Watcher/search_kb = OpenClaw 应用本体的具体任务本体
```

## 参考文献与标准

- **DCSA Standards** — https://dcsa.org/standards/（集装箱航运数据标准）
- **UN/CEFACT** — 联合国贸易便利化中心（Multi-Modal Transport Reference Data Model）
- **GS1 Standards** — 全球供应链标识标准
- **Hitzler, P. et al. (2009)** *OWL 2 Web Ontology Language Primer* — 本体编码标准
- **Laurier, W. & Poels, G. (2013)** *An Ontological Analysis of Shipping*（航运本体分析）
- **Daniele, L. et al. (2014)** *Towards a Core Ontology for Logistics*（物流核心本体）
- **Scheuermann, A. & Leukel, J. (2014)** *Supply Chain Management Ontology*（供应链管理本体综述）
- **ISO 28000:2022** — 供应链安全管理体系

---
*创建: 2026-04-06 | 活文档：随货代业务深入和标准追踪持续更新*
