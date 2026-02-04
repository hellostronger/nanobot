"""
子代理管理器 - 管理后台异步任务执行

子代理（Subagent）是主 Agent 的轻量级副本：
- 独立运行，不阻塞主对话
- 有自己的工具集（无消息发送能力）
- 完成后通过消息总线通知主 Agent
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool


class SubagentManager:
    """
    子代理管理器 - 管理后台异步任务执行

    子代理的特性：
    - 轻量级：共享 LLM 提供商，但有独立的上下文
    - 后台运行：不阻塞主对话
    - 专注：每个子代理有特定的任务描述
    - 隔离：不能发送消息给用户，不能再创建子代理

    使用场景：
    - 长时间运行的研究任务
    - 并行处理多个独立任务
    - 需要调用工具但不需要实时响应的任务
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
    ):
        """
        初始化子代理管理器

        Args:
            provider: LLM 提供商（共享给所有子代理）
            workspace: 工作目录
            bus: 消息总线（用于发送完成通知）
            model: 使用的模型
            brave_api_key: Brave Search API 密钥
        """
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.brave_api_key = brave_api_key
        self._running_tasks: dict[str, asyncio.Task[None]] = {}  # 跟踪运行中的任务

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """
        启动一个子代理执行任务

        Args:
            task: 子代理的任务描述（系统提示词的一部分）
            label: 人类可读的任务标签（用于显示）
            origin_channel: 结果通知的目标通道
            origin_chat_id: 结果通知的目标会话

        Returns:
            状态消息，告知用户任务已开始
        """
        task_id = str(uuid.uuid4())[:8]  # 简短的任务 ID
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        # 记录结果返回的目标位置
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }

        # 创建后台任务
        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task

        # 任务完成后自动清理
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

        logger.info(f"已启动子代理 [{task_id}]: {display_label}")
        return f"子代理 [{display_label}] 已启动 (ID: {task_id})。完成后我会通知你。"
    
    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """
        子代理任务执行主循环

        与主 Agent 类似，但：
        - 无消息工具（不能主动发消息给用户）
        - 无 spawn 工具（不能创建子代理）
        - 有独立的系统提示词
        - 有迭代次数限制（15次）

        Args:
            task_id: 任务 ID（用于日志）
            task: 任务描述
            label: 任务标签
            origin: 原始通道信息（用于返回结果）
        """
        logger.info(f"子代理 [{task_id}] 开始执行任务: {label}")

        try:
            # 构建子代理专用的工具集（移除消息和 spawn 工具）
            tools = ToolRegistry()
            tools.register(ReadFileTool())
            tools.register(WriteFileTool())
            tools.register(ListDirTool())
            tools.register(ExecTool(working_dir=str(self.workspace)))
            tools.register(WebSearchTool(api_key=self.brave_api_key))
            tools.register(WebFetchTool())

            # 构建消息（包含子代理专用的系统提示词）
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # 运行 Agent 循环（限制迭代次数）
            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1

                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                )

                if response.has_tool_calls:
                    # 添加助手消息（包含工具调用）
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })

                    # 执行工具
                    for tool_call in response.tool_calls:
                        logger.debug(f"子代理 [{task_id}] 执行: {tool_call.name}")
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break

            if final_result is None:
                final_result = "任务已完成，但未生成最终响应。"

            logger.info(f"子代理 [{task_id}] 成功完成")
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"错误: {str(e)}"
            logger.error(f"子代理 [{task_id}] 失败: {e}")
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """
        通知主 Agent 任务完成

        通过消息总线发送系统消息，主 Agent 会处理这个消息并生成自然语言摘要。

        Args:
            task_id: 任务 ID
            label: 任务标签
            task: 原始任务描述
            result: 任务执行结果
            origin: 原始通道信息
            status: 状态（"ok" 或 "error"）
        """
        status_text = "成功完成" if status == "ok" else "失败"

        # 构建结果公告（请求主 Agent 生成自然语言摘要）
        announce_content = f"""[子代理 '{label}' {status_text}]

任务: {task}

结果:
{result}

请用自然语言向用户总结这个结果。保持简洁（1-2句话），不要提及"子代理"或任务ID等技术细节。"""

        # 注入为系统消息，触发主 Agent 处理
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        logger.debug(f"子代理 [{task_id}] 已通知 {origin['channel']}:{origin['chat_id']}")

    def _build_subagent_prompt(self, task: str) -> str:
        """
        构建子代理专用的系统提示词

        Args:
            task: 任务描述

        Returns:
            完整的系统提示词
        """
        return f"""# 子代理

你是主 Agent 派生的子代理，负责完成特定任务。

## 你的任务
{task}

## 行为规则
1. 保持专注 - 只完成分配的任务，不做其他事情
2. 你的最终响应会报告给主 Agent
3. 不要主动发起对话或承担额外任务
4. 发现要简洁但信息丰富

## 你可以做的
- 读取和写入工作区文件
- 执行 Shell 命令
- 搜索网页和抓取网页内容
- 彻底完成任务

## 你不能做的
- 直接发送消息给用户（无消息工具）
- 创建其他子代理（无 spawn 工具）
- 访问主 Agent 的对话历史

## 工作区
你的工作区位于: {self.workspace}

完成任务后，清晰总结你的发现或操作。"""

    def get_running_count(self) -> int:
        """获取当前运行的子代理数量"""
        return len(self._running_tasks)
