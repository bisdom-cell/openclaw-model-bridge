#!/usr/bin/env python3
import http.server, socketserver, json, ssl, sys, os, threading, time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Version — read from VERSION file (semver, V36+)
# ---------------------------------------------------------------------------
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as _vf:
        _VERSION = _vf.read().strip()
except OSError:
    _VERSION = "unknown"
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Load fallback config from config.yaml (optional, graceful fallback)
# ---------------------------------------------------------------------------
_FALLBACK_CFG = {}
try:
    from config_loader import load_config
    _cfg = load_config()
    _fb = _cfg.get("fallback", {})
    if isinstance(_fb, dict):
        _rg = _fb.get("remote_gpu", {})
        if isinstance(_rg, dict):
            _FALLBACK_CFG = _rg
except Exception:
    pass  # config_loader 不可用时使用默认值

# V37.9.129: 永久排除地理封锁/不可达的 provider 出 fallback 链（config-driven，版本控制化退役）。
# gemini 从香港返回 400 "User location is not supported"（永久 geo-block，一直是死链——
# 平时 qwen+doubao 够用没人发现，直到 2026-06-10 03:00 三个后端全挂才浮现）。
# 退役方式 = 保留 GEMINI_API_KEY 在 plist（INV-PLIST-ENV-001 + preflight 要求）+ 从 fallback 链排除。
_FALLBACK_EXCLUDE = set(_FALLBACK_CFG.get("exclude_providers", []))

# ---------------------------------------------------------------------------
# Provider registry — V34: 从 providers.py 加载（Provider Compatibility Layer）
# 向后兼容：PROVIDERS 仍是 dict，provider 对象可通过 get_provider() 获取能力声明
# ---------------------------------------------------------------------------
try:
    from providers import PROVIDERS, get_provider as _get_provider, get_registry as _get_registry
except ImportError:
    # providers.py 不可用时回退到内联定义
    _get_provider = None
    _get_registry = None
    PROVIDERS = {
        "qwen": {
            "base_url":    "https://hkagentx.hkopenlab.com/v1",
            "api_key_env": "REMOTE_API_KEY",
            "model_id":    "Qwen3-235B-A22B-Instruct-2507-W8A8",
            "vl_model_id": "Qwen2.5-VL-72B-Instruct",
            "auth_style":  "bearer",
        },
        "openai": {
            "base_url":    "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model_id":    "gpt-4o",
            "auth_style":  "bearer",
        },
        "gemini": {
            "base_url":    "https://generativelanguage.googleapis.com/v1beta/openai",
            "api_key_env": "GEMINI_API_KEY",
            "model_id":    "gemini-2.5-flash",
            "auth_style":  "bearer",
        },
        "claude": {
            "base_url":    "https://api.anthropic.com/v1",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model_id":    "claude-sonnet-4-6",
            "auth_style":  "x-api-key",
        },
        "kimi": {
            "base_url":    "https://api.moonshot.ai/v1",
            "api_key_env": "MOONSHOT_API_KEY",
            "model_id":    "kimi-k2.5",
            "auth_style":  "bearer",
        },
        "minimax": {
            "base_url":    "https://api.minimaxi.com/v1",
            "api_key_env": "MINIMAX_API_KEY",
            "model_id":    "MiniMax-M2.7",
            "auth_style":  "bearer",
        },
        "glm": {
            "base_url":    "https://open.bigmodel.cn/api/paas/v4",
            "api_key_env": "GLM_API_KEY",
            "model_id":    "glm-5",
            "auth_style":  "bearer",
        },
    }

# ---------------------------------------------------------------------------
# Load active provider from environment (default: qwen for backward compat)
# ---------------------------------------------------------------------------
PROVIDER_NAME = os.environ.get("PROVIDER", "qwen")
if PROVIDER_NAME not in PROVIDERS:
    print(f"[adapter] ERROR: unknown PROVIDER={PROVIDER_NAME!r}, valid: {list(PROVIDERS)}", flush=True)
    sys.exit(1)

provider    = PROVIDERS[PROVIDER_NAME]
TARGET_BASE = provider["base_url"]
REAL_MODEL_ID = os.environ.get("MODEL_ID", provider["model_id"])
VL_MODEL_ID   = os.environ.get("VL_MODEL_ID", provider.get("vl_model_id", ""))
API_KEY     = os.environ.get(provider["api_key_env"], "sk-REPLACE-ME")
AUTH_STYLE  = provider["auth_style"]
PORT        = int(os.environ.get("PORT", 5001))
_MAX_INGEST_BYTES = 32 * 1024 * 1024  # V37.9.226 (audit SEC-2): ingest cap, DoS backstop
ctx         = ssl.create_default_context()

# ---------------------------------------------------------------------------
# Fallback chain — capability-based auto-discovery + manual pin (V37)
# If FALLBACK_PROVIDER is set, it becomes first in chain.
# Remaining slots auto-filled from build_fallback_chain() by capability overlap.
# V37.1+: Hot-reload support — _build_fallback_chain() shared by startup & reload.
# ---------------------------------------------------------------------------

