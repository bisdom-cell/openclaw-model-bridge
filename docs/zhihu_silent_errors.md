# Cron + LLM 的静默失败陷阱：一个反复发作的 Bug 是如何被根治的

> 这是一个真实的生产环境案例。一套运行在 Mac Mini 上的 AI 助手系统，有 7 个定时任务（Watcher）负责抓取新闻、论文、社区动态并推送到 WhatsApp。其中只有 HN（Hacker News）能正常推送，其余全部静默失败——没有报错，没有告警，看起来一切正常。

## 一、现象

系统架构：

```
WhatsApp ↔ OpenClaw Gateway (18789) ↔ Tool Proxy (5002) ↔ Adapter (5001) ↔ 远程 LLM API
```

7 个 Watcher 由系统 crontab 触发，分别监控 ArXiv 论文、GitHub Discussions、OpenClaw 官方博客/版本、货代新闻、HN 头版。

**Bug 表现**：除 HN 外，所有 Watcher 从未推送过任何消息。没有报错日志，cron 显示正常退出。

## 二、根因拆解：三个独立问题叠加

### 问题 1：`openclaw agent` 的工具注入陷阱

这是最致命的问题。

多个脚本使用 `openclaw agent` 命令调用 LLM 做文本翻译/分析——看似合理，但 Gateway 会自动向请求注入 12 个工具定义（web_search、browser 等）。LLM 拿到工具后，经常无视 prompt 指令，疯狂调用 web_search，直到 600 秒超时。

```bash
# 错误写法（Gateway 注入工具 → LLM 死循环）
ENRICH="$(openclaw agent --message "$PROMPT" --thinking minimal 2>/dev/null || true)"

# 正确写法（直接调 API，不含 tools 字段 → 纯推理）
ENRICH="$(curl -sS --max-time 30 http://localhost:5001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"any","messages":[{"role":"user","content":"..."}],"max_tokens":200}' \
  | jq -r '.choices[0].message.content // empty')"
```

**关键认知**：纯推理任务（翻译、摘要、评级）不需要工具。走 Gateway 只会引入不可控因素。直接 curl API 端点，请求体不含 `tools` 字段，彻底杜绝工具注入。

更隐蔽的是，HN 脚本也用了 `openclaw agent`，但藏在 Python heredoc 的 `subprocess.run()` 里：

```python
# 藏在 Python 里的 openclaw agent 调用，grep "openclaw agent" *.sh 找不到
result = subprocess.run(
    ["openclaw", "agent", "--message", prompt, "--thinking", "minimal"],
    capture_output=True, text=True, timeout=200
)
```

HN 之所以"能工作"，只是因为它的翻译 prompt 恰好不太触发工具调用——但这不是可靠的，只是运气。

### 问题 2：`2>/dev/null || true` 的滥用

这个组合在 Shell 脚本中极其常见，本意是"忽略非关键错误"。问题在于它被无差别地用在了**关键路径**上：

```bash
# 场景 A：安全的——SSD 可能未挂载，备份失败不影响主流程
rsync -a "$HOME/.kb/" "/Volumes/SSD/KB/" 2>/dev/null || true

# 场景 B：致命的——HTML 解析失败被静默吞掉
python3 parse_blog.py "$HTML" > "$OUTPUT" 2>/dev/null || true
# 如果 parse 崩溃，$OUTPUT 为空 → count=0 → "暂无新内容" → 正常退出
# 用户永远不知道解析器坏了
```

场景 B 的后果：脚本每小时运行一次，每次"成功"退出，但永远不推送内容。日志里只有 `"no new posts."`——看起来完全正常。

**解决方案**：区分"允许失败"和"必须成功"的操作。

```bash
# 修复后：错误写入日志，不再静默
if ! python3 parse_blog.py "$HTML" > "$OUTPUT" 2>"$CACHE/parse.err"; then
    log "⚠️ parse_blog.py 失败: $(head -3 "$CACHE/parse.err")"
fi
```

### 问题 3：cron 环境的 PATH 缺失

macOS cron 的环境变量极其精简，`PATH` 通常只有 `/usr/bin:/bin`。Homebrew 安装的 `python3`、`jq`、`openclaw` 等命令全在 `/opt/homebrew/bin/`，cron 里直接找不到。

crontab 虽然用了 `bash -lc` 加载登录 profile，但用户的 shell 是 zsh，`~/.zshrc` 里的 PATH 设置对 `bash -l` 不生效。

**解决方案**：所有 cron 脚本首行显式声明 PATH，不依赖任何 profile：

```bash
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
```

## 三、为什么 Bug 反复发作？

这个 Bug 不是一次性的，而是在几个月内被"修复"了至少 3 次，每次只修一个脚本。根本原因：

1. **没有统一规范**——每个脚本独立开发，"方便"的 `openclaw agent` 被复制粘贴到新脚本
2. **没有全局审计**——每次只看出问题的那个脚本，不检查其他脚本是否有同样问题
3. **错误不可见**——`2>/dev/null || true` 让所有症状消失，无法通过日志发现问题
4. **部署遗漏**——自动部署脚本的文件映射表漏掉了 ArXiv 脚本，修了也部署不上去

## 四、根治方案

最终的修复不是"再修一个脚本"，而是一次系统性治理：

| 措施 | 覆盖范围 | 效果 |
|------|---------|------|
| 全仓库替换 `openclaw agent` → 直接 `curl` API | 7 个脚本 | 零工具注入风险 |
| 所有 cron 脚本加 `export PATH` | 7 个脚本 | 不依赖 profile |
| 关键路径 stderr 写日志文件 | 4 个 LLM 调用点 | 错误可追溯 |
| fallback 时打 WARN 日志 | 4 个 LLM 调用点 | 降级可见 |
| 自动部署 FILE_MAP 补全 | 1 处遗漏 | 部署闭环 |

验证方法：
```bash
# 确认零 openclaw agent 实际调用
grep -r "openclaw.*agent" *.sh jobs/**/*.sh | grep -v "^#"  # 应全部为注释

# 确认所有脚本有 PATH
head -5 *.sh jobs/**/*.sh | grep "export PATH"
```

## 五、经验总结

### 给 Cron + LLM 系统的建议

1. **LLM 调用要走最短路径**——不需要工具就别过 Gateway/Agent 框架。直接 HTTP 调 API 端点，控制请求体里有什么。
2. **`2>/dev/null || true` 只用于非关键操作**——文件备份、目录创建、进程查找。LLM 调用、数据解析、消息推送的错误必须可见。
3. **cron 脚本不信任环境**——显式 `export PATH`，不依赖 login shell profile。
4. **修一个 Bug 时审计所有同类代码**——如果一个脚本有问题，检查所有脚本是否有相同模式。
5. **自动部署系统必须覆盖所有文件**——新增脚本时同步更新部署映射表。

### `2>/dev/null || true` 决策树

```
这个操作失败时，用户需要知道吗？
├── 是 → 不用 2>/dev/null || true，记录错误
│   ├── LLM 调用 → stderr 写日志文件，fallback 时打 WARN
│   ├── 数据解析 → 检查返回码，失败时 log()
│   └── 消息推送 → 检查返回码，失败时 log()
└── 否 → 可以用
    ├── 备份到外挂 SSD（SSD 可能未挂载）
    ├── grep 去重检查（文件可能不存在）
    └── kill 进程（进程可能已退出）
```

---

*这个案例的核心教训：静默失败的危害远大于 100 次报错。当你的系统"一切正常"但不产出结果时，第一反应应该是——错误被藏起来了。*
