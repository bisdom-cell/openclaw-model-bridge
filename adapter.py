#!/usr/bin/env python3
import http.server, socketserver, json, ssl, sys, os, threading, time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Provider registry — add new providers here, no other code changes needed
# ---------------------------------------------------------------------------
PROVIDERS = {
    "qwen": {
        "base_url":    "https://hkagentx.hkopenlab.com/v1",
        "api_key_env": "REMOTE_API_KEY",
        "model_id":    "Qwen3-235B-A22B-Instruct-2507-W8A8",
        "vl_model_id": "Qwen2.5-VL-72B-Instruct",   # Vision-Language model on same endpoint
        "auth_style":  "bearer",      # Authorization: Bearer <key>
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
        "auth_style":  "x-api-key",   # x-api-key: <key> + anthropic-version header
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

def _forward_request(url, data, auth_headers, timeout=300):
    """Send POST request and return (status, body). Raises on failure."""
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "curl/8.0")
    for k, v in auth_headers.items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.status, resp.read()

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        # Local health check — never forward to remote
        if self.path in ("/health", "/v1/health"):
            info = {"ok": True, "provider": PROVIDER_NAME, "model": REAL_MODEL_ID}
            if VL_MODEL_ID:
                info["vl_model"] = VL_MODEL_ID
            if FALLBACK:
                info["fallback"] = FALLBACK["name"]
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
            with urlopen(req, timeout=30, context=ctx) as resp:
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
            data = json.dumps(clean).encode()
            log(f"{tag}FORWARDING: {len(data)} bytes")

            # --- Primary request with fallback ---
            primary_auth = _make_auth_headers(AUTH_STYLE, API_KEY)
            try:
                status, resp_body = _forward_request(url, data, primary_auth)
                elapsed = int((time.monotonic() - t0) * 1000)
                log(f"{tag}RESPONSE: {status} ({len(resp_body)} bytes) {elapsed}ms")
                self._send_json(status, resp_body)
            except Exception as primary_err:
                elapsed = int((time.monotonic() - t0) * 1000)
                log(f"{tag}PRIMARY FAILED ({elapsed}ms): {primary_err}")

                if not FALLBACK:
                    log(f"{tag}NO FALLBACK configured, returning 502")
                    self._send_json(502, json.dumps({"error": str(primary_err)}).encode())
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
                    fb_status, fb_body = _forward_request(fb_url, fb_data, fb_auth)
                    fb_elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}FALLBACK OK: {fb_status} ({len(fb_body)} bytes) {fb_elapsed}ms")
                    self._send_json(fb_status, fb_body)
                except Exception as fb_err:
                    fb_elapsed = int((time.monotonic() - t0) * 1000)
                    log(f"{tag}FALLBACK ALSO FAILED ({fb_elapsed}ms): {fb_err}")
                    self._send_json(502, json.dumps({
                        "error": f"primary: {primary_err}; fallback({fb['name']}): {fb_err}"
                    }).encode())
            return

        # --- Non-chat endpoints: no fallback ---
        req = Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        add_auth(req)
        req.add_header("User-Agent", "curl/8.0")
        try:
            with urlopen(req, timeout=300, context=ctx) as resp:
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
log(f"Starting on :{PORT} -> {TARGET_BASE} (model: {REAL_MODEL_ID}){vl_info}{fb_info}")
sys.stdout.flush()
class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

with ThreadedServer(("", PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