def _entry_from_registry(cp, api_key, model_id=None):
    """Build a fallback chain entry from a registry provider object.
    V37.9.218: entries carry vl_model_id for capability-aware vision fallback.
    V37.9.224: entries carry reasoning_off_body (镜像 vl_model_id) — 批量 fallback
    按各 provider 自己的声明重算 thinking-off 注入 (B1 fallback 传播)."""
    return {
        "name":        cp.name,
        "base_url":    cp.base_url,
        "api_key":     api_key,
        "model_id":    model_id or cp.model_id,
        "auth_style":  cp.auth_style,
        "vl_model_id": getattr(cp, "vl_model_id", "") or "",
        "reasoning_off_body": getattr(cp, "reasoning_off_body", None) or None,
    }


def _build_fallback_chain():
    """Build fallback chain from env + capability registry. Pure function, no side effects.
    Returns list of {name, base_url, api_key, model_id, auth_style, vl_model_id} dicts.

    Precedence (V37.9.218):
      1) FALLBACK_ORDER — explicit ordered provider list (comma-separated), authoritative.
         一物一形: overrides cap_score auto-sort AND legacy FALLBACK_PROVIDER single slot.
         primary auto-excluded (可传完整偏好顺序, 切换 primary 无需改 FALLBACK_ORDER);
         unknown/unavailable(无 key)/_FALLBACK_EXCLUDE(geo-block) 的 provider 跳过; 去重保序。
      2) Legacy: FALLBACK_PROVIDER single slot → first, then cap_score auto-fill.
    Entries carry vl_model_id → capability-aware vision fallback (image 请求跳过纯文本 provider)。
    """
    reg = _get_registry() if _get_registry else None

    # 1) V37.9.218: FALLBACK_ORDER explicit ordered list (替代单槽, 权威)
    order_raw = os.environ.get("FALLBACK_ORDER", "").strip()
    if order_raw and reg:
        if os.environ.get("FALLBACK_PROVIDER", ""):
            print("[adapter] WARN: FALLBACK_ORDER 与 FALLBACK_PROVIDER 同时设置 — "
                  "FALLBACK_ORDER 优先 (旧单槽被忽略, 一物一形)", flush=True)
        chain, seen = [], set()
        for name in [n.strip() for n in order_raw.split(",") if n.strip()]:
            if name in seen:
                continue
            seen.add(name)
            if name == PROVIDER_NAME:
                continue  # primary 不进 fallback 链 (自动排除)
            if name in _FALLBACK_EXCLUDE:
                continue  # geo-block / 不可达
            cp = reg.get(name)
            if not cp:
                print(f"[adapter] WARN: FALLBACK_ORDER 未知 provider {name!r} — 跳过", flush=True)
                continue
            ck = os.environ.get(cp.api_key_env, "")
            if not ck:
                continue  # 无 key → 不可用
            chain.append(_entry_from_registry(cp, ck))
        return chain

    # 2) Legacy: explicit FALLBACK_PROVIDER (backward compat) → first in chain
    # V37.9.129: _FALLBACK_EXCLUDE 排除地理封锁/不可达 provider（如 gemini 香港 geo-block）
    chain = []
    explicit_fb = os.environ.get("FALLBACK_PROVIDER", "")
    if explicit_fb and explicit_fb in PROVIDERS and explicit_fb != PROVIDER_NAME \
            and explicit_fb not in _FALLBACK_EXCLUDE:
        fb = PROVIDERS[explicit_fb]
        fb_key = os.environ.get(fb["api_key_env"], "")
        if fb_key:
            chain.append({
                "name":       explicit_fb,
                "base_url":   fb["base_url"],
                "api_key":    fb_key,
                "model_id":   os.environ.get("FALLBACK_MODEL_ID", fb["model_id"]),
                "auth_style": fb["auth_style"],
                "vl_model_id": fb.get("vl_model_id", ""),  # V37.9.218
                "reasoning_off_body": fb.get("reasoning_off_body") or None,  # V37.9.224
            })

    # 3) Auto-discover from capability-based chain (sorted by overlap + verification)
    if reg:
        auto_chain = reg.build_fallback_chain(PROVIDER_NAME, require_available=True)
        existing_names = {fb["name"] for fb in chain}
        for cp in auto_chain:
            # V37.9.129: 排除 _FALLBACK_EXCLUDE 中的 provider（geo-block/不可达）
            if cp.name not in existing_names and cp.name not in _FALLBACK_EXCLUDE:
                ck = os.environ.get(cp.api_key_env, "")
                if ck:
                    chain.append(_entry_from_registry(cp, ck))

    return chain

# Initial build at startup
FALLBACK_CHAIN = _build_fallback_chain()

# Backward compat: FALLBACK = first in chain (or None)
FALLBACK = FALLBACK_CHAIN[0] if FALLBACK_CHAIN else None

