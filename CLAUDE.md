# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v27（2026-03-06）

---

## 项目简介

将任意大模型（当前：Qwen3-235B）接入 OpenClaw（WhatsApp AI助手框架）的双层中间件。
运行于 Mac Mini (macOS)，用户：bisdom。

## 架构

```
WhatsApp <-> OpenClaw Gateway (18789) <-> Tool Proxy (5002) <-> Adapter (5001) <-> 远程GPU API
```

| 组件 | 端口 | 文件 | 功能 |
|------|------|------|------|
| OpenClaw Gateway | 18789 | npm全局安装 | WhatsApp接入、工具执行 |
| Tool Proxy | 5002 | `~/tool_proxy.py` + `~/proxy_filters.py` | 工具过滤(24→12)、Schema简化、SSE转换、截断 |
| Adapter | 5001 | `~/adapter.py` | 转发远程GPU、认证、参数过滤 |
| 远程GPU | — | hkagentx.hkopenlab.com | Qwen3-235B推理 |

## 关键文件（本仓库）

| 文件 | 用途 |
|------|------|
| `tool_proxy.py` | HTTP 层（收发请求、日志） |
| `proxy_filters.py` | **V27新增** 策略层（过滤、修复、截断、SSE转换），纯函数无网络依赖 |
| `adapter.py` | API适配层（认证用环境变量 `$REMOTE_API_KEY`） |
| `jobs_registry.yaml` | **V27新增** 统一任务注册表（system + openclaw 双 cron） |
| `check_registry.py` | **V27新增** 注册表校验脚本 |
| `ROLLBACK.md` | **V27新增** 回滚指南（30秒恢复到V26） |
| `upgrade_openclaw.sh` | Gateway升级SOP脚本（必须SSH直连执行，禁止WhatsApp触发） |
| `restart.sh` | 一键重启 Proxy + Adapter |
| `health_check.sh` | 每周健康周报脚本（V27: +JSON输出） |
| `kb_write.sh` | KB写入脚本（含目录锁+原子写） |
| `kb_review.sh` | KB跨笔记回顾脚本 |
| `kb_save_arxiv.sh` | ArXiv监控结果写入KB + rsync备份 |
| `test_tool_proxy.py` | proxy_filters 单测（28个用例） |
| `docs/config.md` | 完整系统配置文档（含所有历史变更） |
| `docs/GUIDE.md` | 完整中英文集成指南 |

## V27 变更摘要

1. **Proxy 拆层**：`tool_proxy.py`（HTTP层）+ `proxy_filters.py`（策略层），策略可独立测试
2. **任务注册表**：`jobs_registry.yaml` 统一登记所有 system/openclaw 定时任务
3. **注册表校验**：`check_registry.py` 自动检查 ID 唯一、路径存在、字段完整
4. **Health JSON**：`health_check.sh` 同时输出 `~/health_status.json` 供自动化消费
5. **回滚机制**：`git tag v26-snapshot` + `ROLLBACK.md`，30秒可回退

## 常用命令

```bash
# 启动服务
nohup python3 ~/adapter.py > ~/adapter.log 2>&1 &
nohup python3 ~/tool_proxy.py > ~/tool_proxy.log 2>&1 &

# 健康检查
curl http://localhost:5002/health

# 一键重启
bash ~/restart.sh

# 运行单测
python3 test_tool_proxy.py

# 校验任务注册表
python3 check_registry.py

# 查询远端当前模型ID
curl -s https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer $REMOTE_API_KEY" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'Qwen3' in m['id']]"

# GitHub push前安全扫描（必须全部为空才允许push）
grep -r "sk-[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
grep -r "BSA[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
```

## 关键规则

### 模型ID规则
| 位置 | 格式 |
|------|------|
| `adapter.py` / `tool_proxy.py` | 裸ID（无前缀） |
| `openclaw.json` agents.defaults.model.primary | **必须带 `qwen-local/` 前缀** |
| `jobs.json` payload.model | **不指定**（继承默认值） |

### 硬性限制
- 工具数量 <= 12（超出导致模型混乱）
- 每任务工具调用 <= 2次（超出超时风险指数级上升）
- 请求体 <= 200KB（硬限制280KB，留buffer）
- `--thinking` 合法值：`off, minimal, low, medium, high, adaptive`（**禁止用 `none`**，这是v26修复的bug #92）

### 双 Cron 归属规则（V27新增）

| 调度器 | 管理方式 | 是否经过 LLM | 登记位置 |
|--------|----------|-------------|----------|
| `system` | macOS `crontab -e` | 否 | `jobs_registry.yaml` scheduler=system |
| `openclaw` | `openclaw cron add` | 是 | `jobs_registry.yaml` scheduler=openclaw |

**原则**：确定性脚本（清理、备份、抓取）用 `system`；需要 LLM 理解/生成的用 `openclaw`。
**新增任务必须先登记到 `jobs_registry.yaml`，运行 `python3 check_registry.py` 通过后才能注册 cron。**

### 安全规则（GitHub push前强制）
- API Key 必须通过环境变量：`os.environ.get("REMOTE_API_KEY")`
- 配置文档（含真实手机号/密钥）永不入库（已在 .gitignore）
- 公开仓库手机号统一用 `+85200000000` 占位

## 工作原则（精简版）

1. **每次开始先读 `docs/config.md`** — 获取完整系统状态和历史踩坑
2. **测试先于注册** — 新脚本必须手动验证后才能注册cron
3. **任务先登记** — 新增定时任务必须先写入 `jobs_registry.yaml` 并校验通过
4. **根因定位** — 多任务同时失败 → 第一反应检查远端模型ID
5. **push前必扫描** — 见上方安全扫描命令
6. **macOS sed禁用OR语法** — 用Python替代（`\|` 在BSD sed不支持）
7. **回滚优先** — 线上故障 → 先 `git checkout v26-snapshot` 恢复，再排查

## 当前待办（v27遗留）

| 优先级 | 任务 |
|--------|------|
| 低 | 货代Watcher V2：ImportYeti手动查询SOP |
| 低 | 货代Watcher V3：Bing News API替代GoogleNews |
| ✅ | Blog中文标题升级为LLM动态生成 |
| ✅ | WhatsApp target号码统一为 OPENCLAW_PHONE |
| 低 | 探索Claude/GPT-4o替换Qwen3 |

## 远程连接（本机）

```bash
ssh bisdom@10.102.0.217      # 办公室内网
ssh bisdom@10.120.230.23     # ZeroTier（回家后）
```

## Git 仓库

```
git@github.com:bisdom-cell/openclaw-model-bridge.git
```
Remote 已改为 SSH（v25修复HTTPS认证失败）。
回滚标签：`v26-snapshot`（V27变更前的完整快照）。
