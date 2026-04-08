#!/usr/bin/env python3
"""
providers.py — Provider Compatibility Layer (V34: Stage2)

统一的 Provider 抽象层，将硬编码的 provider 字典升级为可插拔的接口。
每个 Provider 声明自己的能力（capabilities）、认证方式、模型列表和限制，
为兼容性矩阵和自动化评测提供基础。

设计原则：
- 向后兼容：导出 PROVIDERS dict，adapter.py 无感知切换
- 单文件：适配 auto_deploy FILE_MAP 同步，无需目录结构
- 能力声明：每个 Provider 显式声明支持的功能，而非运行时探测
- 可扩展：新 Provider 只需继承 BaseProvider 并注册
"""
import importlib.util
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability declarations — 每个 Provider 显式声明支持的能力
# ---------------------------------------------------------------------------
@dataclass
class ProviderCapabilities:
    """Provider 能力声明，用于兼容性矩阵和路由决策。"""
    # 模态支持
    text: bool = True
    vision: bool = False
    audio: bool = False
    video: bool = False

    # 功能支持
    tool_calling: bool = False
    streaming: bool = False
    json_mode: bool = False

    # 限制
    context_window: int = 0          # 最大上下文窗口 (tokens)
    max_output_tokens: int = 0       # 最大输出 tokens
    rate_limit_rpm: int = 0          # 每分钟请求限制 (0 = unknown)

    # 验证状态（实际测试过的才标 True）
    verified_text: bool = False
    verified_vision: bool = False
    verified_tool_calling: bool = False
    verified_streaming: bool = False
    verified_fallback: bool = False

    def supported_modalities(self) -> List[str]:
        """返回支持的模态列表。"""
        mods = []
        if self.text: mods.append("text")
        if self.vision: mods.append("vision")
        if self.audio: mods.append("audio")
        if self.video: mods.append("video")
        return mods

    def verified_features(self) -> List[str]:
        """返回已验证的功能列表。"""
        features = []
        if self.verified_text: features.append("text")
        if self.verified_vision: features.append("vision")
        if self.verified_tool_calling: features.append("tool_calling")
        if self.verified_streaming: features.append("streaming")
        if self.verified_fallback: features.append("fallback")
        return features


@dataclass
class ModelInfo:
    """单个模型的信息。"""
    model_id: str
    display_name: str = ""
    modalities: List[str] = field(default_factory=lambda: ["text"])
    context_window: int = 0
    max_output_tokens: int = 0
    is_default: bool = False
    is_vision: bool = False


# ---------------------------------------------------------------------------
# Base Provider — 所有 Provider 的抽象基类
# ---------------------------------------------------------------------------
class BaseProvider:
    """Provider 基类，定义标准接口。

    子类必须实现:
    - name: str
    - display_name: str
    - base_url: str
    - api_key_env: str
    - auth_style: str ("bearer" | "x-api-key")
    - models: List[ModelInfo]
    - capabilities: ProviderCapabilities
    """
    name: str = ""
    display_name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    auth_style: str = "bearer"
    models: List[ModelInfo] = []
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def default_model(self) -> Optional[ModelInfo]:
        """返回默认模型。"""
        for m in self.models:
            if m.is_default:
                return m
        return self.models[0] if self.models else None

    def vision_model(self) -> Optional[ModelInfo]:
        """返回视觉模型（如果有）。"""
        for m in self.models:
            if m.is_vision:
                return m
        return None

    @property
    def model_id(self) -> str:
        """默认模型 ID（兼容旧 PROVIDERS dict）。"""
        dm = self.default_model()
        return dm.model_id if dm else ""

    @property
    def vl_model_id(self) -> str:
        """视觉模型 ID（兼容旧 PROVIDERS dict）。"""
        vm = self.vision_model()
        return vm.model_id if vm else ""

    def make_auth_headers(self, api_key: str) -> Dict[str, str]:
        """生成认证头。子类可覆写以支持特殊认证。"""
        if self.auth_style == "x-api-key":
            return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {api_key}"}

    def to_legacy_dict(self) -> dict:
        """转换为旧格式 PROVIDERS dict entry（向后兼容）。"""
        d = {
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model_id": self.model_id,
            "auth_style": self.auth_style,
        }
        if self.vl_model_id:
            d["vl_model_id"] = self.vl_model_id
        return d

    def to_matrix_row(self) -> dict:
        """生成兼容性矩阵行。"""
        caps = self.capabilities
        return {
            "provider": self.display_name or self.name,
            "models": [m.model_id for m in self.models],
            "modalities": caps.supported_modalities(),
            "tool_calling": caps.tool_calling,
            "streaming": caps.streaming,
            "context_window": caps.context_window,
            "max_output_tokens": caps.max_output_tokens,
            "auth_style": self.auth_style,
            "verified": caps.verified_features(),
        }