# ---------------------------------------------------------------------------
# Hot-reload thread — periodically rebuild FALLBACK_CHAIN (V37.1+)
# Gated by ADAPTER_HOT_RELOAD=true (default: false, non-destructive introduction)
# ---------------------------------------------------------------------------
_HOT_RELOAD_ENABLED = os.environ.get("ADAPTER_HOT_RELOAD", "false").lower() in ("true", "1", "yes")
_HOT_RELOAD_INTERVAL = int(os.environ.get("ADAPTER_HOT_RELOAD_INTERVAL", "3600"))  # seconds
_last_reload_time = time.time()
_last_reload_status = "init"  # "init" | "ok" | "error:<msg>"

def _reload_fallback_chain():
    """Rebuild FALLBACK_CHAIN from current env + registry. Thread-safe via reference replacement."""
    global FALLBACK_CHAIN, FALLBACK, _last_reload_time, _last_reload_status
    old_names = [fb["name"] for fb in FALLBACK_CHAIN]
    try:
        new_chain = _build_fallback_chain()
        new_names = [fb["name"] for fb in new_chain]

        # Only update if build succeeded (never degrade to empty if old chain was non-empty)
        if not new_chain and FALLBACK_CHAIN:
            _last_reload_status = "error:new chain empty, kept old"
            log(f"HOT-RELOAD: new chain is empty but old has {len(FALLBACK_CHAIN)} providers — keeping old chain")
            _last_reload_time = time.time()
            return

        # Atomic reference replacement (Python GIL guarantees safe read from request threads)
        FALLBACK_CHAIN = new_chain
        FALLBACK = new_chain[0] if new_chain else None
        _last_reload_time = time.time()

        if old_names != new_names:
            _last_reload_status = f"ok:changed"
            log(f"HOT-RELOAD: chain updated: {' -> '.join(new_names)} (was: {' -> '.join(old_names)})")
        else:
            _last_reload_status = "ok:unchanged"
    except Exception as e:
        _last_reload_status = f"error:{e}"
        _last_reload_time = time.time()
        log(f"HOT-RELOAD ERROR: {e} — keeping old chain ({' -> '.join(old_names)})")

def _hot_reload_loop():
    """Background thread: periodically reload fallback chain."""
    while True:
        time.sleep(_HOT_RELOAD_INTERVAL)
        _reload_fallback_chain()

if _HOT_RELOAD_ENABLED:
    _reload_thread = threading.Thread(target=_hot_reload_loop, daemon=True, name="fallback-reload")
    _reload_thread.start()

# ---------------------------------------------------------------------------
# Smart routing: fast model for simple queries (optional)
# Set FAST_PROVIDER env to enable (e.g., "gemini" for short/simple queries)
# ---------------------------------------------------------------------------
FAST_PROVIDER_NAME = os.environ.get("FAST_PROVIDER", "")
if FAST_PROVIDER_NAME and FAST_PROVIDER_NAME in PROVIDERS and FAST_PROVIDER_NAME != PROVIDER_NAME:
    _fp = PROVIDERS[FAST_PROVIDER_NAME]
    _fp_key = os.environ.get(_fp["api_key_env"], "")
    if _fp_key:
        FAST_ROUTE = {
            "name":       FAST_PROVIDER_NAME,
            "base_url":   _fp["base_url"],
            "api_key":    _fp_key,
            "model_id":   os.environ.get("FAST_MODEL_ID", _fp["model_id"]),
            "auth_style": _fp["auth_style"],
        }
    else:
        FAST_ROUTE = None
else:
    FAST_ROUTE = None

# Try to import classify_complexity from proxy_filters (co-located on Mac Mini)
try:
    from proxy_filters import classify_complexity
except ImportError:
    classify_complexity = None


def _is_batch_workload(clean, has_multimodal, use_model, primary_name):
    """V37.9.222 批量 workload 判定（纯函数，可单测，**独立于 FAST_ROUTE**）。

    批量 = 无 tools（纯推理，规则 #27）+ 非多模态（图片需 VL）+ 默认 model
    + 非 ?provider= 显式 override（尊重显式选择）。

    独立于 FAST_ROUTE 是关键：B1（reasoning-off 注入）在**无独立快 provider**（FAST_PROVIDER 未设）
    时也要能识别批量 → 给 reasoning primary 注入 thinking-off。A2（_classify_fast_route）额外要求
    FAST_ROUTE 存在。两者共用本判定 = 一物一形，批量定义单一真理源。
    """
    return (not has_multimodal
            and use_model == REAL_MODEL_ID
            and primary_name == PROVIDER_NAME
            and not bool(clean.get("tools")))


def _classify_fast_route(clean, clean_msgs, has_multimodal, use_model, primary_name):
    """V37.9.221 A2 workload 路由决策（纯函数，可单测）。返回 "workload" | "smart" | None。

    - "workload": 批量（_is_batch_workload）→ 路由到独立 fast provider（FAST_ROUTE），
                  **不依赖 classify_complexity**（批量 prompt 内容 complex 但延迟敏感应走快路）。
    - "smart":    有 tools 且 simple 查询 → fast（V37.9.76 既有行为，向后兼容）。
    - None:       留 primary（PA 复杂 tool-call / 多模态 / ?provider= override / 非默认 model）。

    需 FAST_ROUTE 已配置（仅 FAST_PROVIDER≠PROVIDER 时非空）。无 FAST_ROUTE 时批量由 B1
    （do_POST 里 reasoning_off_body 注入）在 primary 上处理，见 _is_batch_workload。
    """
    if not FAST_ROUTE:
        return None
    if _is_batch_workload(clean, has_multimodal, use_model, primary_name):
        return "workload"
    if (not has_multimodal and use_model == REAL_MODEL_ID and primary_name == PROVIDER_NAME
            and classify_complexity and classify_complexity(clean_msgs, has_tools=True) == "simple"):
        return "smart"
    return None


