#!/usr/bin/env python3
"""
config_loader.py — 统一配置加载器（V32: 阈值中心化）
从 config.yaml 加载所有阈值，供 proxy_filters / tool_proxy / watchdog 等使用。
"""
import os
import yaml

_CONFIG_CACHE = None
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# 运行时配置可能在 HOME 目录（auto_deploy 同步后）
_RUNTIME_PATH = os.path.expanduser("~/config.yaml")


def load_config(force_reload=False):
    """加载配置文件，优先运行时路径，回退仓库路径。结果缓存。"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE

    path = _RUNTIME_PATH if os.path.exists(_RUNTIME_PATH) else _CONFIG_PATH
    with open(path) as f:
        _CONFIG_CACHE = yaml.safe_load(f)
    return _CONFIG_CACHE


def get(section, key, default=None):
    """快捷读取: get("proxy", "max_request_bytes", 200000)"""
    cfg = load_config()
    return cfg.get(section, {}).get(key, default)


# ---------------------------------------------------------------------------
# 便捷常量（模块导入时立即可用，兼容旧代码 from config_loader import XXX）
# ---------------------------------------------------------------------------
def _init_constants():
    """延迟初始化常量，避免 import 时文件不存在报错。"""
    cfg = load_config()

    # Proxy
    p = cfg.get("proxy", {})
    constants = {
        "MAX_REQUEST_BYTES": p.get("max_request_bytes", 200000),
        "MAX_TOOLS": p.get("max_tools", 12),
        "BACKEND_TIMEOUT": p.get("backend_timeout_seconds", 300),
        "HEALTH_TIMEOUT": p.get("health_check_timeout_seconds", 5),
        "STATS_FLUSH_INTERVAL": p.get("stats_flush_interval_seconds", 10),
    }

    # Tokens
    t = cfg.get("tokens", {})
    cl = t.get("context_limit", 260000)
    constants["CONTEXT_LIMIT"] = cl
    constants["TOKEN_WARN_THRESHOLD"] = int(cl * t.get("warn_threshold_pct", 75) / 100)
    constants["TOKEN_CRITICAL_THRESHOLD"] = int(cl * t.get("critical_threshold_pct", 90) / 100)

    # Alerts
    a = cfg.get("alerts", {})
    constants["CONSECUTIVE_ERROR_ALERT"] = a.get("consecutive_error_threshold", 3)

    # Routing
    r = cfg.get("routing", {})
    constants["SIMPLE_MAX_MSGS"] = r.get("simple_max_msgs", 4)
    constants["SIMPLE_MAX_USER_LEN"] = r.get("simple_max_user_len", 200)
    constants["COMPLEX_MIN_MSGS"] = r.get("complex_min_msgs", 10)

    # SLO
    s = cfg.get("slo", {})
    constants["SLO_LATENCY_P95_MS"] = s.get("latency_p95_ms", 30000)
    constants["SLO_TOOL_SUCCESS_RATE"] = s.get("tool_success_rate_pct", 95.0)
    constants["SLO_DEGRADATION_RATE"] = s.get("degradation_rate_pct", 5.0)
    constants["SLO_TIMEOUT_RATE"] = s.get("timeout_rate_pct", 3.0)
    constants["SLO_AUTO_RECOVERY_RATE"] = s.get("auto_recovery_rate_pct", 90.0)
    constants["SLO_WINDOW_MINUTES"] = s.get("evaluation_window_minutes", 60)

    # Incidents
    i = cfg.get("incidents", {})
    constants["SNAPSHOT_LOG_LINES"] = i.get("snapshot_log_lines", 100)
    constants["SNAPSHOT_DIR"] = os.path.expanduser(i.get("snapshot_dir", "~/.kb/incidents"))
    constants["MAX_SNAPSHOTS"] = i.get("max_snapshots", 50)

    return constants


try:
    _CONSTANTS = _init_constants()
except Exception:
    _CONSTANTS = {}

def __getattr__(name):
    """模块级 __getattr__，支持 from config_loader import MAX_REQUEST_BYTES。"""
    if name in _CONSTANTS:
        return _CONSTANTS[name]
    raise AttributeError(f"module 'config_loader' has no attribute {name!r}")
