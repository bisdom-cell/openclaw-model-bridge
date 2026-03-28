#!/usr/bin/env python3
"""
V30.4: 激活 OpenClaw 未充分利用的功能
一次性脚本，在 Mac Mini 上运行：python3 ~/openclaw-model-bridge/activate_openclaw_features.py

变更内容：
1. research agent: 添加 sessions_spawn/sessions_send/sessions_history/agents_list 工具
2. ops agent: 添加 web_fetch（localhost健康检查）、memory_search/memory_get
3. 全局: 启用 redactSensitive + sandbox.mode=readonly
4. 备份原配置
"""
import json
import os
import shutil
from datetime import datetime

CONFIG_PATH = os.path.expanduser("~/.openclaw/openclaw.json")
BACKUP_DIR = os.path.expanduser("~/.openclaw/config_backups")

def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found")
        return 1

    # ── 备份 ──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"openclaw_{ts}.json")
    shutil.copy2(CONFIG_PATH, backup_path)
    print(f"✅ 备份: {backup_path}")

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    changes = []

    # ── 1. research agent: 添加 session 管理工具 ──
    agents_list = config.get("agents", {}).get("list", [])
    for agent in agents_list:
        if agent.get("id") == "research":
            tools = agent.setdefault("tools", {})
            allow = tools.get("allow", [])
            new_tools = ["sessions_spawn", "sessions_send", "sessions_history", "agents_list"]
            added = []
            for t in new_tools:
                if t not in allow:
                    allow.append(t)
                    added.append(t)
            if added:
                tools["allow"] = allow
                changes.append(f"research agent +tools: {', '.join(added)}")

            # 确保 memory 工具在允许列表中
            for mt in ["memory_search", "memory_get"]:
                if mt not in allow:
                    allow.append(mt)
                    changes.append(f"research agent +{mt}")

        elif agent.get("id") == "ops":
            tools = agent.setdefault("tools", {})
            allow = tools.get("allow", [])
            new_tools = ["web_fetch", "memory_search", "memory_get"]
            added = []
            for t in new_tools:
                if t not in allow:
                    allow.append(t)
                    added.append(t)
            if added:
                tools["allow"] = allow
                changes.append(f"ops agent +tools: {', '.join(added)}")

    # ── 2. 全局安全配置 ──
    # redactSensitive
    logging = config.setdefault("logging", {})
    if logging.get("redactSensitive") != "tools":
        logging["redactSensitive"] = "tools"
        changes.append("logging.redactSensitive = tools")

    # sandbox (defaults 级别)
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    sandbox = defaults.setdefault("sandbox", {})
    # 合法值: mode=off|non-main|all, workspaceAccess=none|ro|rw
    if sandbox.get("mode") != "non-main":
        sandbox["mode"] = "non-main"
        changes.append("sandbox.mode = non-main")
    if sandbox.get("workspaceAccess") != "ro":
        sandbox["workspaceAccess"] = "ro"
        changes.append("sandbox.workspaceAccess = ro")

    if not changes:
        print("⚠️  无需变更，配置已是最新")
        return 0

    # ── 写入 ──
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)

    print(f"\n✅ 已更新 {CONFIG_PATH}:")
    for c in changes:
        print(f"   • {c}")

    print(f"\n📋 下一步:")
    print(f"   1. bash ~/restart.sh  (重启让配置生效)")
    print(f"   2. 清空 session: echo '{{\"sessions\":[]}}' > ~/.openclaw/agents/research/sessions/sessions.json")
    print(f"   3. WhatsApp 测试: '你还记得我们之前讨论过什么吗？'")
    print(f"\n⚠️  回滚: cp {backup_path} {CONFIG_PATH} && bash ~/restart.sh")
    return 0

if __name__ == "__main__":
    exit(main())
