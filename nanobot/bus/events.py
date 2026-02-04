"""
事件类型定义 - 消息总线传输的数据结构

定义了两类核心消息：
- InboundMessage: 从聊天通道收到的用户消息
- OutboundMessage: 发送给聊天通道的响应消息
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """
    入站消息 - 从聊天通道收到的用户消息

    Attributes:
        channel: 来源通道标识（telegram, whatsapp, cli）
        sender_id: 发送者用户 ID
        chat_id: 会话/聊天 ID（群聊中区分不同对话）
        content: 消息文本内容
        timestamp: 消息时间戳
        media: 媒体文件 URL 列表（图片、语音等）
        metadata: 通道特定扩展数据（如 Telegram 的 message_id）
    """

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # 用户标识
    chat_id: str  # 会话标识
    content: str  # 消息内容
    timestamp: datetime = field(default_factory=datetime.now)  # 时间戳
    media: list[str] = field(default_factory=list)  # 媒体列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展数据

    @property
    def session_key(self) -> str:
        """
        生成会话唯一标识符

        Returns:
            格式如 "telegram:123456789" 的会话键
        """
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """
    出站消息 - 发送给聊天通道的响应消息

    Attributes:
        channel: 目标通道标识
        chat_id: 目标会话 ID
        content: 响应文本内容
        reply_to: 可选的回复目标消息 ID
        media: 要发送的媒体文件列表
        metadata: 通道特定扩展数据
    """

    channel: str  # 目标通道
    chat_id: str  # 目标会话
    content: str  # 响应内容
    reply_to: str | None = None  # 回复目标
    media: list[str] = field(default_factory=list)  # 媒体列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展数据