# ---------------------------------------------------------------------------
# Provider Contract — registration-time validation
# ---------------------------------------------------------------------------
class ProviderContract:
    """Validates that a provider meets the minimum contract requirements.

    The contract ensures every registered provider has:
    - A unique name
    - A valid base_url
    - An api_key_env pointing to the credential
    - At least one model with a model_id
    - A recognized auth_style
    - Consistent capability declarations
    """
    VALID_AUTH_STYLES = {"bearer", "x-api-key", "query-param", "custom"}

    @classmethod
    def validate(cls, provider: BaseProvider) -> List[str]:
        """Validate provider contract. Returns list of violations (empty = valid)."""
        violations = []
        if not getattr(provider, 'name', ''):
            violations.append("name is required")
        if not getattr(provider, 'base_url', ''):
            violations.append("base_url is required")
        if not getattr(provider, 'api_key_env', ''):
            violations.append("api_key_env is required")
        models = getattr(provider, 'models', [])
        if not models:
            violations.append("at least one model is required")
        else:
            for i, m in enumerate(models):
                if not getattr(m, 'model_id', ''):
                    violations.append(f"models[{i}].model_id is required")
            defaults = [m for m in models if getattr(m, 'is_default', False)]
            if len(defaults) > 1:
                violations.append(f"only one model can be is_default (found {len(defaults)})")
        auth = getattr(provider, 'auth_style', 'bearer')
        if auth not in cls.VALID_AUTH_STYLES:
            violations.append(f"auth_style '{auth}' not in {sorted(cls.VALID_AUTH_STYLES)}")
        caps = getattr(provider, 'capabilities', None)
        if caps and getattr(caps, 'vision', False) and models:
            has_vision_model = any(
                getattr(m, 'is_vision', False) or 'vision' in getattr(m, 'modalities', [])
                for m in models
            )
            if not has_vision_model:
                violations.append("capabilities declares vision=True but no model supports vision")
        return violations


class ContractViolationError(ValueError):
    """Raised when a provider fails contract validation."""
    def __init__(self, provider_name: str, violations: List[str]):
        self.provider_name = provider_name
        self.violations = violations
        msg = f"Provider '{provider_name}' contract violations: {'; '.join(violations)}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Plugin Loader — discover and load providers from YAML/Python files
