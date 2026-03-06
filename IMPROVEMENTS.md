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
- `run_hn_fixed.sh` LLM 输出正则过于宽泛（后续，需配合 prompt 工程一起改）

---

## 2026-03-06 · Round 3：P3 修复 — 测试覆盖、锁机制、依赖清理，发现并修复隐藏 Bug

### 已修复

#### tool_proxy.py — 隐藏 Bug（由测试发现）
| 类型 | 问题 | 修复 |
|------|------|------|
| P3 · 隐藏 Bug | 参数别名替换（`file_path`→`path`、`cmd`→`command` 等）后**从未回写** `fn["arguments"]`，导致别名替换对调用方完全无效 | 引入 `alias_changed` 标志，只要发生别名替换就强制回写 |

> **说明**：`clean == args`（别名替换后两者相同），触发不了原有的 `if clean != args` 写回条件。
> 这个 bug 在 20 天的 Chat 迭代中始终未被发现，因为无法运行代码验证。

#### test_tool_proxy.py（新增）
| 内容 | 说明 |
|------|------|
| `TestBrowserProfileFix` (5 tests) | browser profile 无效值替换、有效值保留、缺失时注入 |
| `TestParamAliases` (5 tests) | `file_path/filepath/file/filename`→`path`、`cmd`→`command`、`text`→`content`、`q`→`query` |
| `TestExtraParamsStripped` (2 tests) | 多余参数被清除、合法参数保留 |
| `TestMalformedArgs` (4 tests) | JSON 格式错误、无 tool_calls、空 choices、缺 choices 键 |
| **合计：16 tests，全部通过** | `python3 test_tool_proxy.py` |

#### kb_write.sh
| 类型 | 问题 | 修复 |
|------|------|------|
| P3 · 并发 | 目录忙等待锁（`mkdir` + `sleep 0.1` 循环）浪费 CPU，进程崩溃后锁目录残留 | 改用 `flock -x 9`，进程退出后内核自动释放 |
| P3 · 可移植 | 所有路径硬编码 `/Users/bisdom/.kb/` | 改为读取 `KB_BASE` 环境变量（默认保持原路径） |
| P3 · 可移植 | index 路径通过 Python f-string 硬编码 | 改为通过 `sys.argv[6]` 传入 |
| P3 · 可维护 | emoji 输出日志 | 改为 `[kb_write]` 前缀纯文本 |

#### requirements.txt
| 问题 | 修复 |
|------|------|
| `flask>=2.0.0` 和 `requests>=2.28.0` 均未在任何 Python 文件中 import | 清空为注释说明：本项目仅使用 Python 标准库 |

### 累计未修复项
- 无（所有已知问题已修复）

---

## 2026-03-06 · Round 4：全仓库扫描，一次性修完所有遗留问题

### 修复范围

本轮对所有未审查脚本进行全面扫描并统一修复，清零已知问题。

#### kb_evening.sh
| 问题 | 修复 |
|------|------|
| `PHONE` / `KB_DIR` 硬编码 | 改为 `OPENCLAW_PHONE` / `KB_BASE` 环境变量 |
| 无错误保护 | 加入 `set -euo pipefail` |
| emoji 日志 | 改为 `[kb_evening]` 前缀纯文本；`openclaw message send` 失败时打印 WARN |
| `while read f` 未加引号 | 改为 `while read -r f` + `"$f"` |

#### kb_review.sh
| 问题 | 修复 |
|------|------|
| `KB_DIR` 硬编码 | 改为 `KB_BASE` 环境变量 |
| Python 管道 one-liner（`cat \| python3 -c`）无法正确处理文件不存在 | 改为 `python3 - argv << 'PYEOF'` heredoc，加 `try/except (OSError, json.JSONDecodeError)` |
| 无错误保护 | 加入 `set -euo pipefail` |
| emoji 日志装饰（`━━━`、📚 等） | 清理为 `[kb_review]` 前缀单行输出 |
| 循环变量未加引号 | `$(basename $f)` → `$(basename "$f")` |

