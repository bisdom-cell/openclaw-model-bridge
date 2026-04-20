#!/usr/bin/env python3
"""kb_integrity.py — KB 文件完整性校验器（V30.1 新增）

每日运行，为 ~/.kb/ 关键文件生成 SHA256 指纹并比对上次记录。
检测意外篡改、损坏、异常删除。

用法：
  python3 kb_integrity.py              # 校验（对比上次指纹）
  python3 kb_integrity.py --init       # 首次初始化指纹库
  python3 kb_integrity.py --update     # 更新指纹库（确认变更后）
  python3 kb_integrity.py --json       # JSON 输出（供脚本调用）

指纹库：~/.kb/.integrity/checksums.json
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

KB_DIR = os.path.expanduser("~/.kb")
INTEGRITY_DIR = os.path.join(KB_DIR, ".integrity")
CHECKSUMS_FILE = os.path.join(INTEGRITY_DIR, "checksums.json")

# 关键文件——篡改/损坏会导致系统行为异常
CRITICAL_FILES = [
    "index.json",
    "status.json",
    "daily_digest.md",
]

# 关键目录——文件数量骤降可能表示意外删除
CRITICAL_DIRS = {
    "notes": 0,      # 动态阈值，初始化时记录
    "sources": 0,
    "text_index": 0,
    "mm_index": 0,
}

# 不校验的路径模式
SKIP_PATTERNS = {".integrity", ".write.lockdir", "__pycache__", ".tmp"}


def sha256_file(path):
    """计算文件 SHA256"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def scan_critical_files():
    """扫描关键文件，返回 {rel_path: sha256}"""
    checksums = {}
    for name in CRITICAL_FILES:
        path = os.path.join(KB_DIR, name)
        if os.path.isfile(path):
            checksums[name] = sha256_file(path)
    return checksums


def scan_dir_counts():
    """统计关键目录文件数"""
    counts = {}
    for dirname in CRITICAL_DIRS:
        dirpath = os.path.join(KB_DIR, dirname)
        if os.path.isdir(dirpath):
            count = sum(1 for f in os.listdir(dirpath)
                        if os.path.isfile(os.path.join(dirpath, f))
                        and not f.startswith("."))
            counts[dirname] = count
        else:
            counts[dirname] = 0
    return counts


def scan_permissions():
    """检查 ~/.kb/ 目录权限"""
    issues = []
    try:
        st = os.stat(KB_DIR)
        mode = oct(st.st_mode)[-3:]
        # 目录应该是 700 或 750（不允许 other 读写）
        if int(mode[2]) > 0:
            issues.append(f"~/.kb/ 权限 {mode}：other 有访问权限（应为 700 或 750）")
    except OSError:
        issues.append("~/.kb/ 不存在或无法访问")

    # 检查 status.json 权限
    status_path = os.path.join(KB_DIR, "status.json")
    if os.path.isfile(status_path):
        st = os.stat(status_path)
        mode = oct(st.st_mode)[-3:]
        if int(mode[2]) > 0:
            issues.append(f"status.json 权限 {mode}：other 有访问权限")

    return issues


def load_checksums():
    """加载上次的指纹记录"""
    if not os.path.isfile(CHECKSUMS_FILE):
        return None
    try:
        with open(CHECKSUMS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_checksums(data):
    """原子保存指纹"""
    os.makedirs(INTEGRITY_DIR, exist_ok=True)
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = CHECKSUMS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKSUMS_FILE)


