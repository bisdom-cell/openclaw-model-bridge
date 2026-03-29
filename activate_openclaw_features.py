#!/usr/bin/env python3
"""
V30.5: 激活 OpenClaw 未充分利用的功能（安全版）
在 Mac Mini 上运行：python3 ~/openclaw-model-bridge/activate_openclaw_features.py

⚠️ 使用 `openclaw config set` CLI 修改配置（保留 secrets resolution），
   禁止 json.dump() 直接覆盖 openclaw.json（V30.4教训：会破坏 Gateway 内部一致性）。

变更内容：
1. research agent: 添加 sessions_spawn/sessions_send/sessions_history/agents_list/memory 工具
2. ops agent: 添加 web_fetch/memory_search/memory_get
3. Brave LLM-context 模式（提升 PA 搜索质量）
4. 消息去抖（防用户连发触发重复响应）
5. 日志脱敏（工具调用参数自动脱敏）
6. sandbox 限制（非主 agent 只读）
"""
import json
import os
import subprocess
import sys


def run_config_set(key, value, dry_run=False):
    """通过 openclaw config set 安全修改配置。"""
    cmd = ["openclaw", "config", "set", key, str(value)]
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return True
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"  ✅ {key} = {value}")
            return True
        else:
            print(f"  ❌ {key}: {result.stderr.strip() or result.stdout.strip()}")
            return False
    except FileNotFoundError:
        print("  ❌ openclaw CLI 未找到（确认已全局安装）")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ {key}: 超时")
        return False


def get_current_config():
    """读取当前 openclaw.json（只读，不修改）。"""
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: 无法读取 {config_path}: {e}")
        return {}


def check_agent_tools(config, agent_id, required_tools):
    """检查 agent 是否已有所需工具，返回缺失的工具列表。"""
    agents_list = config.get("agents", {}).get("list", [])
    for agent in agents_list:
        if agent.get("id") == agent_id:
            allow = agent.get("tools", {}).get("allow", [])
            return [t for t in required_tools if t not in allow]
    return required_tools  # agent 不存在，全部缺失


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("🔍 Dry-run 模式（不实际修改）\n")

    config = get_current_config()
    changes = []
    failures = []

    # ── 1. Agent 工具配置（需要 openclaw config set 的 JSON path 语法） ──
    print("📋 Agent 工具配置:")

    research_missing = check_agent_tools(config, "research", [
        "sessions_spawn", "sessions_send", "sessions_history",
        "agents_list", "memory_search", "memory_get"
    ])
    if research_missing:
        # openclaw config set 对数组的支持有限，需要用 JSON path
        # 实际操作：通过完整的 tools.allow 数组设置
        current_allow = []
        for agent in config.get("agents", {}).get("list", []):
            if agent.get("id") == "research":
                current_allow = agent.get("tools", {}).get("allow", [])
                break
        new_allow = current_allow + research_missing
        print(f"  research agent 缺少: {', '.join(research_missing)}")
        print(f"  ⚠️ 需要手动执行:")
        for t in research_missing:
            print(f"     openclaw config set agents.list[id=research].tools.allow.+ \"{t}\"")
        changes.append(f"research agent +{', '.join(research_missing)}")
    else:
        print("  research agent: 工具已完整 ✅")

    ops_missing = check_agent_tools(config, "ops", [
        "web_fetch", "memory_search", "memory_get"
    ])
    if ops_missing:
        print(f"  ops agent 缺少: {', '.join(ops_missing)}")
        print(f"  ⚠️ 需要手动执行:")
        for t in ops_missing:
            print(f"     openclaw config set agents.list[id=ops].tools.allow.+ \"{t}\"")
        changes.append(f"ops agent +{', '.join(ops_missing)}")
    else:
        print("  ops agent: 工具已完整 ✅")

    # ── 2. Brave LLM-context 模式 ──
    print("\n🔍 Brave 搜索优化:")
    brave_mode = config.get("tools", {}).get("web", {}).get("search", {}).get("brave", {}).get("mode", "")
    if brave_mode != "llm-context":
        if run_config_set("tools.web.search.brave.mode", "llm-context", dry_run):
            changes.append("Brave mode = llm-context")
        else:
            failures.append("Brave mode")
    else:
        print("  已配置 llm-context ✅")

    # ── 3. 消息去抖 ──
    print("\n⏱️ 消息去抖:")
    debounce = config.get("messages", {}).get("inboundDebounce", {}).get("ms", 0)
    if debounce < 1000:
        if run_config_set("messages.inboundDebounce.ms", "1500", dry_run):
            changes.append("inboundDebounce = 1500ms")
        else:
            failures.append("inboundDebounce")
    else:
        print(f"  已配置 {debounce}ms ✅")

    # ── 4. 日志脱敏 ──
    print("\n🔒 日志脱敏:")
    redact = config.get("logging", {}).get("redactSensitive", "")
    if redact != "tools":
        if run_config_set("logging.redactSensitive", "tools", dry_run):
            changes.append("redactSensitive = tools")
        else:
            failures.append("redactSensitive")
    else:
        print("  已配置 ✅")

    # ── 5. Sandbox 限制 ──
    print("\n🛡️ Sandbox 限制:")
    sandbox = config.get("agents", {}).get("defaults", {}).get("sandbox", {})
    if sandbox.get("mode") != "non-main":
        if run_config_set("agents.defaults.sandbox.mode", "non-main", dry_run):
            changes.append("sandbox.mode = non-main")
        else:
            failures.append("sandbox.mode")
    else:
        print("  sandbox.mode 已配置 ✅")

    if sandbox.get("workspaceAccess") != "ro":
        if run_config_set("agents.defaults.sandbox.workspaceAccess", "ro", dry_run):
            changes.append("sandbox.workspaceAccess = ro")
        else:
            failures.append("sandbox.workspaceAccess")
    else:
        print("  sandbox.workspaceAccess 已配置 ✅")

    # ── 总结 ──
    print("\n" + "=" * 50)
    if changes:
        print(f"✅ 变更 {len(changes)} 项:")
        for c in changes:
            print(f"   • {c}")
    if failures:
        print(f"❌ 失败 {len(failures)} 项: {', '.join(failures)}")
    if not changes and not failures:
        print("✅ 所有配置已是最新，无需变更")

    if changes:
        print(f"\n📋 Gateway 会自动热重载，通常无需 restart。")
        print(f"   如 PA 行为异常，执行: bash ~/restart.sh")

    return 1 if failures else 0


if __name__ == "__main__":
    exit(main())
