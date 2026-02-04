"""
Agent 循环 - 核心处理引擎

负责：
1. 从消息总线接收用户消息
2. 构建对话上下文（历史、记忆、技能）
3. 调用 LLM 生成响应
4. 执行工具调用
5. 返回响应到消息总线
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    Agent 循环 - 核心处理引擎

    处理流程：
    ┌─────────────┐
    │ 接收消息     │
    └──────┬──────┘
           ↓
    ┌─────────────┐
    │ 构建上下文   │ ← 历史记录、记忆、workspace 文件
    └──────┬──────┘
           ↓
    ┌─────────────┐
    │ 调用 LLM    │ ── 有工具调用？ ──Yes──→ 执行工具
    └──────┬──────┘                           │
           │No                               ↓
           ↓                          ┌─────────────┐
    ┌─────────────┐                  │ 工具结果     │
    │ 返回响应    │ ←──────────────── │ 加入上下文   │
    └─────────────┘                  └─────────────┘
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None
    ):
        """
        初始化 Agent 循环

        Args:
            bus: 消息总线连接
            provider: LLM 提供商
            workspace: 工作目录路径
            model: 使用的模型名称
            max_iterations: 最大工具调用迭代次数
            brave_api_key: Brave Search API 密钥
        """
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key

        # 上下文构建器 - 管理提示词和历史
        self.context = ContextBuilder(workspace)
        # 会话管理器 - 管理对话历史持久化
        self.sessions = SessionManager(workspace)
        # 工具注册表 - 管理所有可用工具
        self.tools = ToolRegistry()
        # 子代理管理器 - 管理后台任务
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
        )

        self._running = False
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认工具集"""
        # 文件操作工具
        self.tools.register(ReadFileTool())
        self.tools.register(WriteFileTool())
        self.tools.register(EditFileTool())
        self.tools.register(ListDirTool())

        # Shell 命令执行工具
        self.tools.register(ExecTool(working_dir=str(self.workspace)))

        # Web 搜索工具
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())

        # 消息发送工具
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)

        # 子代理启动工具
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
    
    async def run(self) -> None:
        """
        启动 Agent 循环主任务

        持续从消息总线获取消息并处理，直到被停止
        """
        self._running = True
        logger.info("Agent 循环已启动")

        while self._running:
            try:
                # 等待下一条消息（超时 1 秒以支持优雅退出）
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )

                # 处理消息
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")
                    # 发送错误响应
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"抱歉，发生了错误: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """停止 Agent 循环"""
        self._running = False
        logger.info("Agent 循环正在停止")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        处理单条入站消息的核心逻辑

        处理步骤：
        1. 获取或创建会话
        2. 更新工具上下文
        3. 构建 LLM 消息
        4. 循环调用 LLM 并执行工具直到无工具调用
        5. 保存会话历史
        6. 返回响应消息

        Args:
            msg: 入站消息对象

        Returns:
            出站消息对象，无响应时返回 None
        """
        # 处理系统消息（如子代理公告）
        # chat_id 包含原始 "channel:chat_id" 用于路由回复
        if msg.channel == "system":
            return await self._process_system_message(msg)

        logger.info(f"处理来自 {msg.channel}:{msg.sender_id} 的消息")

        # 获取或创建会话
        session = self.sessions.get_or_create(msg.session_key)

        # 更新消息工具的上下文（用于发送消息到正确的通道）
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        # 更新子代理工具的上下文（用于后台任务通知）
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)

        # 构建初始消息（包含历史和当前消息）
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
        )

        # Agent 循环：LLM 调用 + 工具执行
        iteration = 0
        final_content = None

        while iteration < self.max_iterations:
            iteration += 1

            # 调用 LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )

            # 处理工具调用
            if response.has_tool_calls:
                # 将助手消息（包含工具调用）添加到上下文
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # 必须为 JSON 字符串
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                # 执行每个工具调用
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"执行工具: {tool_call.name}, 参数: {args_str}")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    # 将工具结果添加到上下文
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # 无工具调用，完成
                final_content = response.content
                break

        if final_content is None:
            final_content = "处理完成，但没有生成响应。"

        # 保存会话历史
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        # 返回响应消息
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        处理系统消息（如子代理完成通知）

        子代理完成任务后，通过 system 通道发送公告。
        chat_id 字段包含 "original_channel:original_chat_id" 用于路由回复。

        Args:
            msg: 系统消息对象

        Returns:
            出站消息对象
        """
        logger.info(f"处理来自 {msg.sender_id} 的系统消息")

        # 从 chat_id 解析原始通道（格式: "channel:chat_id"）
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # 回退到 CLI 模式
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # 使用原始会话键保持上下文连续性
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        # 更新工具上下文
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)

        # 构建消息（包含公告内容作为当前消息）
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content
        )

        # Agent 循环（限制迭代次数）
        iteration = 0
        final_content = None

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"执行工具: {tool_call.name}, 参数: {args_str}")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "后台任务已完成。"

        # 保存会话（标记为系统消息）
        session.add_message("user", f"[系统: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        # 返回到原始通道
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )

    async def process_direct(self, content: str, session_key: str = "cli:direct") -> str:
        """
        直接处理消息（用于 CLI 交互模式）

        与 _process_message 不同的是，这个方法直接返回字符串响应，
        不通过消息总线，适合单次对话场景。

        Args:
            content: 用户输入的消息内容
            session_key: 会话标识符

        Returns:
            Agent 生成的文本响应
        """
        msg = InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content=content
        )

        response = await self._process_message(msg)
        return response.content if response else ""