# ---------------------------------------------------------------------------
class PluginLoader:
    """Load provider plugins from YAML definitions or Python modules.

    Supports two formats:
    - YAML (.yaml/.yml): Declarative provider definition, no code needed.
      Covers 90% of use cases (OpenAI-compatible providers).
    - Python (.py): For providers needing custom auth or special behavior.
      Must export exactly one BaseProvider subclass.

    Files starting with '_' or '.' are skipped (reserved for examples/hidden).
    """

    @classmethod
    def from_yaml(cls, path: str) -> BaseProvider:
        """Load a provider from a YAML definition file.

        YAML format:
            name: deepseek
            display_name: DeepSeek
            base_url: https://api.deepseek.com/v1
            api_key_env: DEEPSEEK_API_KEY
            auth_style: bearer          # optional, default: bearer
            models:
              - model_id: deepseek-chat
                display_name: DeepSeek V3
                modalities: [text]
                context_window: 65536
                max_output_tokens: 8192
                is_default: true
            capabilities:               # optional
              text: true
              tool_calling: true
              streaming: true
        """
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML is required for YAML plugins: pip3 install pyyaml")

        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"YAML plugin must be a dict, got {type(data).__name__}")

        return cls._dict_to_provider(data, source=path)

    @classmethod
    def _dict_to_provider(cls, data: dict, source: str = "") -> BaseProvider:
        """Convert a dict (from YAML or JSON) to a BaseProvider instance."""
        # Build models
        models = []
        for md in data.get('models', []):
            models.append(ModelInfo(
                model_id=md.get('model_id', ''),
                display_name=md.get('display_name', ''),
                modalities=md.get('modalities', ['text']),
                context_window=md.get('context_window', 0),
                max_output_tokens=md.get('max_output_tokens', 0),
                is_default=md.get('is_default', False),
                is_vision=md.get('is_vision', False),
            ))

        # Build capabilities
        caps_data = data.get('capabilities', {})
        caps = ProviderCapabilities(
            text=caps_data.get('text', True),
            vision=caps_data.get('vision', False),
            audio=caps_data.get('audio', False),
            video=caps_data.get('video', False),
            tool_calling=caps_data.get('tool_calling', False),
            streaming=caps_data.get('streaming', False),
            json_mode=caps_data.get('json_mode', False),
            context_window=caps_data.get('context_window', 0),
            max_output_tokens=caps_data.get('max_output_tokens', 0),
        )

        # Create provider instance
        provider = BaseProvider()
        provider.name = data.get('name', '')
        provider.display_name = data.get('display_name', provider.name)
        provider.base_url = data.get('base_url', '')
        provider.api_key_env = data.get('api_key_env', '')
        provider.auth_style = data.get('auth_style', 'bearer')
        provider.models = models
        provider.capabilities = caps
        provider._plugin_source = source
        return provider

    @classmethod
    def from_python(cls, path: str) -> BaseProvider:
        """Load a provider from a Python module.

        The module must define exactly one class that inherits from BaseProvider.
        The class is instantiated with no arguments.
        """
        module_name = Path(path).stem
        spec = importlib.util.spec_from_file_location(
            f"provider_plugin_{module_name}", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load Python plugin: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Find BaseProvider subclasses defined in this module
        candidates = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                    and issubclass(attr, BaseProvider)
                    and attr is not BaseProvider
                    and attr.__module__ == module.__name__):
                candidates.append(attr)

        if not candidates:
            raise ValueError(f"No BaseProvider subclass found in {path}")
        if len(candidates) > 1:
            names = [c.__name__ for c in candidates]
            raise ValueError(f"Multiple BaseProvider subclasses in {path}: {names}. Expected exactly one.")

        provider = candidates[0]()
        provider._plugin_source = path
        return provider

    @classmethod
    def discover(cls, directory: str) -> List[Tuple[Optional[BaseProvider], Optional[str]]]:
        """Discover and load all provider plugins from a directory.

        Returns list of (provider, error) tuples.
        - On success: (provider, None)
        - On failure: (None, error_message)

        Files starting with '_' or '.' are skipped.
        """
        results = []
        dir_path = Path(directory)
        if not dir_path.is_dir():
            return results

        for f in sorted(dir_path.iterdir()):
            if f.name.startswith('_') or f.name.startswith('.'):
                continue
            try:
                if f.suffix in ('.yaml', '.yml'):
                    provider = cls.from_yaml(str(f))
                    results.append((provider, None))
                elif f.suffix == '.py':
                    provider = cls.from_python(str(f))
                    results.append((provider, None))
            except Exception as e:
                results.append((None, f"{f.name}: {e}"))
                logger.warning("Failed to load plugin %s: %s", f.name, e)

        return results


