# OpenClaw #48703：一个让 WhatsApp 推送静默失败 10 天的 Bundler Bug

> 复盘一次从"系统正常"到发现 Vite code-splitting 导致全局状态分裂的排查过程。希望能帮到同样踩坑的人。

---

## 背景

我用 OpenClaw（开源 AI 助手框架）搭了一套 WhatsApp 自动化系统，跑在 Mac Mini 上：

- ArXiv 论文监控，每 3 小时推送摘要
- Hacker News 热帖抓取，每 3 小时推送
- 货代数据监控，每天 3 次 LLM 分析 + 推送
- KB 知识库回顾，每周深度分析 + 推送
- 健康周报、Issue 监控……

所有推送都通过 `openclaw message send` CLI 命令发送 WhatsApp 消息。

**3 月 13 日升级到 OpenClaw v2026.3.13 后，所有定时推送全部静默失败——但我 10 天后才发现。**

## 为什么 10 天才发现？

因为"看起来一切正常"：

| 功能 | 状态 | 原因 |
|------|------|------|
| WhatsApp 自动回复 | ✅ 正常 | 走的是 socket 直接引用，不经过 listeners Map |
| `openclaw channels status` | ✅ 全绿 | 显示 connected、linked、running |
| Gateway 健康检查 | ✅ 正常 | HTTP 200 |
| Cron 任务执行 | ✅ 正常运行 | 脚本本身没报错 |
| WhatsApp 推送 | ❌ 全部失败 | 错误被 `2>/dev/null` 或 `|| true` 吞掉了 |

自动回复正常 + 健康检查正常 = **完美的假象**。推送失败的错误被脚本里的错误处理静默消化了。

## 发现问题

某天手动测试主动发送时：

```bash
$ openclaw message send --target "+852xxxxxxxx" --message "test"
GatewayClientRequestError: Error: No active WhatsApp Web listener (account: default).
```

这才发现问题。而且这个错误在 `openclaw channels status` 中完全不会体现——因为 WhatsApp session 确实在线，只是主动发送的代码路径断了。

## 根因分析

