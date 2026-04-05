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
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Load fallback config from config.yaml (optional, graceful fallback)
# ---------------------------------------------------------------------------
_FALLBACK_CFG = {}
try:
    from config_loader import load_config
    _cfg = load_config()
    _FALLBACK_CFG = _cfg.get("fallback", {}).get("remote_gpu", {})
except Exception:
    pass  # config_loader 不可用时使用默认值

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
ctx         = ssl.create_default_context()

# ---------------------------------------------------------------------------
# Fallback provider (optional, for chat/completions only)
# ---------------------------------------------------------------------------
FALLBACK_NAME = os.environ.get("FALLBACK_PROVIDER", "gemini")
_fb = PROVIDERS.get(FALLBACK_NAME)
if _fb and FALLBACK_NAME != PROVIDER_NAME:
    _fb_key = os.environ.get(_fb["api_key_env"], "")
    if _fb_key:
        FALLBACK = {
            "name":       FALLBACK_NAME,
            "base_url":   _fb["base_url"],
            "api_key":    _fb_key,
            "model_id":   os.environ.get("FALLBACK_MODEL_ID", _fb["model_id"]),
            "auth_style": _fb["auth_style"],
        }
    else:
        FALLBACK = None
else:
    FALLBACK = None

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
            if FALLBACK:
                info["fallback"] = FALLBACK["name"]
                info["circuit_breaker"] = _circuit_breaker.state()
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

    def do_POST(self):
        rid = self.headers.get("X-Request-ID", "")
        tag = f"[{rid}] " if rid else ""
        t0 = time.monotonic()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        path = self.path.replace("/v1", "", 1) if self.path.startswith("/v1") else self.path
        url = f"{TARGET_BASE}{path}"
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

            # --- Smart routing: simple queries → fast model ---
            use_fast = False
            if (FAST_ROUTE and classify_complexity
                    and not has_multimodal
                    and use_model == REAL_MODEL_ID):
                complexity = classify_complexity(clean_msgs, has_tools="tools" in clean)
                if complexity == "simple":
                    use_fast = True
                    log(f"{tag}SMART ROUTE: simple -> {FAST_ROUTE['name']}/{FAST_ROUTE['model_id']}")

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
                primary_auth = _make_auth_headers(AUTH_STYLE, API_KEY)

            # Circuit breaker: 短路时跳过 primary，直接走 fallback
            cb_open = FALLBACK and _circuit_breaker.is_open()
            if cb_open:
                log(f"{tag}CIRCUIT BREAKER OPEN: skipping primary, direct fallback")

            primary_err = None
            if not cb_open:
                try:
                    status, resp_body = _forward_request(url, data, primary_auth, timeout=int(_PRIMARY_TIMEOUT))
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}RESPONSE: {status} ({len(resp_body)} bytes) {elapsed}ms")
                    _circuit_breaker.record_success()
                    self._send_json(status, resp_body)
                    return
                except Exception as err:
                    primary_err = err
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}PRIMARY FAILED ({elapsed}ms): {primary_err}")
                    _circuit_breaker.record_failure()

            if not FALLBACK:
                log(f"{tag}NO FALLBACK configured, returning 502")
                self._send_json(502, json.dumps({"error": str(primary_err or "circuit breaker open")}).encode())
                return

            # --- Fallback attempt ---
            fb = FALLBACK
            fb_url = f"{fb['base_url']}{path}"
            fb_clean = dict(clean)
            fb_clean["model"] = fb["model_id"]
            fb_data = json.dumps(fb_clean).encode()
            fb_auth = _make_auth_headers(fb["auth_style"], fb["api_key"])

            log(f"{tag}FALLBACK -> {fb['name']} ({fb['model_id']}) {fb_url}")
            try:
                fb_status, fb_body = _forward_request(fb_url, fb_data, fb_auth, timeout=int(_FALLBACK_TIMEOUT))
                fb_elapsed = int((time.monotonic() - t0) * 1000)
                log(f"{tag}FALLBACK OK: {fb_status} ({len(fb_body)} bytes) {fb_elapsed}ms")
                self._send_json(fb_status, fb_body)
            except Exception as fb_err:
                fb_elapsed = int((time.monotonic() - t0) * 1000)
                log(f"{tag}FALLBACK ALSO FAILED ({fb_elapsed}ms): {fb_err}")
                self._send_json(502, json.dumps({
                    "error": f"primary: {primary_err or 'circuit open'}; fallback({fb['name']}): {fb_err}"
                }).encode())
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

    def _send_json(self, status, body):
        """Helper to send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

fb_info = f" (fallback: {FALLBACK['name']}/{FALLBACK['model_id']})" if FALLBACK else " (no fallback)"
vl_info = f" (VL: {VL_MODEL_ID})" if VL_MODEL_ID else ""
fast_info = f" (fast: {FAST_ROUTE['name']}/{FAST_ROUTE['model_id']})" if FAST_ROUTE else ""
BIND_ADDR = os.environ.get("BIND_ADDR", "127.0.0.1")
log(f"Starting on {BIND_ADDR}:{PORT} -> {TARGET_BASE} (model: {REAL_MODEL_ID}){vl_info}{fb_info}{fast_info}")
sys.stdout.flush()
class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

with ThreadedServer((BIND_ADDR, PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
