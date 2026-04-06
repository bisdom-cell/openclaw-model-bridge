# 本体论 × LLM × Agent：关键文献追踪

> 按主题组织，标注阅读优先级（P0=必读，P1=重要，P2=参考）

## 基础理论

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P0 | Guarino (1998) *Formal Ontology in Information Systems* | 形式本体论在信息系统中的奠基之作 |
| P0 | Gruber (1993) *A Translation Approach to Portable Ontology Specifications* | "本体是概念化的显式规范"经典定义 |
| P1 | Smith et al. (2007) *The OBO Foundry* | 领域本体协作方法论 |
| P1 | Arp, Smith, Spear (2015) *Building Ontologies with Basic Formal Ontology* | BFO 实践指南 |

## 企业本体论

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P0 | Dietz (2006) *Enterprise Ontology: Theory and Methodology* | DEMO 方法论：用交易模式分析企业本质结构 |
| P1 | The Open Group (2019) *ArchiMate 3.1 Specification* | 企业架构建模标准 |
| P1 | FIBO (Financial Industry Business Ontology) | 金融行业本体标准，可参考其方法论 |
| P2 | Uschold & Gruninger (1996) *Ontologies: Principles, Methods and Applications* | 本体工程方法论综述 |

## Neuro-Symbolic AI（本体论 + LLM）

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P0 | Marcus & Davis (2019) *Rebooting AI* | 为什么纯深度学习不够，需要符号推理 |
| P0 | Bengio (2024-2026) System 2 Deep Learning 系列 | 从纯连接主义转向结构化推理 |
| P0 | d'Avila Garcez & Lamb (2023) *Neurosymbolic AI: The 3rd Wave* | Neuro-Symbolic 第三波浪潮综述 |
| P0 | Hitzler et al. (2022) *Neuro-Symbolic AI: The State of the Art* | 最全面的 Neuro-Symbolic 综述 |
| P1 | Horrocks et al. — OWL/Description Logic 体系 | 本体推理的形式化基础 |
| P1 | Pan et al. (2024) *Unifying Large Language Models and Knowledge Graphs* | LLM + KG 统一框架综述 |
| P1 | Zhu et al. (2024) *LLMs for Knowledge Graph Construction and Reasoning* | LLM 自动构建和推理知识图谱 |
| P1 | Riegel et al. (2020) *Logical Neural Networks* (IBM) | 逻辑门=神经元，可训练且可解释 |
| P2 | Serafini & d'Avila Garcez (2016) *Logic Tensor Networks* | 一阶逻辑编码为张量运算 |
| P2 | Manhaeve et al. (2021) *DeepProbLog* | 概率逻辑编程 + 神经网络 |
| P2 | Zhang et al. (2019) *ERNIE* | BERT + 知识图谱实体嵌入 |
| P2 | Liu et al. (2020) *K-BERT* | 在输入层注入知识图谱子图 |

## Agent + 本体

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P0 | 待追踪：Ontology-Driven Agent Architecture 方向 | Agent 工具调用的本体约束 |
| P1 | FIPA Agent Communication Language | Agent 通信的本体基础（经典） |
| P1 | Semantic Web Services (OWL-S) | 服务（工具）的语义描述标准 |

## 供应链本体

| 优先级 | 文献/标准 | 关键贡献 |
|--------|----------|---------|
| P0 | DCSA Standards (Digital Container Shipping Association) | 集装箱航运数据标准（API + 事件模型） |
| P1 | UN/CEFACT Multi-Modal Transport Reference Data Model | 贸易便利化术语标准 |
| P1 | Laurier & Poels (2013) *An Ontological Analysis of Shipping* | 航运本体分析 |
| P1 | Daniele et al. (2014) *Towards a Core Ontology for Logistics* | 物流核心本体 |
| P2 | Scheuermann & Leukel (2014) *Supply Chain Management Ontology* | 供应链管理本体综述 |
| P2 | GS1 Standards | 全球供应链标识标准（GTIN, SSCC） |
| P2 | ISO 28000:2022 | 供应链安全管理体系 |

## 顶层本体（BFO/DOLCE/UFO）

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P0 | Guizzardi (2005) *Ontological Foundations for Structural Conceptual Models* | UFO 博士论文（核心参考） |
| P0 | Guizzardi et al. (2022) *UFO: Unified Foundational Ontology* | UFO 最新综述 |
| P1 | Masolo et al. (2003) *WonderWeb D18: Ontology Library* | DOLCE 首次完整发表 |
| P1 | Smith (2015) *Basic Formal Ontology 2.0* — ISO/IEC 21838-2 | BFO 国际标准 |
| P1 | Guizzardi et al. (2006) *On the Role of Foundational Ontologies for Interoperability* | BFO/DOLCE/UFO 对齐研究 |

## 企业 AI 治理

| 优先级 | 文献 | 关键贡献 |
|--------|------|---------|
| P1 | EU AI Act (2024) | 对高风险 AI 系统的合规要求 |
| P1 | NIST AI Risk Management Framework | AI 风险管理框架 |
| P2 | ISO/IEC 42001 AI Management System | AI 管理体系标准 |

## 待通过 Dream 引擎自动发现

以下方向应加入 ArXiv/Semantic Scholar 监控关键词：
- `ontology-driven agent`
- `neuro-symbolic LLM`
- `knowledge graph reasoning LLM`
- `enterprise ontology digital transformation`
- `formal verification AI agent`
- `supply chain ontology`
- `logistics knowledge graph`
- `foundational ontology comparison`（BFO/DOLCE/UFO）
- `OntoUML conceptual modeling`
- `logical neural networks`

---
*初始版本: 2026-04-06 | 更新: 2026-04-06（+Neuro-Symbolic 7文献, +供应链 7文献/标准, +顶层本体 5文献, +5监控关键词）*
*Dream 引擎发现的相关论文将自动追加*