查到 GitHub Issue [#48703](https://github.com/openclaw/openclaw/issues/48703)，根因非常经典：

### Vite/Rollup Code-Splitting 导致全局状态分裂

OpenClaw 用 Vite 打包，bundler 把一个共享的 `listeners` Map 拆成了 **多个 chunk 文件**，每个 chunk 都有自己独立的 `new Map()` 实例：

```javascript
// 文件 A (channel connect 写入)
const listeners = /* @__PURE__ */ new Map();  // ← Map A
function setActiveWebListener(id, listener) {
  listeners.set(id, listener);  // 写入 Map A
}

// 文件 B (message send 读取)
const listeners = /* @__PURE__ */ new Map();  // ← Map B（完全不同的实例！）
function requireActiveWebListener(id) {
  return listeners.get(id);  // 从 Map B 读，永远为空
}
```

WhatsApp 连接时 `setActiveWebListener()` 写入了 Map A，但发送消息时 `requireActiveWebListener()` 从 Map B 读——Map B 永远是空的。

### 为什么自动回复不受影响？

自动回复走的是 `web-auto-reply` 模块，它持有 WebSocket 的闭包引用，直接调用 `msg.reply()`，**完全不经过 `requireActiveWebListener()`**。两条代码路径：

```
收消息 → auto-reply → msg.reply() [直接socket引用] → ✅
主动发送 → message tool → requireActiveWebListener() → Map B(空) → ❌
```

## 修复

### 定位所有副本

```bash
$ grep -rn "const listeners.*new Map" /opt/homebrew/lib/node_modules/openclaw/dist/ \
    --include="*.js" | grep -v ".bak"
```

在 v2026.3.13 中找到了 **7 个独立的 Map 副本**，分布在：

- `reply-Bm8VrLQh.js`
- `model-selection-46xMp11W.js`
- `model-selection-CU2b7bN6.js`
- `auth-profiles-DRjqKE3G.js`
- `auth-profiles-DDVivXkv.js`
- `discord-CcCLMjHw.js`
- `plugin-sdk/thread-bindings-SYAnWHuW.js`
- `entry.js`

### 应用 globalThis Singleton 补丁

按 #48703 建议的修复方案，把所有副本改为共享同一个 `globalThis` 上的 Map 实例：

```bash
sudo sed -i.bak \
  's|const listeners = /\* @__PURE__ \*/ new Map()|const listeners = globalThis.__openclaw_web_listeners__ ??= /* @__PURE__ */ new Map()|g' \
  /opt/homebrew/lib/node_modules/openclaw/dist/reply-Bm8VrLQh.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/model-selection-46xMp11W.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/model-selection-CU2b7bN6.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/auth-profiles-DRjqKE3G.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/auth-profiles-DDVivXkv.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/discord-CcCLMjHw.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/plugin-sdk/thread-bindings-SYAnWHuW.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/entry.js
```

修复原理：

```javascript
// 修复后：所有 chunk 共享同一个 Map
const listeners = globalThis.__openclaw_web_listeners__ ??= /* @__PURE__ */ new Map();
// 第一个加载的 chunk 创建 Map，后续 chunk 复用同一个实例
```

### 验证

```bash
$ bash ~/restart.sh
$ openclaw message send --target "+852xxxxxxxx" --message "hotfix test"
✅ Sent via gateway (whatsapp). Message ID: 3EB0473CEB50278758D770
```

10 天的静默失败，一条 sed 命令修复。

## 经验教训

### 1. `/* @__PURE__ */` 是 Bundler 的陷阱

`/* @__PURE__ */` 告诉 bundler"这个表达式没有副作用，可以 tree-shake"。但它也意味着 bundler 可以**自由复制**这个表达式到多个 chunk 中。对于无状态的工具函数没问题，但对于 **需要共享的全局状态**（如 Map、Set、单例），这是灾难性的。

**教训**：在使用 Vite/Rollup/Webpack 等 bundler 时，全局状态要么：
- 放在 `globalThis` 上
- 使用 `manualChunks` 确保不被拆分
- 放在独立的 entry module 中

### 2. 静默失败是最危险的失败

推送脚本里的 `2>/dev/null` 和 `|| true` 设计初衷是防止单次推送失败影响整个脚本流程。但它同时也掩盖了系统性故障。

**改进**：关键操作的错误应该被记录到日志，而不是直接丢弃。可以用：
```bash
openclaw message send ... 2>"$SEND_ERR" || {
  echo "$(date) SEND_FAILED: $(cat $SEND_ERR)" >> ~/push_errors.log
}
```

### 3. 健康检查的盲区

`openclaw channels status` 只检查 session 状态（linked、connected），不验证完整的发送路径。"看起来连接正常"≠"实际能发消息"。

**改进**：端到端健康检查应该包含一次真实的发送验证（比如发一个零宽字符或发给自己）。

### 4. 升级后要测试完整路径

升级 OpenClaw 后我只测了自动回复（收到消息→AI回复），没测主动发送。两条路径的代码完全不同，一个正常不代表另一个也正常。

**改进**：升级后的 checklist 应包含所有关键路径的验证。

## 适用范围

这个 bug 影响 OpenClaw v2026.3.13 的所有使用 `openclaw message send` 的场景：

- ✅ 自动回复正常（不受影响）
- ❌ `openclaw message send` CLI 命令
- ❌ Cron 定时推送
- ❌ Agent 内部调用 `message` tool
- ❌ 任何通过 Gateway RPC 发起的主动发送

如果你在用 OpenClaw + WhatsApp 并且发现主动推送失败，大概率就是这个问题。上面的 sed 命令可以直接修复。

## 补丁脚本

为了方便重装后重新打补丁，可以保存以下脚本：

```bash
#!/bin/bash
# ~/patch_48703.sh — Hotfix for OpenClaw #48703
# 使用方法：npm install -g openclaw@2026.3.13 后运行此脚本
sudo sed -i.bak \
  's|const listeners = /\* @__PURE__ \*/ new Map()|const listeners = globalThis.__openclaw_web_listeners__ ??= /* @__PURE__ */ new Map()|g' \
  /opt/homebrew/lib/node_modules/openclaw/dist/*.js \
  /opt/homebrew/lib/node_modules/openclaw/dist/plugin-sdk/*.js
echo "Patch #48703 applied"
# 验证
UNPATCHED=$(grep -rn 'const listeners = /\* @__PURE__ \*/ new Map()' \
  /opt/homebrew/lib/node_modules/openclaw/dist/ --include="*.js" | grep -v ".bak" | wc -l)
echo "Remaining unpatched: $UNPATCHED (should be 0)"
```

---

*写于 2026-03-23。如果这篇文章帮到了你，欢迎点赞让更多人看到。*
