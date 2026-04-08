# Provider Plugin Guide

> How to add a new LLM provider to OpenClaw Model Bridge without modifying core code.

## Overview

The Provider Plugin Interface allows you to add new LLM providers by dropping a
file into the `providers.d/` directory. No core code changes required.

Two formats are supported:

| Format | Use When | Complexity |
|--------|----------|------------|
| **YAML** | Standard OpenAI-compatible API (90% of cases) | Drop a file |
| **Python** | Custom auth, special headers, dynamic behavior | Write a class |

## Quick Start: Add a Provider in 60 Seconds

### Option A: YAML (Recommended)

Create `providers.d/deepseek.yaml`:

```yaml
name: deepseek
display_name: DeepSeek
base_url: https://api.deepseek.com/v1
api_key_env: DEEPSEEK_API_KEY
auth_style: bearer

models:
  - model_id: deepseek-chat
    display_name: DeepSeek V3 (671B MoE)
    modalities: [text]
    context_window: 65536
    max_output_tokens: 8192
    is_default: true

capabilities:
  text: true
  tool_calling: true
  streaming: true
  json_mode: true
  context_window: 65536
  max_output_tokens: 8192
```

Set the API key:
```bash
export DEEPSEEK_API_KEY="sk-..."
```

Verify:
```bash
python3 providers.py --validate
```

### Option B: Python (Custom Auth)

Create `providers.d/custom.py`:

```python
from providers import BaseProvider, ModelInfo, ProviderCapabilities

class CustomProvider(BaseProvider):
    name = "custom"
    display_name = "Custom Provider"
    base_url = "https://api.custom.com/v1"
    api_key_env = "CUSTOM_API_KEY"
    auth_style = "custom"
    models = [
        ModelInfo(
            model_id="custom-v1",
            display_name="Custom Model V1",
            modalities=["text"],
            context_window=32768,
            max_output_tokens=4096,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        tool_calling=True,
        streaming=True,
    )

    def make_auth_headers(self, api_key: str):
        """Override for non-standard authentication."""
        return {"X-Custom-Auth": f"Token {api_key}"}
```

## Provider Contract

Every provider must satisfy these requirements:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Unique identifier (lowercase, no spaces) |
| `base_url` | Yes | — | API endpoint URL |
| `api_key_env` | Yes | — | Environment variable for the API key |
| `models` | Yes (>=1) | — | At least one model definition |
| `display_name` | No | `name` | Human-readable name |
| `auth_style` | No | `"bearer"` | `"bearer"`, `"x-api-key"`, `"query-param"`, or `"custom"` |
| `capabilities` | No | text-only | Feature declarations |

### Model Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `model_id` | Yes | — | Model identifier sent to the API |
| `display_name` | No | `""` | Human-readable name |
| `modalities` | No | `["text"]` | Supported modalities: `text`, `vision`, `audio`, `video` |
| `context_window` | No | `0` | Max context tokens |
| `max_output_tokens` | No | `0` | Max output tokens |
| `is_default` | No | `false` | Primary model (only one per provider) |
| `is_vision` | No | `false` | Dedicated vision model |

### Capability Fields

| Field | Type | Description |
|-------|------|-------------|
| `text` | bool | Text generation |
| `vision` | bool | Image understanding (requires a vision-capable model) |
| `audio` | bool | Audio processing |
| `video` | bool | Video processing |
| `tool_calling` | bool | Function/tool calling |
| `streaming` | bool | SSE streaming |
| `json_mode` | bool | Structured JSON output |
| `context_window` | int | Max context tokens (provider-level) |
| `max_output_tokens` | int | Max output tokens (provider-level) |

### Contract Validation Rules

1. `name` must be non-empty
2. `base_url` must be non-empty
3. `api_key_env` must be non-empty
4. At least one model with a non-empty `model_id`
5. At most one model with `is_default: true`
6. `auth_style` must be one of the recognized values
7. If `capabilities.vision` is true, at least one model must support vision