def _batch_reasoning_off_body(clean, has_multimodal, use_model, primary_name, use_fast):
    """V37.9.222 B1: 批量请求在 reasoning provider 上要注入的「关 reasoning」请求体片段（或 None）。

    批量（_is_batch_workload）+ 服务 provider 声明了 reasoning_off_body → 返回该片段；否则 None。
    服务 provider = FAST_ROUTE provider（use_fast 时）否则 primary。让 reasoning primary 也能
    快速服务批量（无独立快 provider 时的终局路径，qwen 退役后 doubao_21/deepseek_full 单模型通吃）。
    qwen 等非-reasoning provider 无 reasoning_off_body → 返回 None（no-op）。纯函数可单测。
    """
    if not _is_batch_workload(clean, has_multimodal, use_model, primary_name):
        return None
    serving = FAST_PROVIDER_NAME if use_fast else primary_name
    entry = PROVIDERS.get(serving, {})
    rob = entry.get("reasoning_off_body") if isinstance(entry, dict) else None
    # V37.9.224: 非 dict（畸形 YAML 插件声明如字符串）→ None，注入/剥离两侧都只认 dict
    # （FAIL-OPEN: 畸形声明不得让请求路径抛异常）
    return rob if isinstance(rob, dict) and rob else None


def _fallback_batch_body(clean, fb_entry, is_batch, serving_rob):
    """V37.9.224 B1 fallback 传播: 构造 fallback 尝试的请求体（纯函数，可单测）。

    批量请求 fallback 时，reasoning-off 注入必须按 **fallback provider 自己的声明**重算，
    不能继承 serving provider 的注入（旧行为 fb_clean = dict(clean) 原样带走）：
    - 先剥掉 serving provider 注入的 keys（serving_rob）— 该参数对无声明的 provider
      未测（e.g. doubao 的 thinking 片段发给 qwen vLLM 端点可能 400，打断最后兜底）；
    - 再注入 fallback provider 自己的 reasoning_off_body（有声明才注）— 修 qwen-primary
      回滚态批量 fallback 到 reasoning provider 带 reasoning 跑的事故路径
      （V37.9.220 在 fallback 重演：reasoning 7-9min >> client 超时 → broken pipe → 502）。
    非批量（PA tool-call / 多模态 / override）：原样浅拷贝，现行为不变
    （PA fallback 保留 reasoning 质量）。
    """
    fb_clean = dict(clean)
    if not is_batch:
        return fb_clean
    if isinstance(serving_rob, dict):
        for k in serving_rob:
            fb_clean.pop(k, None)
    fb_rob = fb_entry.get("reasoning_off_body") if isinstance(fb_entry, dict) else None
    # V37.9.224 FAIL-OPEN: 只认 dict 片段 — 畸形第三方 YAML 插件声明（字符串/list）不得让
    # update() 抛异常打断整条 fallback 链（本调用点在 per-provider try 之前，异常会摧毁兜底）
    if isinstance(fb_rob, dict) and fb_rob:
        fb_clean.update(fb_rob)
    return fb_clean

# ---------------------------------------------------------------------------
# Circuit Breaker for primary provider (V32: Fallback Matrix)
# ---------------------------------------------------------------------------
_CB_THRESHOLD = _FALLBACK_CFG.get("circuit_breaker_threshold", 5)
_CB_RESET_SEC = _FALLBACK_CFG.get("circuit_breaker_reset_seconds", 300)
_PRIMARY_TIMEOUT = _FALLBACK_CFG.get("timeout_ms", 300000) / 1000  # → seconds
_FALLBACK_TIMEOUT = _FALLBACK_CFG.get("fallback_timeout_ms", 60000) / 1000

class CircuitBreaker:
    """简易断路器：连续失败 N 次后短路，直接走 fallback，reset 时间后恢复尝试。"""
    def __init__(self, threshold=5, reset_seconds=300):
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._consecutive_failures = 0
        self._open_since = 0  # epoch when circuit opened
        self._lock = threading.Lock()

    def is_open(self):
        with self._lock:
            if self._consecutive_failures < self._threshold:
                return False
            # 短路状态：检查是否到了重置时间
            if time.time() - self._open_since >= self._reset_seconds:
                # half-open: 允许一次尝试
                return False
            return True

    def record_success(self):
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self):
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._open_since = time.time()

    def state(self):
        with self._lock:
            if self._consecutive_failures < self._threshold:
                return "closed"
            if time.time() - self._open_since >= self._reset_seconds:
                return "half-open"
            return "open"

_circuit_breaker = CircuitBreaker(_CB_THRESHOLD, _CB_RESET_SEC)

