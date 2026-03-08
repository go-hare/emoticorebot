"""LLM factory - 根据 schema 配置构建 LangChain chat model。

支持的 provider：
- openai          : OpenAI 官方 API（GPT-4 系列）
- anthropic       : Anthropic API（langchain-anthropic）
- gemini          : Google Gemini（langchain-google-genai）
- groq            : Groq API（langchain-groq）
- ollama          : 本地 Ollama 服务（langchain-ollama）
- openrouter      : OpenRouter 统一网关（模型名格式 "provider/model"）
- deepseek        : DeepSeek API（OpenAI 兼容）
- zhipu           : 智谱 GLM API（OpenAI 兼容）
- dashscope       : 阿里云通义千问（OpenAI 兼容）
- moonshot        : Moonshot/Kimi API（OpenAI 兼容）
- minimax         : MiniMax API（OpenAI 兼容）
- siliconflow     : 硅基流动 API（OpenAI 兼容）
- volcengine      : 火山引擎 API（OpenAI 兼容）
- aihubmix        : AiHubMix 网关（OpenAI 兼容，支持 extra_headers）
- vllm            : 本地 vLLM 服务（OpenAI 兼容）
- custom          : 任意 OpenAI 兼容自定义端点
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


class LLMFactory:
    """根据 IQ/EQ 模式配置和 provider 凭证构建 LangChain chat model。

    Usage::

        factory = LLMFactory(
            providers_config=config.providers,
            iq_mode=config.agents.defaults.iq_mode,
            eq_mode=config.agents.defaults.eq_mode,
        )
        iq_llm = factory.get_iq()
        eq_llm = factory.get_eq()
    """

    def __init__(
        self,
        providers_config: "ProvidersConfig",
        iq_mode: "ModelModeConfig",
        eq_mode: "ModelModeConfig",
    ) -> None:
        self._providers = providers_config
        self._iq = iq_mode
        self._eq = eq_mode

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_iq(self) -> Any:
        """构建 IQ（智能推理）主力大模型实例。"""
        return self._build(self._iq)

    def get_eq(self) -> Any:
        """构建 EQ（情感智能）模型实例。"""
        return self._build(self._eq)

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
