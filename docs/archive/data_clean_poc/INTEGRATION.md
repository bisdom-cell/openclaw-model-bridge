# 数据清洗 Agent 集成指引

## Phase 1 架构

```
用户 (WhatsApp): "帮我清洗这个CSV"
    │
    ▼
OpenClaw LLM 使用已有工具（exec + read + write）
    │
    ├─ exec("python3 ~/data_clean.py profile <file>")     → 数据画像
    ├─ LLM 分析报告 → 生成清洗方案 → 展示给用户确认
    ├─ exec("python3 ~/data_clean.py execute <file> ...")  → 执行清洗
    ├─ read("~/.data_clean/workspace/report.md")           → 展示报告
    └─ exec("python3 ~/data_clean.py validate ...")        → 验证结果
```

**零新增工具槽位**：复用 exec/read/write，CLI 子命令无限扩展。

## CLI 子命令

```bash
# 数据画像
python3 ~/data_clean.py profile <file.csv>
python3 ~/data_clean.py profile <file.csv> --format text

# 查看可用操作
python3 ~/data_clean.py list-ops

# 执行清洗
python3 ~/data_clean.py execute <file.csv> --ops trim dedup fix_dates fix_case \
  --fix-case-cols status email \
  --fix-date-cols order_date

# 验证结果
python3 ~/data_clean.py validate <original.csv> <cleaned.csv>

# 查看版本历史
python3 ~/data_clean.py history <file.csv>
```

## Workspace CLAUDE.md 注入内容

在 `kb_inject.sh` 生成的 workspace CLAUDE.md 中添加以下指引，让 WhatsApp PA 知道如何使用数据清洗工具：

```markdown
## 数据清洗

当用户要求清洗 CSV/表格数据时：

1. 先用 `exec` 运行 `python3 ~/data_clean.py profile <文件路径>` 获取数据质量报告
2. 分析报告中的 issues，向用户解释发现的问题并建议清洗方案
3. 用户确认后，用 `exec` 运行 `python3 ~/data_clean.py execute <文件路径> --ops <操作列表>`
4. 用 `read` 读取 `~/.data_clean/workspace/report.md` 展示清洗结果
5. 如需回滚，版本快照在 `~/.data_clean/workspace/versions/`

可用操作: dedup(去重) dedup_near(近似去重) trim(去空格) fix_dates(统一日期) fix_case(统一大小写) fill_missing(标记缺失) remove_test(去测试数据)
```

## Phase 2 路线图（三 Agent 拆分）

Phase 1 验证后，拆分为 OpenClaw Multi-Agent：

| Agent | 角色 | 使用的 CLI 子命令 |
|-------|------|-------------------|
| Profiler | 数据画像 + 问题诊断 | `profile` |
| Planner | 策略规划 + 人工确认 | `list-ops` + LLM 推理 |
| Executor | 执行 + 验证 + 回滚 | `execute`, `validate`, `history` |

每个 Agent 在 `openclaw.json` 中配置独立的 agent，工具集隔离，session 隔离。
