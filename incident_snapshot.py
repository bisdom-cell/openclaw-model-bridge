#!/usr/bin/env python3
"""
incident_snapshot.py — 故障快照机制（V32: P0-4）

故障时自动收集 proxy.log 尾部 + adapter.log + 最近请求 + 系统状态，
写入 ~/.kb/incidents/<timestamp>.json。

用法：
  # 自动模式：由 proxy_stats 连续错误触发
  python3 incident_snapshot.py --auto "连续3次502错误"

  # 手动模式：主动采集当前系统状态
  python3 incident_snapshot.py --manual "用户报告响应超时"

  # 列出最近事件
  python3 incident_snapshot.py --list

  # 清理旧快照（保留最近 N 个）
  python3 incident_snapshot.py --cleanup
"""
import glob
import json
import os
import subprocess
import sys
import time

from config_loader import load_config

cfg = load_config()
inc_cfg = cfg.get("incidents", {})
SNAPSHOT_DIR = os.path.expanduser(inc_cfg.get("snapshot_dir", "~/.kb/incidents"))
LOG_LINES = inc_cfg.get("snapshot_log_lines", 100)
MAX_SNAPSHOTS = inc_cfg.get("max_snapshots", 50)

# 日志文件位置（Mac Mini 运行时路径）
LOG_FILES = {
    "proxy": os.path.expanduser("~/tool_proxy.log"),
    "adapter": os.path.expanduser("~/adapter.log"),
    "gateway": os.path.expanduser("~/openclaw-gateway.log"),
}

STATS_FILE = os.path.expanduser("~/proxy_stats.json")


def _tail_file(path, lines=100):
    """读取文件最后 N 行"""
    if not os.path.exists(path):
        return f"[file not found: {path}]"
    try:
        with open(path, "rb") as f:
            # 从文件末尾读取
            f.seek(0, 2)
            size = f.tell()
            # 读取最多 64KB 来找最后 N 行
            read_size = min(size, 65536)
            f.seek(max(0, size - read_size))
            content = f.read().decode("utf-8", errors="replace")
        tail = content.split("\n")[-lines:]
        return "\n".join(tail)
    except OSError as e:
        return f"[read error: {e}]"


def _read_json_file(path):
    """安全读取 JSON 文件"""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _service_status():
    """检查三层服务状态"""
    services = {}
    for name, port in [("adapter", 5001), ("proxy", 5002), ("gateway", 18789)]:
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", "3", f"http://localhost:{port}/health"],
                capture_output=True, text=True, timeout=5,
            )
            services[name] = {"port": port, "http_code": result.stdout.strip()}
        except (subprocess.TimeoutExpired, OSError):
            services[name] = {"port": port, "http_code": "timeout"}
    return services


def create_snapshot(trigger, description=""):
    """创建故障快照，返回快照文件路径"""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    snapshot = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": trigger,
        "description": description,
        "logs": {},
        "proxy_stats": None,
        "services": {},
    }

    # 收集日志尾部
    for name, path in LOG_FILES.items():
        snapshot["logs"][name] = _tail_file(path, LOG_LINES)

    # 收集 proxy_stats
    snapshot["proxy_stats"] = _read_json_file(STATS_FILE)

    # 检查服务状态
    snapshot["services"] = _service_status()

    # 写入快照文件
    filename = f"{ts}_{trigger.replace(' ', '_')[:30]}.json"
    filepath = os.path.join(SNAPSHOT_DIR, filename)
    try:
        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"Snapshot saved: {filepath}")
    except OSError as e:
        print(f"Failed to save snapshot: {e}", file=sys.stderr)
        return None

    # 自动清理旧快照
    _cleanup_old_snapshots()

    return filepath


def _cleanup_old_snapshots():
    """保留最近 MAX_SNAPSHOTS 个快照"""
    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json")))
    if len(files) > MAX_SNAPSHOTS:
        for old in files[:len(files) - MAX_SNAPSHOTS]:
            try:
                os.remove(old)
            except OSError:
                pass


def list_snapshots():
    """列出最近快照"""
    if not os.path.isdir(SNAPSHOT_DIR):
        print("No snapshots yet.")
        return

    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json")), reverse=True)
    if not files:
        print("No snapshots yet.")
        return

    print(f"{'Time':<20} {'Trigger':<30} {'File'}")
    print("-" * 80)
    for f in files[:20]:
        try:
            with open(f) as fh:
                data = json.load(fh)
            ts = data.get("timestamp", "?")
            trigger = data.get("trigger", "?")
            print(f"{ts:<20} {trigger:<30} {os.path.basename(f)}")
        except (json.JSONDecodeError, OSError):
            print(f"{'?':<20} {'?':<30} {os.path.basename(f)}")


def main():
    if "--list" in sys.argv:
        list_snapshots()
        return 0

    if "--cleanup" in sys.argv:
        _cleanup_old_snapshots()
        print(f"Cleanup done. Max {MAX_SNAPSHOTS} snapshots retained.")
        return 0

    if "--auto" in sys.argv:
        idx = sys.argv.index("--auto")
        desc = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "auto-triggered"
        path = create_snapshot("auto", desc)
        return 0 if path else 1

    if "--manual" in sys.argv:
        idx = sys.argv.index("--manual")
        desc = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "manual snapshot"
        path = create_snapshot("manual", desc)
        return 0 if path else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
