# OpenClaw Model Bridge — 改进归档

> 本文件由 Claude Code 自动维护，记录每次代码审查与修复的完整历史。
> 每次修复后自动更新，作为持续迭代的参考基准。

---

## 2026-03-06 · Round 1：初次代码质量审查与 P0/P1 修复

### 背景
仓库由 Claude Chat（非 Claude Code）经过约 20 天对话积累而成。两者使用相同基础模型，
但 Claude Chat 无法执行代码、搜索文件系统或验证运行结果，导致若干可避免的 bug 积累。

### 已修复

#### adapter.py
| 类型 | 问题 | 修复 |
|------|------|------|
| P0 · 崩溃 Bug | 缺少 `import os`，运行时必然 `NameError` | 补充到第 2 行 import 列表 |
| P1 · 异常处理 | 裸 `except:` 吞掉所有异常（含 SystemExit）| 改为 `except (json.JSONDecodeError, ValueError) as e` + 记录日志 |

#### tool_proxy.py
| 类型 | 问题 | 修复 |
|------|------|------|
| P0 · 日志误导 | `FIX: browser profile -> 'chrome'` 但实际写入 `'openclaw'` | 日志修正为 `-> 'openclaw'` |
| P0 · 隐私泄露 | 工具调用参数前 200 字节直接打印到日志 | 改为只打印工具名 + 字节数 |
| P1 · 异常处理 | `fix_tool_args()` 中裸 `except: args = {}` | 改为 `except (json.JSONDecodeError, ValueError) as e` + log |
| P1 · 异常处理 | 请求预处理裸 `except: pass`，静默吃掉错误 | 改为具体异常类型 + 错误日志 |
| P1 · 截断通知 | 消息截断只打印 `Truncated N msgs`，信息不足 | 改为 `WARN: Truncated N old messages (X->Y msgs, ~ZKB). Oldest context may be lost.` |

#### restart.sh
| 类型 | 问题 | 修复 |
|------|------|------|
| P1 · 崩溃 Bug | `kill $(lsof -ti :18789)` 在无进程时退出码非零导致脚本中断 | 改为 `lsof ... \| xargs kill ... \|\| true` |
| P1 · 健壮性 | 无错误保护模式 | 加入 `set -euo pipefail` |
| P1 · 可维护性 | emoji 日志无法机器解析（`🔧 Starting...`）| 改为 `[restart] Starting...` 格式 |

### 已知遗留问题（待后续修复）
- `health_check.sh`：Python heredoc 中多处裸 `except`（第 35、54 行）
- `health_check.sh`：`PHONE` 和 openclaw 路径硬编码，不可移植
- `run_hn_fixed.sh`：Shell 循环中每条数据调用 5 次 Python 子进程，性能低
- `run_hn_fixed.sh`：Python 段多处裸 `except: continue/pass`
- 全局：无单元测试
- 全局：`requirements.txt` 中 `requests` 未在 Python 代码中使用（冗余依赖）

---

## 2026-03-06 · Round 2：P2 修复 — 性能、异常处理、脚本健壮性

### 已修复

#### run_hn_fixed.sh
| 类型 | 问题 | 修复 |
|------|------|------|
| P2 · 性能 | Shell 循环中每条数据重复调用 5 次 Python 子进程解析 JSON | 合并为单次 Python 调用，一次性解析所有字段 |
| P2 · 异常处理 | Python 段裸 `except: continue`（JSONL 解析）| 改为 `except (json.JSONDecodeError, ValueError)` |
| P2 · 异常处理 | LLM 原始日志写入裸 `except: pass` | 改为 `except OSError as e` + 警告日志 |
| P2 · 异常处理 | 解析成功率写入裸 `except: pass` | 同上 |

#### health_check.sh
| 类型 | 问题 | 修复 |
|------|------|------|
| P2 · 异常处理 | Python heredoc 中 `except:` 吞掉任务读取错误 | 改为 `except (OSError, json.JSONDecodeError, KeyError) as e` + 打印错误 |
| P2 · 异常处理 | `subprocess.run` 结果解析裸 `except` | 改为具体异常类型 |
| P2 · 配置 | `PHONE="+85200000000"` 硬编码 | 改为读取 `OPENCLAW_PHONE` 环境变量，保留默认值 |
| P2 · 配置 | `/opt/homebrew/bin/openclaw` 硬编码 macOS 路径 | 改为优先使用 `$PATH` 中的 `openclaw` |

### 累计未修复项
- 无单元测试（P3）
- `requirements.txt` 冗余依赖 `requests`（P3）
- `kb_write.sh` 忙等待锁机制（P3）
- `run_hn_fixed.sh` LLM 输出正则过于宽泛（P3，需配合 prompt 工程一起改）

---

*最后更新：2026-03-06 Round 2*