# ---------------------------------------------------------------------------
# Concrete Providers
# ---------------------------------------------------------------------------
class QwenProvider(BaseProvider):
    name = "qwen"
    display_name = "Qwen (Remote GPU)"
    base_url = "https://hkagentx.hkopenlab.com/v1"
    api_key_env = "REMOTE_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="Qwen3-235B-A22B-Instruct-2507-W8A8",
            display_name="Qwen3-235B (MoE, 22B active)",
            modalities=["text"],
            context_window=262144,
            max_output_tokens=8192,
            is_default=True,
        ),
        ModelInfo(
            model_id="Qwen2.5-VL-72B-Instruct",
            display_name="Qwen2.5-VL-72B (Vision)",
            modalities=["text", "vision"],
            context_window=32768,
            max_output_tokens=4096,
            is_vision=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=False,
        context_window=262144,
        max_output_tokens=8192,
        # 已验证的功能
        verified_text=True,
        verified_vision=True,
        verified_tool_calling=True,
        verified_streaming=True,
        verified_fallback=True,   # 作为 primary 被 fallback 过
    )


class OpenAIProvider(BaseProvider):
    name = "openai"
    display_name = "OpenAI"
    base_url = "https://api.openai.com/v1"
    api_key_env = "OPENAI_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="gpt-4o",
            display_name="GPT-4o",
            modalities=["text", "vision", "audio"],
            context_window=128000,
            max_output_tokens=16384,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=True,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        context_window=128000,
        max_output_tokens=16384,
        # 未在生产中验证
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
    )


class GeminiProvider(BaseProvider):
    name = "gemini"
    display_name = "Google Gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    api_key_env = "GEMINI_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            modalities=["text", "vision"],
            context_window=1048576,
            max_output_tokens=8192,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        context_window=1048576,
        max_output_tokens=8192,
        # 作为 fallback 验证过
        verified_text=True,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=True,    # 作为 fallback provider 验证过
    )


class ClaudeProvider(BaseProvider):
    name = "claude"
    display_name = "Anthropic Claude"
    base_url = "https://api.anthropic.com/v1"
    api_key_env = "ANTHROPIC_API_KEY"
    auth_style = "x-api-key"
    models = [
        ModelInfo(
            model_id="claude-sonnet-4-6",
            display_name="Claude Sonnet 4.6",
            modalities=["text", "vision"],
            context_window=200000,
            max_output_tokens=64000,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=False,
        context_window=200000,
        max_output_tokens=64000,
        # 未在生产中验证
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
    )


# ---------------------------------------------------------------------------
# Chinese Providers — 国内主流大模型
# ---------------------------------------------------------------------------
class KimiProvider(BaseProvider):
    """Moonshot AI (Kimi) — https://platform.moonshot.ai"""
    name = "kimi"
    display_name = "Kimi (Moonshot AI)"
    base_url = "https://api.moonshot.ai/v1"
    api_key_env = "MOONSHOT_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="kimi-k2.5",
            display_name="Kimi K2.5 (1T MoE, 32B active)",
            modalities=["text", "vision"],
            context_window=262144,
            max_output_tokens=32768,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        context_window=262144,
        max_output_tokens=32768,
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
    )


class MiniMaxProvider(BaseProvider):
    """MiniMax — https://www.minimax.io"""
    name = "minimax"
    display_name = "MiniMax"
    base_url = "https://api.minimaxi.com/v1"
    api_key_env = "MINIMAX_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="MiniMax-M2.7",
            display_name="MiniMax M2.7",
            modalities=["text", "vision"],
            context_window=204800,
            max_output_tokens=131072,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        context_window=204800,
        max_output_tokens=131072,
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
    )


class GLMProvider(BaseProvider):
    """Zhipu AI (GLM) — https://open.bigmodel.cn"""
    name = "glm"
    display_name = "GLM (Zhipu AI)"
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    api_key_env = "GLM_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="glm-5",
            display_name="GLM-5 (744B MoE, ~40B active)",
            modalities=["text"],
            context_window=202752,
            max_output_tokens=128000,
            is_default=True,
        ),
        ModelInfo(
            model_id="glm-5v-turbo",
            display_name="GLM-5V-Turbo (Vision)",
            modalities=["text", "vision"],
            context_window=202752,
            max_output_tokens=128000,
            is_vision=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        context_window=202752,
        max_output_tokens=128000,
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
    )


