"""
异步消息队列 - 实现聊天通道与核心Agent的解耦通信

架构说明：
- Inbound Queue（入站队列）：接收来自 Telegram、WhatsApp 等通道的消息
- Outbound Queue（出站队列）：发送 Agent 响应到各通道
- 订阅机制：各通道订阅出站消息，实现点对点路由
"""

import asyncio
from typing import Callable, Awaitable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    异步消息总线 - 连接聊天通道与 Agent 核心

    工作流程：
    1. Telegram/WhatsApp 等通道收到消息 → 放入 Inbound Queue
    2. Agent 从 Inbound Queue 取出消息并处理
    3. Agent 生成响应 → 放入 Outbound Queue
    4. 各通道订阅 Outbound Queue → 接收并发送响应
    """

    def __init__(self):
        # 接收用户消息的队列
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        # 发送响应消息的队列
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        # 出站消息订阅者字典（key: channel名称, value: 回调函数列表）
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False  # 调度器运行状态

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """发布入站消息（从通道到 Agent）"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """消费入站消息（阻塞直到有新消息）"""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """发布出站消息（从 Agent 到通道）"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """消费出站消息（阻塞直到有新消息）"""
        return await self.outbound.get()

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """
        订阅特定通道的出站消息

        Args:
            channel: 通道名称（如 'telegram', 'whatsapp'）
            callback: 消息到达时调用的回调函数
        """
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    async def dispatch_outbound(self) -> None:
        """
        调度出站消息到各订阅通道
        作为后台任务运行，持续检查 Outbound Queue
        """
        self._running = True
        while self._running:
            try:
                # 等待出站消息，超时 1 秒以支持优雅退出
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                # 获取该通道的所有订阅者
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"发送到 {msg.channel} 失败: {e}")
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """停止调度器"""
        self._running = False

    @property
    def inbound_size(self) -> int:
        """获取入站队列中待处理消息数量"""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """获取出站队列中待发送消息数量"""
        return self.outbound.qsize()
