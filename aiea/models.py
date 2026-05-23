"""AIEA - 数据模型定义"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApiSuccessEvent:
    """tengu_api_success 解码后的数据"""
    model: str
    message_count: int
    message_tokens: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    duration_ms: int
    cost_usd: float
    stop_reason: str
    query_chain_id: Optional[str] = None
    query_depth: int = 0
    session_id: str = ""
    timestamp: str = ""
    token_cost_rate: float = 0.0  # 推算的 token 单价


@dataclass
class ToolUseSuccessEvent:
    """工具调用事件（来自 telemetry 或 session JSONL）"""
    tool_name: str
    duration_ms: int
    tool_result_size_bytes: int
    tool_input_size_bytes: int
    tool_use_id: Optional[str] = None
    bash_command_len: Optional[int] = None
    bash_command_text: Optional[str] = None
    file_extension: Optional[str] = None
    file_path_len: Optional[int] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    result_timestamp: str = ""
    duration_ms_estimated: bool = False
    heap_used_delta_bytes: Optional[int] = None
    external_delta_bytes: Optional[int] = None
    query_chain_id: Optional[str] = None
    query_depth: int = 0
    session_id: str = ""
    timestamp: str = ""
    tool_status: str = "unknown"  # success / failed / timeout / unknown
    exit_code: Optional[int] = None


@dataclass
class ContextSizeEvent:
    """tengu_context_size 解码后的数据"""
    total_context_size: int
    git_status_size: int = 0
    claude_md_size: int = 0
    non_mcp_tools_tokens: int = 0
    mcp_tools_tokens: int = 0
    project_file_count_rounded: int = 0
    session_id: str = ""
    timestamp: str = ""


@dataclass
class FileReadRereadEvent:
    """tengu_file_read_reread 解码后的数据"""
    prior_op: str = ""
    session_id: str = ""
    timestamp: str = ""


@dataclass
class WasteFinding:
    """一条浪费检测结果"""
    severity: str          # critical / high / medium / low
    category: str          # 模式名称
    message: str           # 描述
    estimated_waste_usd: float = 0.0
    details: dict = field(default_factory=dict)


@dataclass
class BashTimeoutEvent:
    """tengu_bash_command_timeout_backgrounded 解码后"""
    session_id: str = ""
    timestamp: str = ""


@dataclass
class BroadBashDetectedEvent:
    """tengu_ant_overly_broad_bash_detected 解码后"""
    session_id: str = ""
    timestamp: str = ""


@dataclass
class SessionTimeline:
    """单个 Session 的完整时间线"""
    session_id: str
    model: str = ""
    api_calls: list[ApiSuccessEvent] = field(default_factory=list)
    tool_calls: list[ToolUseSuccessEvent] = field(default_factory=list)
    context_sizes: list[ContextSizeEvent] = field(default_factory=list)
    rereads: list[FileReadRereadEvent] = field(default_factory=list)
    bash_timeouts: list[BashTimeoutEvent] = field(default_factory=list)
    broad_bash_detections: list[BroadBashDetectedEvent] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.api_calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(a.input_tokens for a in self.api_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(a.output_tokens for a in self.api_calls)

    @property
    def total_cached_tokens(self) -> int:
        return sum(a.cached_input_tokens for a in self.api_calls)

    @property
    def total_uncached_tokens(self) -> int:
        return sum(a.uncached_input_tokens for a in self.api_calls)

    @property
    def total_message_tokens(self) -> int:
        return sum(a.message_tokens for a in self.api_calls)

    @property
    def total_api_calls(self) -> int:
        return len(self.api_calls)

    @property
    def cache_hit_rate(self) -> float:
        total_cached = self.total_cached_tokens
        total_input = total_cached + self.total_uncached_tokens
        if total_input == 0:
            return 0.0
        return total_cached / total_input
