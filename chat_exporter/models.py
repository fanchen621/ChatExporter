from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessagePartType(Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FILE = "file"
    IMAGE = "image"
    CODE = "code"


@dataclass
class MessagePart:
    type: MessagePartType
    content: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None
    tool_output: Optional[str] = None
    file_name: Optional[str] = None
    language: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: Role
    content: str = ""
    timestamp: Optional[datetime] = None
    message_id: Optional[str] = None
    parent_id: Optional[str] = None
    parts: List[MessagePart] = field(default_factory=list)
    model: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    id: str
    title: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    messages: List[Message] = field(default_factory=list)
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_app: str = ""


@dataclass
class AppInfo:
    name: str
    display_name: str
    icon: Optional[str] = None
    is_available: bool = False
    data_path: Optional[str] = None
    conversation_count: int = 0