ALLOWED_PARAMS = {
    "model", "messages", "max_tokens", "temperature", "top_p",
    "stream", "stop", "tools", "tool_choice", "n",
    "presence_penalty", "frequency_penalty", "seed"
}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[adapter:{PROVIDER_NAME}] {ts} {msg}", flush=True)

def _make_auth_headers(auth_style, api_key):
    """Return auth headers dict for a given style and key."""
    if auth_style == "x-api-key":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}

def add_auth(req):
    for k, v in _make_auth_headers(AUTH_STYLE, API_KEY).items():
        req.add_header(k, v)

def _safe_urlopen(req, **kwargs):
    """urlopen wrapper that rejects non-https schemes (B310 mitigation)."""
    scheme = urlparse(req.full_url).scheme
    if scheme not in ("https", "http"):
        raise URLError(f"Blocked URL scheme: {scheme}")
    return urlopen(req, **kwargs)  # nosec B310


def _forward_request(url, data, auth_headers, timeout=300):
    """Send POST request and return (status, body). Raises on failure."""
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "curl/8.0")
    for k, v in auth_headers.items():
        req.add_header(k, v)
    with _safe_urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.status, resp.read()

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        # Local health check — never forward to remote
        if self.path in ("/health", "/v1/health"):
            info = {"ok": True, "version": _VERSION, "provider": PROVIDER_NAME, "model": REAL_MODEL_ID}
            if VL_MODEL_ID:
                info["vl_model"] = VL_MODEL_ID
            if FALLBACK_CHAIN:
                info["fallback_chain"] = [fb["name"] for fb in FALLBACK_CHAIN]
                info["fallback"] = FALLBACK_CHAIN[0]["name"]  # backward compat
                info["circuit_breaker"] = _circuit_breaker.state()
            if _HOT_RELOAD_ENABLED:
                info["hot_reload"] = {
                    "enabled": True,
                    "interval_s": _HOT_RELOAD_INTERVAL,
                    "last_status": _last_reload_status,
                    "last_reload": datetime.fromtimestamp(_last_reload_time).strftime("%H:%M:%S"),
                }
            if FAST_ROUTE:
                info["fast_route"] = f"{FAST_ROUTE['name']}/{FAST_ROUTE['model_id']}"
            # V34: capabilities from Provider Compatibility Layer
            if _get_provider:
                p = _get_provider(PROVIDER_NAME)
                if p:
                    info["capabilities"] = p.capabilities.supported_modalities()
                    info["verified"] = p.capabilities.verified_features()
            resp = json.dumps(info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        path = self.path.replace("/v1", "", 1) if self.path.startswith("/v1") else self.path
        url = f"{TARGET_BASE}{path}"
        log(f"GET {url}")
        req = Request(url)
        add_auth(req)
        req.add_header("User-Agent", "curl/8.0")
        try:
            with _safe_urlopen(req, timeout=30, context=ctx) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            log(f"GET error: {e}")
            self.send_error(502, str(e))

    def _resolve_primary_provider(self):
        """V37.9.77 enforcement: 解析 ?provider=X query 参数, 返回 (base_url, model_id, auth_style, api_key, name).

        FAIL-OPEN 契约:
        - 缺 ?provider=X → 返回默认 (PROVIDER_NAME 全局)
        - ?provider=X 但 X 不在 PROVIDERS → 返回默认 (silent fallback)
        - ?provider=X 但 X 缺 API key → 返回默认 (避免认证失败)
        - ROUTER_ENFORCE=off (默认) → 忽略 ?provider= 强制走默认 (PoC 安全网)

        V37.9.77 设计: enforcement 是 opt-in (ROUTER_ENFORCE=on env var), 默认仍是 V37.9.76 shadow 行为.
        Mac Mini operator 可单独 flip env var 启用 enforcement 一周观察后再永久 on.
        """
        # ROUTER_ENFORCE feature flag — 默认 off (V37.9.77 PoC 安全网, V37.9.78+ 评估默认 on)
        enforce = os.environ.get("ROUTER_ENFORCE", "off").lower() in ("on", "true", "1", "yes")
        if not enforce:
            return TARGET_BASE, REAL_MODEL_ID, AUTH_STYLE, API_KEY, PROVIDER_NAME
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            override = (qs.get("provider", [None])[0] or "").strip()
        except (ValueError, AttributeError):
            return TARGET_BASE, REAL_MODEL_ID, AUTH_STYLE, API_KEY, PROVIDER_NAME
        if not override or override not in PROVIDERS:
            return TARGET_BASE, REAL_MODEL_ID, AUTH_STYLE, API_KEY, PROVIDER_NAME
        ovr = PROVIDERS[override]
        ovr_api_key = os.environ.get(ovr.get("api_key_env", ""), "")
        if not ovr_api_key:
            # 选了一个没配 API key 的 provider, 回退默认 (避免 401)
            return TARGET_BASE, REAL_MODEL_ID, AUTH_STYLE, API_KEY, PROVIDER_NAME
        return (
            ovr["base_url"],
            ovr.get("model_id", REAL_MODEL_ID),
            ovr.get("auth_style", "bearer"),
            ovr_api_key,
            override,
        )

    def do_POST(self):
        rid = self.headers.get("X-Request-ID", "")
        tag = f"[{rid}] " if rid else ""
        t0 = time.monotonic()
        # V37.9.226 (audit SEC-2/3): 畸形 Content-Length 优雅 400（原 int() 裸调崩线程）;
        # 超大 body 读入前拒（防 OOM）。32 MB 生成式上限，legit 请求远低于此。
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self.send_error(400, "Bad Content-Length")
            return
        if length < 0 or length > _MAX_INGEST_BYTES:
            self.send_error(413, "Request too large")
            return
        raw = self.rfile.read(length)
        # V37.9.77 enforcement: 先 strip query string 再拼 forward URL (provider API 不识别 ?provider=)
        try:
            _parsed_path = urlparse(self.path)
            _clean_path = _parsed_path.path  # 不含 query
        except (ValueError, AttributeError):
            _clean_path = self.path
        path = _clean_path.replace("/v1", "", 1) if _clean_path.startswith("/v1") else _clean_path
        # V37.9.77 enforcement: 解析 ?provider=X 走 override; 默认仍用 PROVIDER_NAME (向后兼容)
        primary_base, primary_model_id, primary_auth_style, primary_api_key, primary_name = (
            self._resolve_primary_provider()
        )
        url = f"{primary_base}{path}"
        if primary_name != PROVIDER_NAME:
            log(f"{tag}V37.9.77 ROUTER OVERRIDE: provider={primary_name} (default={PROVIDER_NAME})")
        log(f"{tag}POST {url} ({length} bytes)")

        if "/chat/completions" in self.path:
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as e:
                log(f"{tag}Bad JSON: {e}")
                self.send_error(400, "Bad JSON")
                return

            log(f"{tag}INCOMING KEYS: {list(body.keys())}")

            # Clean messages - detect multimodal content for VL routing
            msgs = body.get("messages", [])
            clean_msgs = []
            has_multimodal = False
            for m in msgs:
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    # Check if any part is non-text (image_url, audio, etc.)
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in ("image_url", "image", "audio", "video"):
                            has_multimodal = True
                            break
                    if has_multimodal and VL_MODEL_ID:
                        # Keep multimodal content as-is for VL model
                        clean_msg = {"role": role, "content": content}
                    else:
                        # No VL model available — fallback to text extraction
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                text_parts.append(part)
                        clean_msg = {"role": role, "content": "\n".join(text_parts) if text_parts else ""}
                else:
                    clean_msg = {"role": role, "content": content}

                # Preserve tool_calls for assistant messages
                if "tool_calls" in m:
                    clean_msg["tool_calls"] = m["tool_calls"]
                # Preserve tool_call_id for tool messages
                if role == "tool" and "tool_call_id" in m:
                    clean_msg["tool_call_id"] = m["tool_call_id"]
                if role == "tool" and "name" in m:
                    clean_msg["name"] = m["name"]

                clean_msgs.append(clean_msg)

            # Build clean request — route to VL model if multimodal content detected
            # V37.9.77 enforcement: override provider 时直接用 override.model_id (不走 VL_MODEL_ID 路径)
            # VL 路由仅适用于默认 PROVIDER_NAME 路径 (向后兼容现有多模态行为)
            if primary_name != PROVIDER_NAME:
                use_model = primary_model_id  # override path: 用 router 选的 provider 的 model_id
            else:
                use_model = VL_MODEL_ID if (has_multimodal and VL_MODEL_ID) else REAL_MODEL_ID
            if has_multimodal:
                log(f"{tag}MULTIMODAL detected -> model: {use_model}")
            clean = {"model": use_model, "messages": clean_msgs}
            if "max_tokens" in body:
                clean["max_tokens"] = body["max_tokens"]
            else:
                clean["max_tokens"] = 4096
            if "temperature" in body:
                clean["temperature"] = body["temperature"]
            if "tools" in body:
                clean["tools"] = body["tools"]
            if "tool_choice" in body:
                clean["tool_choice"] = body["tool_choice"]
            if "stream" in body:
                clean["stream"] = body["stream"]

            log(f"{tag}CLEAN KEYS: {list(clean.keys())}")
            log(f"{tag}MSG COUNT: {len(clean_msgs)}, ROLES: {[m['role'] for m in clean_msgs]}")

            # --- Workload routing: batch (pure-inference, no tools) → fast provider ---
            # V37.9.221: 决策在 _classify_fast_route (纯函数, 可单测). no-tools=批量→fast;
            #   PA(有 tools)→primary. 根因 2026-07-02 reasoning-primary 拖垮批量 job 事故
            #   (reasoning_model_primary_breaks_batch_jobs_case.md). no-op until flip:
            #   FAST_ROUTE 仅 FAST_PROVIDER≠PROVIDER 时非空.
            use_fast = False
            _fr_type = _classify_fast_route(clean, clean_msgs, has_multimodal, use_model, primary_name)
            if _fr_type == "workload":
                use_fast = True
                log(f"{tag}WORKLOAD ROUTE: pure-inference (no tools) -> {FAST_ROUTE['name']}/{FAST_ROUTE['model_id']}")
            elif _fr_type == "smart":
                use_fast = True
                log(f"{tag}SMART ROUTE: simple -> {FAST_ROUTE['name']}/{FAST_ROUTE['model_id']}")

            # --- V37.9.222 B1: batch reasoning-off injection ---
            # 决策在 _batch_reasoning_off_body (纯函数, 可单测). 批量 (no-tools) 且服务 provider
            # 支持关 reasoning → 注入 reasoning_off_body, 让 reasoning primary 也能快速服务批量
            # (无独立快 provider 时的终局路径). qwen 无 body → no-op.
            _is_batch = _is_batch_workload(clean, has_multimodal, use_model, primary_name)
            _rob = _batch_reasoning_off_body(clean, has_multimodal, use_model, primary_name, use_fast)
            if _rob:
                _serving = FAST_PROVIDER_NAME if use_fast else primary_name
                for _k, _v in _rob.items():
                    clean[_k] = _v
                log(f"{tag}B1 REASONING-OFF: batch on {_serving} -> inject {list(_rob)}")

            data = json.dumps(clean).encode()
            log(f"{tag}FORWARDING: {len(data)} bytes")

            # --- Primary request with fallback + circuit breaker ---
            if use_fast:
                fr = FAST_ROUTE
                fast_clean = dict(clean)
                fast_clean["model"] = fr["model_id"]
                fast_data = json.dumps(fast_clean).encode()
                fast_url = f"{fr['base_url']}{path}"
                primary_auth = _make_auth_headers(fr["auth_style"], fr["api_key"])
                url = fast_url
                data = fast_data
            else:
                # V37.9.77 enforcement: 用 _resolve_primary_provider 解析的 auth
                # (override 时是 override 的 auth, 否则是默认 AUTH_STYLE/API_KEY)
                primary_auth = _make_auth_headers(primary_auth_style, primary_api_key)

            # Circuit breaker: 短路时跳过 primary，直接走 fallback chain
            cb_open = FALLBACK_CHAIN and _circuit_breaker.is_open()
            if cb_open:
                log(f"{tag}CIRCUIT BREAKER OPEN: skipping primary, direct fallback chain ({len(FALLBACK_CHAIN)} providers)")

            primary_err = None
            if not cb_open:
                try:
                    status, resp_body = _forward_request(url, data, primary_auth, timeout=int(_PRIMARY_TIMEOUT))
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}RESPONSE: {status} ({len(resp_body)} bytes) {elapsed}ms")
                    _circuit_breaker.record_success()
                    # V37.9.231 (审计 finding E): 回写经 _deliver — 此前 _send_json 在本
                    # try 内, client 断开 (BrokenPipe) 被 except 当 backend 失败 →
                    # 健康 backend 被记 CB failure + 对死 socket 跑整条 fallback 链
                    # (V37.9.220 实录: 每个 FALLBACK OK 后 1ms 内 FAILED Broken pipe)。
                    self._deliver(status, resp_body, tag=tag)
                    return
                except Exception as err:
                    primary_err = err
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}PRIMARY FAILED ({elapsed}ms): {primary_err}")
                    _circuit_breaker.record_failure()

            if not FALLBACK_CHAIN:
                log(f"{tag}NO FALLBACK CHAIN configured, returning 502")
                self._deliver(502, json.dumps({"error": str(primary_err or "circuit breaker open")}).encode(), tag=tag)
                return

            # --- Fallback chain: try each provider sequentially ---
            fb_errors = [f"primary: {primary_err or 'circuit open'}"]
            for fb in FALLBACK_CHAIN:
                # V37.9.218: capability-aware vision fallback — image 请求需要 vision 模型。
                # 纯文本 provider (无 vl_model_id) 对 image 请求必然失败 → 跳过, 不浪费尝试。
                # vision-capable provider 用其 vl_model_id (qwen 用独立 VL 模型 / doubao 单模型多模态)。
                if has_multimodal:
                    fb_vl = fb.get("vl_model_id", "")
                    if not fb_vl:
                        log(f"{tag}FALLBACK skip {fb['name']} (纯文本, image 请求跳过)")
                        fb_errors.append(f"{fb['name']}: skipped (text-only, image request)")
                        continue
                    fb_model = fb_vl
                else:
                    fb_model = fb["model_id"]

                fb_url = f"{fb['base_url']}{path}"
                # V37.9.224 B1 fallback 传播: 批量按 fallback provider 自己的 reasoning_off_body
                # 重算注入 (剥 serving 注入的 keys + 注入 fb 自己的片段), 非批量原样.
                fb_clean = _fallback_batch_body(clean, fb, _is_batch, _rob)
                if _is_batch:
                    _fb_rob = fb.get("reasoning_off_body") or None
                    if _fb_rob:
                        log(f"{tag}B1 REASONING-OFF: fallback {fb['name']} -> inject {list(_fb_rob)}")
                    elif _rob:
                        log(f"{tag}B1 REASONING-OFF: fallback {fb['name']} -> strip {list(_rob)}")
                fb_clean["model"] = fb_model
                fb_data = json.dumps(fb_clean).encode()
                fb_auth = _make_auth_headers(fb["auth_style"], fb["api_key"])

                log(f"{tag}FALLBACK -> {fb['name']} ({fb_model})")
                try:
                    fb_status, fb_body = _forward_request(fb_url, fb_data, fb_auth, timeout=int(_FALLBACK_TIMEOUT))
                    fb_elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}FALLBACK OK: {fb_status} ({len(fb_body)} bytes) {fb_elapsed}ms via {fb['name']}")
                    # V37.9.229 (审计 finding A): 降级服务的响应带 X-Adapter-Fallback,
                    # 上游 proxy 读之调 record_fallback → SLO degradation_rate 见真值
                    # (此前 proxy 只见 200, V37.9.220 primary 全宕 100% fallback 时
                    # SLO 仍报 healthy = fail-plausible SLO)。primary 成功/502 不带。
                    # V37.9.231 (审计 finding E): 回写经 _deliver — 此前 client 断开被
                    # 本 except 当 fallback 失败 → 继续对死 socket 试下一个 fallback
                    # (每个都上游成功+回写失败) 直到链耗尽, 纯浪费上游调用/配额/时间。
                    self._deliver(fb_status, fb_body,
                                  extra_headers={"X-Adapter-Fallback": fb["name"]}, tag=tag)
                    return
                except Exception as fb_err:
                    fb_elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}FALLBACK {fb['name']} FAILED ({fb_elapsed}ms): {fb_err}")
                    fb_errors.append(f"{fb['name']}: {fb_err}")

            # All providers in chain failed
            log(f"{tag}ALL {len(FALLBACK_CHAIN)} FALLBACKS FAILED")
            self._deliver(502, json.dumps({"error": "; ".join(fb_errors)}).encode(), tag=tag)
            return

        # --- Non-chat endpoints: no fallback ---
        req = Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        add_auth(req)
        req.add_header("User-Agent", "curl/8.0")
        try:
            with _safe_urlopen(req, timeout=300, context=ctx) as resp:
                resp_body = resp.read()
                elapsed = int((time.monotonic() - t0) * 1000)
                log(f"{tag}RESPONSE: {resp.status} ({len(resp_body)} bytes) {elapsed}ms")
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            log(f"{tag}FORWARD ERROR ({elapsed}ms): {e}")
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _send_json(self, status, body, extra_headers=None):
        """Helper to send a JSON response.

        V37.9.229 (审计 finding A): extra_headers 可选附加响应头 — fallback 成功
        路径用 X-Adapter-Fallback 把降级事件 surface 给上游 tool_proxy 的
        proxy_stats (record_fallback 复活, degradation_rate 不再结构性 0%)。
        """
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for _hk, _hv in extra_headers.items():
                self.send_header(_hk, _hv)
        self.end_headers()
        self.wfile.write(body)

    def _deliver(self, status, body, extra_headers=None, tag=""):
        """V37.9.231 (审计 finding E): 回写响应给 client — 投递失败 ≠ backend 失败。

        client 已超时断开 (V37.9.220: cron job HTTP client 超时先走) 时, 回写撞
        BrokenPipeError/ConnectionResetError (OSError 族)。此前该异常泄漏进
        _forward_request 的 try/except → 两个 defect: ① 健康 backend 被记
        _circuit_breaker.record_failure() (误开断路器) ② 对死 socket 跑完整条
        fallback 链 (每个 fallback 上游成功 + 回写失败, 浪费 ~500s tail + 配额)。
        修复: 投递单独兜异常, client 断开只记日志绝不冒泡。仅捕 OSError
        (写 socket 的失败族); 编程错误 (TypeError 等) 照常传播可见。
        Returns True=已投递 / False=client 已断开。
        """
        try:
            self._send_json(status, body, extra_headers=extra_headers)
            return True
        except OSError as werr:
            log(f"{tag}CLIENT GONE: response ready but client disconnected ({werr}) — 不触发 fallback/不记 CB failure")
            return False

_chain_names = [fb["name"] for fb in FALLBACK_CHAIN]
fb_info = f" (fallback chain: {' -> '.join(_chain_names)})" if FALLBACK_CHAIN else " (no fallback)"
vl_info = f" (VL: {VL_MODEL_ID})" if VL_MODEL_ID else ""
fast_info = f" (fast: {FAST_ROUTE['name']}/{FAST_ROUTE['model_id']})" if FAST_ROUTE else ""
BIND_ADDR = os.environ.get("BIND_ADDR", "127.0.0.1")
class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

# V37.9.77: server startup 移到 __main__ 块, 让 test 可 import adapter 不绑端口
if __name__ == "__main__":
    reload_info = f" (hot-reload: every {_HOT_RELOAD_INTERVAL}s)" if _HOT_RELOAD_ENABLED else ""
    log(f"Starting on {BIND_ADDR}:{PORT} -> {TARGET_BASE} (model: {REAL_MODEL_ID}){vl_info}{fb_info}{fast_info}{reload_info}")
    sys.stdout.flush()
    with ThreadedServer((BIND_ADDR, PORT), ProxyHandler) as httpd:
        httpd.serve_forever()
