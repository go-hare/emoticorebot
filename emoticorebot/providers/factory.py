"""Model factories for front and agent runtimes.

`LLMFactory` builds the user-facing front model with LangChain.
`AgentsModelFactory` builds the backend Core/Sleep model bundle for OpenAI Agents SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents import ModelSettings
from agents.extensions.models.litellm_model import LitellmModel
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from emoticorebot.config.schema import ModelModeConfig, ProvidersConfig

# ── 各 OpenAI 兼容 provider 的默认 API base URL ───────────────────────────────
_PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "minimax": "https://api.minimax.chat/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "aihubmix": "https://aihubmix.com/v1",
}

_PREFIX_PROVIDERS = {"anthropic", "gemini", "groq"}


@dataclass(slots=True)
class AgentModelBundle:
    model: Any
    model_settings: ModelSettings


class LLMFactory:
    """根据 brain / executor 模式配置和 provider 凭证构建 LangChain chat model。

    Usage::

        factory = LLMFactory(
            providers_config=config.providers,
            executor_mode=config.agents.defaults.executor_mode,
            brain_mode=config.agents.defaults.brain_mode,
        )
        executor_llm = factory.get_executor()
        brain_llm = factory.get_brain()
    """

    def __init__(
        self,
        providers_config: "ProvidersConfig",
        executor_mode: "ModelModeConfig",
        brain_mode: "ModelModeConfig",
    ) -> None:
        self._providers = providers_config
        self._executor = executor_mode
        self._brain = brain_mode

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_executor(self) -> Any:
        """构建 executor 执行模型实例。"""
        return self._build(self._executor)

    def get_brain(self) -> Any:
        """构建 brain 模型实例。"""
        return self._build(self._brain)

    # ── 内部构建逻辑 ──────────────────────────────────────────────────────────

    def _detect_provider(self, model: str) -> str:
        """根据模型名称自动推断 provider。"""
        m = model.lower()
        # OpenRouter 格式：含斜杠，如 "anthropic/claude-opus-4-5"
        if "/" in m:
            return "openrouter"
        if m.startswith(("claude-", "claude.")):
            return "anthropic"
        if m.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
            return "openai"
        if m.startswith("gemini-"):
            return "gemini"
        if m.startswith("deepseek-"):
            return "openai"
        if m.startswith(("moonshot-", "kimi-")):
            return "openai"
        if m.startswith("glm-"):
            return "openai"
        if m.startswith(("qwen-", "qwen2", "qwen3")):
            return "openai"    
        if m.startswith(("llama", "mistral", "mixtral", "gemma")):
            return "openai"
        return "openai"

    def _build(self, mode: "ModelModeConfig") -> Any:
        """根据 ModelModeConfig 和 ProvidersConfig 构建 LangChain chat model。"""
        model = mode.model
        provider = mode.provider
        temperature = mode.temperature
        max_tokens = mode.max_tokens

        if provider == "auto":
            provider = self._detect_provider(model)

        # ── Ollama ────────────────────────────────────────────────────────────
        if provider == "ollama":
            cfg = getattr(self._providers, "ollama", None)
            base_url = (cfg.api_base if cfg and cfg.api_base else None) or "http://localhost:11434"
            return ChatOllama(model=model, base_url=base_url, reasoning=False)

        # ── Anthropic ─────────────────────────────────────────────────────────
        if provider == "anthropic":
            cfg = self._providers.anthropic
            kwargs: dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if cfg.api_key:
                kwargs["api_key"] = cfg.api_key
            if cfg.api_base:
                kwargs["base_url"] = cfg.api_base
            return ChatAnthropic(**kwargs)

        # ── Google Gemini ─────────────────────────────────────────────────────
        if provider == "gemini":
            cfg = self._providers.gemini
            kwargs = {"model": model, "temperature": temperature, "max_output_tokens": max_tokens}
            if cfg.api_key:
                kwargs["google_api_key"] = cfg.api_key
            return ChatGoogleGenerativeAI(**kwargs)

        # ── Groq ──────────────────────────────────────────────────────────────
        if provider == "groq":
            cfg = self._providers.groq
            kwargs = {"model": model, "temperature": temperature, "max_tokens": max_tokens}
            if cfg.api_key:
                kwargs["api_key"] = cfg.api_key
            return ChatGroq(**kwargs)

        # ── 所有 OpenAI 兼容 provider ─────────────────────────────────────────
        provider_cfg = getattr(self._providers, provider, None)
        api_key = (provider_cfg.api_key or None) if provider_cfg else None
        api_base = (provider_cfg.api_base or None) if provider_cfg else None
        extra_headers = (provider_cfg.extra_headers or None) if provider_cfg else None

        # 未配置 api_base 时使用内置默认值
        if not api_base and provider in _PROVIDER_BASE_URLS:
            api_base = _PROVIDER_BASE_URLS[provider]

        kwargs = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["base_url"] = api_base
        if extra_headers:
            kwargs["default_headers"] = extra_headers

        return ChatOpenAI(**kwargs)


class AgentsModelFactory:
    """Build OpenAI Agents SDK models backed by LiteLLM."""

    def __init__(self, providers_config: "ProvidersConfig") -> None:
        self.providers = providers_config

    def build(self, mode: "ModelModeConfig") -> AgentModelBundle:
        provider = resolve_provider_name(mode)
        provider_config = getattr(self.providers, provider, None)
        api_key = (provider_config.api_key or None) if provider_config else None
        api_base = (provider_config.api_base or None) if provider_config else None
        extra_headers = (provider_config.extra_headers or None) if provider_config else None
        if not api_base and provider in _PROVIDER_BASE_URLS:
            api_base = _PROVIDER_BASE_URLS[provider]

        model_name = self.normalize_agent_model_name(provider, mode.model)
        return AgentModelBundle(
            model=LitellmModel(model=model_name, base_url=api_base, api_key=api_key),
            model_settings=ModelSettings(
                temperature=mode.temperature,
                max_tokens=mode.max_tokens,
                parallel_tool_calls=True,
                extra_headers=extra_headers,
            ),
        )

    def normalize_agent_model_name(self, provider: str, model: str) -> str:
        text = str(model or "").strip()
        if not text:
            return "gpt-4.1-mini"
        if "/" in text:
            return text
        if provider in _PREFIX_PROVIDERS:
            return f"{provider}/{text}"
        return text


def resolve_provider_name(mode: "ModelModeConfig") -> str:
    provider = str(mode.provider or "auto").strip().lower() or "auto"
    if provider != "auto":
        return provider
    model = str(mode.model or "").strip().lower()
    if "/" in model:
        return "openrouter"
    if model.startswith(("claude-", "claude.")):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith(("llama", "mistral", "mixtral", "gemma", "qwen-", "qwen2", "qwen3", "deepseek-")):
        return "openai"
    return "openai"