Run `python3 providers.py --validate` to check all providers.

## File Naming Rules

| Pattern | Behavior |
|---------|----------|
| `providers.d/deepseek.yaml` | Auto-discovered and loaded |
| `providers.d/custom.py` | Auto-discovered and loaded |
| `providers.d/_example.yaml` | Skipped (starts with `_`) |
| `providers.d/.hidden.yaml` | Skipped (starts with `.`) |
| `providers.d/readme.txt` | Skipped (not `.yaml`/`.yml`/`.py`) |

## Integration with the System

### How It Works

```
providers.py (import time)
    |
    +-- Register 7 built-in providers (Qwen, OpenAI, Gemini, Claude, Kimi, MiniMax, GLM)
    |
    +-- Scan providers.d/ for plugin files
    |    +-- Load each .yaml/.yml/.py file
    |    +-- Validate against ProviderContract
    |    +-- Skip files that conflict with built-in names
    |    +-- Register valid plugins
    |
    +-- Export PROVIDERS dict (built-in + plugins)
         |
         +-- adapter.py imports PROVIDERS → routing includes your provider
```

### Using Your Provider

After adding a plugin, set it as the active provider:

```bash
# As primary provider
export PROVIDER_NAME=deepseek
export DEEPSEEK_API_KEY="sk-..."

# As fallback provider
export FALLBACK_PROVIDER=deepseek
export DEEPSEEK_API_KEY="sk-..."
```

### Compatibility Matrix

Your provider automatically appears in the compatibility matrix:

```bash
python3 providers.py          # Markdown table
python3 providers.py --json   # JSON format
```

## Advanced: Python Plugin Features

### Custom Authentication

Override `make_auth_headers()` for non-standard auth:

```python
def make_auth_headers(self, api_key: str):
    import hashlib, time
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{api_key}:{ts}".encode()).hexdigest()
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Timestamp": ts,
        "X-Signature": sig,
    }
```

### Multiple Models with Vision

```python
models = [
    ModelInfo(
        model_id="my-text-v1",
        display_name="Text Model",
        modalities=["text"],
        context_window=128000,
        is_default=True,
    ),
    ModelInfo(
        model_id="my-vision-v1",
        display_name="Vision Model",
        modalities=["text", "vision"],
        context_window=32768,
        is_vision=True,
    ),
]
```

## Troubleshooting

### Plugin not loading?

```bash
# Check for errors
python3 providers.py --validate

# Check if providers.d/ exists
ls providers.d/

# Check file naming (no leading _ or .)
ls -la providers.d/
```

### Name conflict?

Plugin names must be unique and cannot override built-in providers.
If you need to replace a built-in, modify `providers.py` directly.

### Missing PyYAML?

```bash
pip3 install pyyaml
```

### Contract violation?

Run `--validate` to see specific violations:

```
$ python3 providers.py --validate
FAIL myprov (providers.d/myprov.yaml): name is required; at least one model is required
```

## API Reference

### Classes

| Class | Purpose |
|-------|---------|
| `BaseProvider` | Base class for all providers |
| `ModelInfo` | Model metadata (dataclass) |
| `ProviderCapabilities` | Feature declarations (dataclass) |
| `ProviderContract` | Contract validation |
| `PluginLoader` | YAML/Python plugin loading |
| `ProviderRegistry` | Registration and discovery |
| `ContractViolationError` | Raised on contract failure |

### Key Functions

```python
from providers import get_registry, get_provider, PROVIDERS

# Get the registry (includes built-in + plugins)
registry = get_registry()

# Get a specific provider
provider = get_provider("deepseek")

# Legacy dict (backward compatible with adapter.py)
providers_dict = PROVIDERS
```

### CLI

```bash
python3 providers.py              # Compatibility matrix (Markdown)
python3 providers.py --json        # Compatibility matrix (JSON)
python3 providers.py --validate    # Contract validation for all providers
```
