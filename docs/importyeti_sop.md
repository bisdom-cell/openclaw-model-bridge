# ImportYeti 手动查询 SOP

> 货代 Watcher V2 配套操作指南。适用于收到 📦 ImportYeti 链接后的深度调研。

---

## 一、什么是 ImportYeti

ImportYeti 是免费的美国海关海运提单（Bill of Lading）查询平台，收录了 2015 年至今超过 7000 万条海运记录。

**可查数据**：买方/卖方名称、供应商国家、货物重量、起止港口、HS 编码（商品分类）。

**局限**：仅覆盖美国海运进口，不含空运/陆运；部分企业已申请保密。

---

## 二、货代 Watcher 自动触发条件

当 Watcher 分析结果中某条新闻满足：
- 评级 ≥ ⭐⭐⭐⭐
- 成功提取到企业名称

系统自动附加 ImportYeti 查询链接：
```
📦 ImportYeti：https://www.importyeti.com/search?q=企业名
```

---

## 三、手动查询步骤

### 3.1 基本查询

1. 打开 https://www.importyeti.com
2. 搜索框输入**企业法定名称**（英文，如 `SHEIN`, `TEMU`, `COSCO SHIPPING`）
3. 查看结果页：
   - **Shipment Count**：指定时间段内的海运记录数量
   - **Top Suppliers**：前 10 大供应商列表
   - **Top 10 US Buyers**：前 10 大美国买家
   - **HS Code Breakdown**：按商品分类的进口分布
   - **Recent Shipments**：最近的海运记录

### 3.2 深度调研（发现商机后）

| 步骤 | 操作 | 目的 |
|------|------|------|
| 1 | 点击供应商名称 | 查看该供应商还给哪些企业供货（发现潜在客户） |
| 2 | 对比 HS 编码分布 | 了解企业主要进口品类 |
| 3 | 查看 Shipment 时间线 | 判断进口量趋势（增长/下降） |
| 4 | 搜索竞品企业名称 | 对比供应商重合度 |

### 3.3 供应商反查

1. 切换到 **Supplier Search** 模式
2. 输入中国供应商名称（英文）
3. 查看该供应商的所有美国客户 → 识别潜在货代需求方

---

## 四、结果记录

将有价值的发现写入 KB：

```bash
# 示例：记录一条调研结果
cat >> ~/.kb/sources/freight_daily.md << 'EOF'

## [2026-03-08] ImportYeti 调研：ACME Corp
- 供应商：深圳XX贸易有限公司（前3大供应商之一）
- 月均海运量：~50 TEU
- 主要品类：HS 8471（电脑设备）
- 商机判断：中等体量，有货代合作空间
EOF
```

---

## 五、免费账户限制

| 项目 | 限制 |
|------|------|
| 页面浏览 | 25 次/IP（登录后无限制） |
| CSV 导出 | 需 Custom Plan（付费） |
| Power Query | 需 Custom Plan（付费） |
| 注册 | 免费，邮箱注册即可 |

**建议**：用邮箱注册免费账户，避免 25 次浏览限制。

---

## 六、替代平台（付费，未来参考）

| 平台 | 覆盖范围 | 备注 |
|------|----------|------|
| [ExportGenius](https://www.exportgenius.in) | 多国进出口数据 | V4 候选 |
| [Panjiva](https://panjiva.com) | 全球贸易数据 | S&P Global 旗下 |
| [ImportGenius](https://www.importgenius.com) | 美国海关数据 | ImportYeti 竞品 |
| [Sayari](https://sayari.com) | 供应链风险分析 | 偏合规/OSINT |