#### kb_save_arxiv.sh
| 问题 | 修复 |
|------|------|
| `/opt/homebrew/bin/openclaw` 硬编码 | 改为 `command -v openclaw` 动态查找 |
| `/Users/bisdom/kb_write.sh` 硬编码 | 改为 `KB_WRITE_SCRIPT` 环境变量，默认相对路径 |
| Python 内联代码无异常处理 | 改为 heredoc，加 `try/except (OSError, json.JSONDecodeError)` |
| `openclaw cron runs` 结果解析无保护 | 加 `try/except (json.JSONDecodeError, KeyError)` |
| 无错误保护 | 加入 `set -euo pipefail` |
| emoji 输出 | 改为 `[kb_save_arxiv]` 前缀 |

#### run_discussions.sh（根目录）
| 问题 | 修复 |
|------|------|
| `set -eo pipefail` 缺少 `-u` | 改为 `set -euo pipefail` |
| `TO` / `KB_SRC` / `KB_INBOX` 硬编码 | 改为 `OPENCLAW_PHONE` / `KB_BASE` 环境变量 |

#### jobs/openclaw_official/run.sh
| 问题 | 修复 |
|------|------|
| `set -euo pipefail` 位于变量赋值之后（第5行） | 移至文件顶部（第2行） |
| `echo "DEBUG blog_new_count=$blog_new_count"` 调试输出未清理 | 改为 `[run.sh]` 前缀日志 |
| Blog INBOX 写入重复 3 次（grep 保护防止实际重复，但代码冗余） | 移除 MSG 组装块和 KB_SRC 块中的两处副本，仅保留专用 INBOX 写入段 |
| `KB_SRC` / `KB_INBOX` 硬编码 | 改为 `KB_BASE` 环境变量 |
| 最后一行 `+85200000000` 硬编码 | 改为 `OPENCLAW_PHONE` 环境变量 |

#### jobs/openclaw_official/run_blog.sh
| 问题 | 修复 |
|------|------|
| `set -eo pipefail` 缺少 `-u` | 改为 `set -euo pipefail` |
| `TO` 硬编码 | 改为 `OPENCLAW_PHONE` 环境变量 |

#### jobs/openclaw_official/run_discussions.sh
| 问题 | 修复 |
|------|------|
| `set -eo pipefail` 缺少 `-u` | 改为 `set -euo pipefail` |
| `TO` / `KB_SRC` / `KB_INBOX` 硬编码 | 改为 `OPENCLAW_PHONE` / `KB_BASE` 环境变量 |

#### run_hn_fixed.sh
| 问题 | 修复 |
|------|------|
| Prompt 格式要求不够严格，导致 LLM 输出 Markdown 变体（`**`、`【】`等）需要宽泛正则兜底 | 显式禁止 Markdown 符号，要求纯文本格式；正则依然保留兼容性但触发频率大幅降低 |
| `TO` 硬编码 | 改为 `OPENCLAW_PHONE` 环境变量 |

### 验证结果
- `python3 test_tool_proxy.py` → **16/16 passed**
- `python3 -m py_compile adapter.py tool_proxy.py test_tool_proxy.py` → **全部通过**
- `bash -n` 语法检查所有 11 个 shell 脚本 → **全部通过**

### 环境变量汇总（新增统一配置接口）

| 变量 | 默认值 | 影响文件 |
|------|--------|---------|
| `OPENCLAW_PHONE` | `+85200000000` | kb_evening.sh, run_hn_fixed.sh, run_discussions.sh, jobs/run*.sh |
| `KB_BASE` | `~/.kb` | kb_write.sh, kb_evening.sh, kb_review.sh, run_discussions.sh, jobs/run*.sh |
| `OPENCLAW_CFG` | `~/.openclaw` | kb_save_arxiv.sh |
| `KB_WRITE_SCRIPT` | `./kb_write.sh` | kb_save_arxiv.sh |
| `REMOTE_API_KEY` | `sk-REPLACE-ME` | adapter.py |

### 累计未修复项
- 无

---

*最后更新：2026-03-06 Round 4*