def verify(json_output=False):
    """校验当前状态 vs 上次记录"""
    prev = load_checksums()
    if prev is None:
        msg = "指纹库不存在，请先运行 python3 kb_integrity.py --init"
        if json_output:
            print(json.dumps({"status": "no_baseline", "message": msg}))
        else:
            print(f"⚠️  {msg}")
        return 1

    alerts = []
    info = []

    # 1. 关键文件指纹比对
    current_checksums = scan_critical_files()
    prev_checksums = prev.get("checksums", {})

    for name in CRITICAL_FILES:
        prev_hash = prev_checksums.get(name)
        curr_hash = current_checksums.get(name)

        if prev_hash and not curr_hash:
            alerts.append(f"❌ {name} 已消失（上次存在）")
        elif prev_hash and curr_hash and prev_hash != curr_hash:
            info.append(f"📝 {name} 已变更（正常更新或需确认）")
        elif not prev_hash and curr_hash:
            info.append(f"✨ {name} 新增")

    # 2. 目录文件数变化
    current_counts = scan_dir_counts()
    prev_counts = prev.get("dir_counts", {})

    for dirname, curr_count in current_counts.items():
        prev_count = prev_counts.get(dirname, 0)
        if prev_count > 0 and curr_count == 0:
            alerts.append(f"❌ {dirname}/ 目录已清空（{prev_count} → 0）")
        elif prev_count > 10 and curr_count < prev_count * 0.5:
            alerts.append(f"⚠️  {dirname}/ 文件数骤降（{prev_count} → {curr_count}）")
        elif curr_count > prev_count:
            info.append(f"📈 {dirname}/: {prev_count} → {curr_count} 文件")

    # 3. 权限检查
    perm_issues = scan_permissions()
    for issue in perm_issues:
        alerts.append(f"🔒 {issue}")

    # 4. status.json 结构完整性
    status_path = os.path.join(KB_DIR, "status.json")
    if os.path.isfile(status_path):
        try:
            with open(status_path) as f:
                status = json.load(f)
            required_keys = {"priorities", "recent_changes", "feedback", "health", "focus"}
            missing = required_keys - set(status.keys())
            if missing:
                alerts.append(f"⚠️  status.json 缺少字段: {missing}")
        except json.JSONDecodeError:
            alerts.append("❌ status.json JSON 格式损坏")

    # 输出
    if json_output:
        result = {
            "status": "alert" if alerts else "ok",
            "alerts": alerts,
            "info": info,
            "checksums": current_checksums,
            "dir_counts": current_counts,
            "baseline_time": prev.get("updated", "unknown"),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"🔍 KB 完整性校验 ({ts})")
        print(f"   基线时间: {prev.get('updated', 'unknown')}")
        print()

        if alerts:
            print("🚨 告警:")
            for a in alerts:
                print(f"  {a}")
            print()

        if info:
            print("ℹ️  变更:")
            for i in info:
                print(f"  {i}")
            print()

        if not alerts and not info:
            print("✅ 所有关键文件完好，无异常变更")

        # 目录统计
        print(f"\n📊 目录统计:")
        for dirname, count in current_counts.items():
            prev_count = prev_counts.get(dirname, "?")
            print(f"  {dirname}/: {count} 文件 (上次: {prev_count})")

    return 1 if alerts else 0


def init_or_update():
    """初始化或更新指纹库"""
    checksums = scan_critical_files()
    dir_counts = scan_dir_counts()

    data = {
        "checksums": checksums,
        "dir_counts": dir_counts,
    }
    save_checksums(data)

    print(f"✅ 指纹库已{'更新' if os.path.isfile(CHECKSUMS_FILE) else '初始化'}")
    print(f"   关键文件: {len(checksums)} 个")
    for name, h in checksums.items():
        print(f"     {name}: {h[:16]}...")
    print(f"   目录统计:")
    for dirname, count in dir_counts.items():
        print(f"     {dirname}/: {count} 文件")


def main():
    parser = argparse.ArgumentParser(description="KB 文件完整性校验")
    parser.add_argument("--init", action="store_true", help="初始化指纹库")
    parser.add_argument("--update", action="store_true", help="更新指纹库")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if args.init or args.update:
        init_or_update()
    else:
        sys.exit(verify(json_output=args.json))


if __name__ == "__main__":
    main()