# ---------------------------------------------------------------------------
# Provider Registry — 动态注册 + 发现
# ---------------------------------------------------------------------------
class ProviderRegistry:
    """Provider 注册表，支持动态注册、合约验证和插件发现。"""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._plugin_errors: List[str] = []

    def register(self, provider: BaseProvider, validate: bool = True):
        """注册一个 Provider。

        Args:
            provider: Provider instance to register.
            validate: If True, run contract validation before registration.

        Raises:
            ContractViolationError: If validation is enabled and provider
                fails contract checks.
        """
        if validate:
            violations = ProviderContract.validate(provider)
            if violations:
                raise ContractViolationError(
                    getattr(provider, 'name', '<unknown>'), violations
                )
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> bool:
        """Remove a provider by name. Returns True if it existed."""
        return self._providers.pop(name, None) is not None

    def get(self, name: str) -> Optional[BaseProvider]:
        """按名获取 Provider。"""
        return self._providers.get(name)

    def list_names(self) -> List[str]:
        """返回所有已注册 Provider 名。"""
        return list(self._providers.keys())

    def all(self) -> List[BaseProvider]:
        """返回所有已注册 Provider。"""
        return list(self._providers.values())

    def load_plugins(self, directory: str) -> List[str]:
        """Discover and load all provider plugins from a directory.

        Returns list of error messages (empty = all loaded successfully).
        Plugins that conflict with built-in names are skipped with a warning.
        """
        errors = []
        results = PluginLoader.discover(directory)
        for provider, error in results:
            if error:
                errors.append(error)
                continue
            if provider is None:
                continue
            if provider.name in self._providers:
                src = getattr(provider, '_plugin_source', '?')
                errors.append(
                    f"{provider.name}: skipped (conflicts with built-in provider). "
                    f"Source: {src}"
                )
                logger.warning("Plugin '%s' skipped: conflicts with built-in", provider.name)
                continue
            try:
                self.register(provider, validate=True)
                logger.info("Loaded plugin provider: %s", provider.name)
            except ContractViolationError as e:
                errors.append(str(e))
        self._plugin_errors = errors
        return errors

    @property
    def plugin_errors(self) -> List[str]:
        """Errors from the last load_plugins() call."""
        return self._plugin_errors

    def to_legacy_dict(self) -> Dict[str, dict]:
        """转换为旧格式 PROVIDERS dict（向后兼容 adapter.py）。"""
        return {name: p.to_legacy_dict() for name, p in self._providers.items()}

    def compatibility_matrix(self) -> List[dict]:
        """生成完整兼容性矩阵。"""
        return [p.to_matrix_row() for p in self._providers.values()]

    def print_matrix(self):
        """打印兼容性矩阵（Markdown 格式）。"""
        matrix = self.compatibility_matrix()
        if not matrix:
            print("No providers registered.")
            return

        print("| Provider | Models | Modalities | Tool Calling | Streaming | Context | Verified |")
        print("|----------|--------|------------|-------------|-----------|---------|----------|")
        for row in matrix:
            models = ", ".join(row["models"][:2])
            if len(row["models"]) > 2:
                models += f" (+{len(row['models'])-2})"
            mods = ", ".join(row["modalities"])
            verified = ", ".join(row["verified"]) if row["verified"] else "none"
            ctx = f"{row['context_window']//1000}K" if row['context_window'] else "?"
            print(f"| {row['provider']} | {models} | {mods} | "
                  f"{'Yes' if row['tool_calling'] else 'No'} | "
                  f"{'Yes' if row['streaming'] else 'No'} | {ctx} | {verified} |")

    # ------------------------------------------------------------------
    # Capability-Based Routing — V37: query providers by features
    # ------------------------------------------------------------------
    def find_by_capability(self, **required) -> List[BaseProvider]:
        """Find providers matching ALL required capabilities.

        Accepts any ProviderCapabilities boolean field as a keyword argument.
        Example: registry.find_by_capability(vision=True, tool_calling=True)

        Returns matching providers (order preserved from registration).
        """
        results = []
        for p in self._providers.values():
            caps = p.capabilities
            match = True
            for key, value in required.items():
                if not hasattr(caps, key):
                    match = False
                    break
                if getattr(caps, key) != value:
                    match = False
                    break
            if match:
                results.append(p)
        return results

    def available(self) -> List[BaseProvider]:
        """Return providers that have API keys configured in environment."""
        return [
            p for p in self._providers.values()
            if os.environ.get(p.api_key_env, "")
        ]

    def _capability_score(self, provider: BaseProvider) -> int:
        """Score a provider by total capability count (for ranking)."""
        caps = provider.capabilities
        score = 0
        for attr in ('text', 'vision', 'audio', 'video',
                     'tool_calling', 'streaming', 'json_mode'):
            if getattr(caps, attr, False):
                score += 1
        for attr in ('verified_text', 'verified_vision', 'verified_tool_calling',
                     'verified_streaming', 'verified_fallback'):
            if getattr(caps, attr, False):
                score += 2  # verified features weigh more
        return score

    def _capability_overlap(self, a: BaseProvider, b: BaseProvider) -> int:
        """Count shared capabilities between two providers."""
        overlap = 0
        for attr in ('text', 'vision', 'audio', 'video',
                     'tool_calling', 'streaming', 'json_mode'):
            if getattr(a.capabilities, attr, False) and getattr(b.capabilities, attr, False):
                overlap += 1
        return overlap

    def build_fallback_chain(self, primary_name: str,
                             require_available: bool = False) -> List[BaseProvider]:
        """Auto-build a fallback chain for a primary provider.

        Returns providers sorted by:
        1. Capability overlap with primary (most similar first)
        2. Verified features (verified > unverified)
        3. Total capability score (more capable > less)

        Args:
            primary_name: Name of the primary provider to build chain for.
            require_available: If True, only include providers with API keys set.

        Excludes the primary provider itself.
        """
        primary = self._providers.get(primary_name)
        if primary is None:
            return []

        candidates = [
            p for p in self._providers.values()
            if p.name != primary_name
        ]

        if require_available:
            candidates = [p for p in candidates if os.environ.get(p.api_key_env, "")]

        def sort_key(p):
            overlap = self._capability_overlap(primary, p)
            verified_count = len(p.capabilities.verified_features())
            cap_score = self._capability_score(p)
            # Negative for descending sort
            return (-overlap, -verified_count, -cap_score)

        return sorted(candidates, key=sort_key)

    def capability_overlap(self, name_a: str, name_b: str) -> Dict[str, bool]:
        """Compare capabilities between two providers.

        Returns dict of {capability: both_support_it}.
        """
        a = self._providers.get(name_a)
        b = self._providers.get(name_b)
        if not a or not b:
            return {}
        result = {}
        for attr in ('text', 'vision', 'audio', 'video',
                     'tool_calling', 'streaming', 'json_mode'):
            a_has = getattr(a.capabilities, attr, False)
            b_has = getattr(b.capabilities, attr, False)
            result[attr] = a_has and b_has
        return result


