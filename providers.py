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
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


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
# Provider Registry — 动态注册 + 发现
# ---------------------------------------------------------------------------
class ProviderRegistry:
    """Provider 注册表，支持动态注册和按名查找。"""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider):
        """注册一个 Provider。"""
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[BaseProvider]:
        """按名获取 Provider。"""
        return self._providers.get(name)

    def list_names(self) -> List[str]:
        """返回所有已注册 Provider 名。"""
        return list(self._providers.keys())

    def all(self) -> List[BaseProvider]:
        """返回所有已注册 Provider。"""
        return list(self._providers.values())

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


# ---------------------------------------------------------------------------
# Default registry — 内置 4 个 Provider
# ---------------------------------------------------------------------------
_default_registry = ProviderRegistry()
_default_registry.register(QwenProvider())
_default_registry.register(OpenAIProvider())
_default_registry.register(GeminiProvider())
_default_registry.register(ClaudeProvider())


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
    else:
        print("# Provider Compatibility Matrix\n")
        _default_registry.print_matrix()
        print(f"\nTotal: {len(_default_registry.list_names())} providers registered")
        print(f"Names: {', '.join(_default_registry.list_names())}")

        # 验证摘要
        print("\n## Verification Status\n")
        for p in _default_registry.all():
            verified = p.capabilities.verified_features()
            total = len(p.capabilities.supported_modalities()) + \
                    sum([p.capabilities.tool_calling, p.capabilities.streaming])
            status = f"{len(verified)}/{total} verified" if total else "N/A"
            print(f"- **{p.display_name}**: {status} — {', '.join(verified) if verified else 'not yet tested'}")
