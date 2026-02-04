"""
Spawn 工具 - 启动后台子代理

这个工具允许 Agent 调用子代理来执行耗时任务。
子代理在后台独立运行，完成后通过消息总线通知主 Agent。
"""

from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """
    Spawn 工具 - 启动后台子代理

    使用场景：
    - 长时间运行的研究任务
    - 需要并行处理的多个任务
    - 需要执行但不需要实时响应的操作

    子代理特性：
    - 后台运行，不阻塞主对话
    - 有自己的工具集
    - 完成后自动通知主 Agent
    """

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"  # 默认来源通道
        self._origin_chat_id = "direct"  # 默认会话 ID

    def set_context(self, channel: str, chat_id: str) -> None:
        """
        设置子代理结果返回的上下文

        Args:
            channel: 目标通道（如 telegram, whatsapp）
            chat_id: 目标会话 ID
        """
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        """工具名称"""
        return "spawn"

    @property
    def description(self) -> str:
        """工具描述（供 LLM 参考）"""
        return (
            "启动一个子代理在后台执行任务。"
            "适用于复杂或耗时的任务，可以独立运行。"
            "子代理完成后会报告结果。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """
        工具参数定义（JSON Schema 格式）

        Returns:
            参数规范对象
        """
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "子代理要完成的任务描述",
                },
                "label": {
                    "type": "string",
                    "description": "任务的简短标签（用于显示）",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        """
        启动子代理执行任务

        Args:
            task: 任务描述
            label: 可选的任务标签
            **kwargs: 其他参数（忽略）

        Returns:
            状态消息
        """
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
        )
