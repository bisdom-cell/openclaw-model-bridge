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

# 日志文件位置（Mac Mini 运行时路径）—— 每个组件一个候选列表，取第一个真实存在的。
#
# V37.9.325 血案（对抗审计）：gateway 此前写死单条 ~/openclaw-gateway.log，而本仓库
# **没有任何写入方**产出该路径 —— launchd plist 写 $HOME/openclaw_gateway.log
# (deploy/install_openclaw_macmini.sh) / restart.sh 的 nohup fallback 写 ~/gateway.log /
# Gateway 自身 verbose 日志在 /tmp/openclaw/openclaw-<date>.log (diagnose.sh 同源)。
# 于是每一份故障快照的 gateway 段恒为「[file not found]」，而快照恰恰只在故障时创建，
# gateway 静默死正是有案可查的故障类（踩坑 #96 / V37.8.13 宕 9h）。读者会把
# 「file not found」读成「gateway 没记日志」——对一个死掉的 gateway 而言，这个错误
# 结论比正确结论更可信 = fail-plausible。
# Gateway 自己写死的日志目录。豁免 B108 的理由：这不是我们创建的临时文件，而是**只读**
# 一个由 Gateway 写在固定路径的日志（diagnose.sh 读同一路径）。B108 的威胁模型是「可预测
# 路径下创建临时文件 → 符号链接攻击」，只读不创建不适用；且**不能**改用
# tempfile.gettempdir() —— 它在 macOS 上返回 $TMPDIR 的每用户私有目录 (/var/folders/...)，
# 会让这条候选永远解析不到真实文件，那是用规避扫描器的写法换来一个不工作的路径。
_GATEWAY_TMP_LOG_DIR = "/tmp/openclaw"  # nosec B108

LOG_FILE_CANDIDATES = {
    "proxy": [os.path.expanduser("~/tool_proxy.log")],
    "adapter": [os.path.expanduser("~/adapter.log")],
    "gateway": [
        # Gateway 自身 verbose 日志（信息量最大，按日期滚动）
        os.path.join(_GATEWAY_TMP_LOG_DIR, "openclaw-" + time.strftime("%Y-%m-%d") + ".log"),
        # launchd StandardOutPath
        os.path.expanduser("~/openclaw_gateway.log"),
        # restart.sh 在 plist 缺失时的 nohup fallback
        os.path.expanduser("~/gateway.log"),
        # 历史路径：本仓库无写入方产出它，仅作兜底保留（dev 无法验证 Mac Mini 文件系统，
        # 删掉的代价是可能丢证据，留着的代价是一个字符串 → 留）
        os.path.expanduser("~/openclaw-gateway.log"),
    ],
}

# 三层服务健康探测（可被测试替换）
SERVICE_PORTS = [("adapter", 5001), ("proxy", 5002), ("gateway", 18789)]

# V37.9.325 血案：curl 只给了 --connect-timeout（只管建连），对「接受连接但不响应」的
# 挂起态服务无界，单服务靠 subprocess timeout=5 兜底 → 三个挂起服务实测 15.0s，
# 而 tool_proxy 侧 subprocess timeout=10 会先把整个快照进程杀掉；又因服务探测是
# 写文件前的最后一步，**已采集的日志一并丢弃** → 快照机制恰在它存在的那个场景里失效。
SERVICE_CHECK_TIMEOUT_SEC = 3   # 单服务硬上限（curl --max-time 2 + subprocess 3 兜底）
SERVICE_CHECK_BUDGET_SEC = 6    # 三服务总预算，超出的显式标 skipped_budget

STATS_FILE = os.path.expanduser("~/proxy_stats.json")


def _resolve_log_path(candidates):
    """按顺序取第一个真实存在的候选；返回 (路径 or None, 试过的候选列表)。"""
    tried = []
    for path in candidates:
        tried.append(path)
        if os.path.exists(path):
            return path, tried
    return None, tried


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
    """检查三层服务状态（V37.9.325: 单服务 --max-time + 总预算，超预算显式标注）。"""
    services = {}
    deadline = time.monotonic() + SERVICE_CHECK_BUDGET_SEC
    for name, port in SERVICE_PORTS:
        if time.monotonic() >= deadline:
            # 诚实标注而非静默缺失：读者要能区分「探测过但没响应」与「没来得及探测」
            services[name] = {"port": port, "http_code": "skipped_budget"}
            continue
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", "1", "--max-time", "2",
                 f"http://localhost:{port}/health"],
                capture_output=True, text=True, timeout=SERVICE_CHECK_TIMEOUT_SEC,
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
        "logs_meta": {},
        "proxy_stats": None,
        "services": {},
    }

    # 收集日志尾部（V37.9.325: 候选解析 + 记录找过哪些，解析不到时不再只报一个错路径）
    for name, candidates in LOG_FILE_CANDIDATES.items():
        path, tried = _resolve_log_path(candidates)
        if path is None:
            snapshot["logs"][name] = f"[no log file found; tried: {', '.join(tried)}]"
            snapshot["logs_meta"][name] = {"resolved": None, "candidates_tried": tried}
        else:
            snapshot["logs"][name] = _tail_file(path, LOG_LINES)
            snapshot["logs_meta"][name] = {"resolved": path, "candidates_tried": tried}

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
