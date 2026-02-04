"""
LiteLLM 提供商实现 - 支持多种大语言模型接口

核心功能：
- 统一接口调用不同 LLM 服务商（OpenRouter、Anthropic、OpenAI、Gemini 等）
- 自动识别服务商类型并配置相应的 API 密钥
- 处理模型名称前缀转换（如 openai/、gemini/ 等）
"""

import os
from typing import Any

import litellm
from litellm import acompletion

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """
    基于 LiteLLM 的 LLM 提供商，支持多服务商统一调用

    支持的提供商：
    - OpenRouter: 通过 sk-or- 前缀识别
    - Anthropic: Claude 系列模型
    - OpenAI: GPT 系列模型
    - Gemini: Google Gemini 系列模型
    - Groq: 快速推理服务
    - vLLM: 本地 OpenAI 兼容服务器
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5"
    ):
        """
        初始化 LLM 提供商

        Args:
            api_key: API 密钥（根据服务商类型自动路由）
            api_base: API 基础 URL（用于自定义端点如 vLLM）
            default_model: 默认使用的模型名称
        """
        super().__init__(api_key, api_base)
        self.default_model = default_model

        # 检测是否为 OpenRouter（通过密钥前缀或 URL 判断）
        self.is_openrouter = (
            (api_key and api_key.startswith("sk-or-")) or
            (api_base and "openrouter" in api_base)
        )

        # 检测是否为 vLLM/自定义端点（非 OpenRouter 但有 api_base）
        self.is_vllm = bool(api_base) and not self.is_openrouter

        # 根据提供商类型设置环境变量
        if api_key:
            if self.is_openrouter:
                # OpenRouter 模式
                os.environ["OPENROUTER_API_KEY"] = api_key
            elif self.is_vllm:
                # vLLM/自定义端点（使用 OpenAI 兼容格式）
                os.environ["OPENAI_API_KEY"] = api_key
            elif "anthropic" in default_model:
                os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
            elif "openai" in default_model or "gpt" in default_model:
                os.environ.setdefault("OPENAI_API_KEY", api_key)
            elif "gemini" in default_model.lower():
                os.environ.setdefault("GEMINI_API_KEY", api_key)
            elif "zhipu" in default_model or "glm" in default_model or "zai" in default_model:
                os.environ.setdefault("ZHIPUAI_API_KEY", api_key)
            elif "groq" in default_model:
                os.environ.setdefault("GROQ_API_KEY", api_key)

        # 设置自定义 API 基础 URL（用于 vLLM 等）
        if api_base:
            litellm.api_base = api_base

        # 关闭 LiteLLM 的调试日志输出
        litellm.suppress_debug_info = True
    
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        发送聊天补全请求

        Args:
            messages: 消息列表，每个消息包含 'role' 和 'content'
            tools: 可选的函数工具定义（OpenAI 格式）
            model: 模型标识符（如 'anthropic/claude-sonnet-4-5'）
            max_tokens: 最大生成长度
            temperature: 采样温度（0-2，越高越随机）

        Returns:
            LLMResponse: 包含生成的文本内容或工具调用请求
        """
        model = model or self.default_model

        # OpenRouter 需要 openrouter/ 前缀
        if self.is_openrouter and not model.startswith("openrouter/"):
            model = f"openrouter/{model}"

        # 智谱 AI (Zhipu) 需要 zhipu/ 前缀
        if ("glm" in model.lower() or "zhipu" in model.lower()) and not (
            model.startswith("zhipu/") or
            model.startswith("zai/") or
            model.startswith("openrouter/")
        ):
            model = f"zhipu/{model}"

        # vLLM 需要 hosted_vllm/ 前缀
        if self.is_vllm:
            model = f"hosted_vllm/{model}"

        # Gemini 需要 gemini/ 前缀
        if "gemini" in model.lower() and not model.startswith("gemini/"):
            model = f"gemini/{model}"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # 自定义端点直接传递 api_base
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # 如果有工具定义，添加到请求中
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            # 出错时返回错误信息，让上层处理
            return LLMResponse(
                content=f"调用 LLM 失败: {str(e)}",
                finish_reason="error",
            )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """
        解析 LiteLLM 响应为标准格式

        Args:
            response: LiteLLM 返回的原始响应对象

        Returns:
            标准化的 LLMResponse 对象
        """
        choice = response.choices[0]
        message = choice.message

        # 解析工具调用
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # 解析参数（可能是 JSON 字符串）
                args = tc.function.arguments
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        # 解析使用统计
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )

    def get_default_model(self) -> str:
        """获取默认模型名称"""
        return self.default_model