# ---------------------------------------------------------------------------
# Default registry — 7 built-in providers + auto-discovered plugins
# ---------------------------------------------------------------------------
_default_registry = ProviderRegistry()
_default_registry.register(QwenProvider())
_default_registry.register(OpenAIProvider())
_default_registry.register(GeminiProvider())
_default_registry.register(ClaudeProvider())
_default_registry.register(KimiProvider())
_default_registry.register(MiniMaxProvider())
_default_registry.register(GLMProvider())

# Auto-discover plugins from providers.d/ (relative to this file)
_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.d")
if os.path.isdir(_PLUGIN_DIR):
    _plugin_errors = _default_registry.load_plugins(_PLUGIN_DIR)
    if _plugin_errors:
        for _err in _plugin_errors:
            logger.warning("Plugin load error: %s", _err)


def get_registry() -> ProviderRegistry:
    """获取默认 Provider 注册表。"""
    return _default_registry


def get_provider(name: str) -> Optional[BaseProvider]:
    """按名获取 Provider（快捷方法）。"""
    return _default_registry.get(name)


# ---------------------------------------------------------------------------
# 向后兼容 — 导出 PROVIDERS dict，adapter.py 可直接 from providers import PROVIDERS
# ---------------------------------------------------------------------------
PROVIDERS = _default_registry.to_legacy_dict()


# ---------------------------------------------------------------------------
# CLI: python3 providers.py → 打印兼容性矩阵
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        import json
        print(json.dumps(_default_registry.compatibility_matrix(), indent=2, ensure_ascii=False))
    elif "--fallback-chain" in sys.argv:
        # Show auto-generated fallback chain for a provider
        idx = sys.argv.index("--fallback-chain")
        primary = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "qwen"
        chain = _default_registry.build_fallback_chain(primary)
        avail = _default_registry.available()
        avail_names = {p.name for p in avail}
        print(f"# Fallback Chain for '{primary}'\n")
        primary_p = _default_registry.get(primary)
        if not primary_p:
            print(f"ERROR: provider '{primary}' not found")
            sys.exit(1)
        primary_caps = primary_p.capabilities.supported_modalities()
        print(f"Primary capabilities: {', '.join(primary_caps)}")
        print(f"Tool calling: {primary_p.capabilities.tool_calling}")
        print(f"Streaming: {primary_p.capabilities.streaming}\n")
        print(f"| # | Provider | Overlap | Verified | Available | Caps Lost |")
        print(f"|---|----------|---------|----------|-----------|-----------|")
        for i, p in enumerate(chain, 1):
            overlap = _default_registry.capability_overlap(primary, p.name)
            shared = sum(1 for v in overlap.values() if v)
            total = sum(1 for v in overlap.values() if getattr(primary_p.capabilities, list(overlap.keys())[list(overlap.values()).index(True)], False)) if any(overlap.values()) else 0
            lost = [k for k, v in overlap.items() if not v and getattr(primary_p.capabilities, k, False)]
            avail_mark = "yes" if p.name in avail_names else "no"
            verified_str = ", ".join(p.capabilities.verified_features()) or "none"
            lost_str = ", ".join(lost) if lost else "none"
            print(f"| {i} | {p.display_name} | {shared}/{len(overlap)} | {verified_str} | {avail_mark} | {lost_str} |")
    elif "--validate" in sys.argv:
        # Validate all registered providers
        print("# Provider Contract Validation\n")
        all_ok = True
        for p in _default_registry.all():
            violations = ProviderContract.validate(p)
            source = getattr(p, '_plugin_source', 'built-in')
            if violations:
                print(f"FAIL {p.name} ({source}): {'; '.join(violations)}")
                all_ok = False
            else:
                print(f"  OK {p.name} ({source})")
        if _default_registry.plugin_errors:
            print(f"\nPlugin load errors:")
            for err in _default_registry.plugin_errors:
                print(f"  ERROR {err}")
            all_ok = False
        print(f"\n{'All providers valid.' if all_ok else 'Some providers have issues.'}")
        sys.exit(0 if all_ok else 1)
    else:
        print("# Provider Compatibility Matrix\n")
        _default_registry.print_matrix()

        # Separate built-in from plugins
        builtins = [p for p in _default_registry.all() if not hasattr(p, '_plugin_source')]
        plugins = [p for p in _default_registry.all() if hasattr(p, '_plugin_source')]
        print(f"\nTotal: {len(_default_registry.list_names())} providers "
              f"({len(builtins)} built-in, {len(plugins)} plugins)")
        print(f"Names: {', '.join(_default_registry.list_names())}")

        if plugins:
            print(f"\n## Plugins\n")
            for p in plugins:
                print(f"- **{p.display_name}** ({p.name}) — {p._plugin_source}")

        if _default_registry.plugin_errors:
            print(f"\n## Plugin Errors\n")
            for err in _default_registry.plugin_errors:
                print(f"- {err}")

        # 验证摘要
        print("\n## Verification Status\n")
        for p in _default_registry.all():
            verified = p.capabilities.verified_features()
            total = len(p.capabilities.supported_modalities()) + \
                    sum([p.capabilities.tool_calling, p.capabilities.streaming])
            status = f"{len(verified)}/{total} verified" if total else "N/A"
            print(f"- **{p.display_name}**: {status} — {', '.join(verified) if verified else 'not yet tested'}")
