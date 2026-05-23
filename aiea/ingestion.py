"""AIEA - 数据接入层

扫描 ~/.claude/telemetry/ 下的 JSONL 文件，
解析事件，解码 base64 元数据，构建时间线。
"""

import base64
import json
import os
from pathlib import Path
from collections import defaultdict

from .models import (
    ApiSuccessEvent,
    ToolUseSuccessEvent,
    ContextSizeEvent,
    FileReadRereadEvent,
    SessionTimeline,
)

# 我们关心的有用事件
RELEVANT_EVENTS = {
    "tengu_api_success",
    "tengu_tool_use_success",
    "tengu_context_size",
    "tengu_file_read_reread",
    "tengu_bash_command_timeout_backgrounded",
    "tengu_ant_overly_broad_bash_detected",
}

TELEMETRY_DIR = Path.home() / ".claude" / "telemetry"


def decode_metadata(meta_value: str) -> dict:
    """解码 base64 编码的 additional_metadata"""
    if not meta_value:
        return {}
    try:
        decoded = base64.b64decode(meta_value).decode("utf-8")
        return json.loads(decoded)
    except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    except Exception:
        return {}


def scan_telemetry_files(telemetry_dir: str | Path | None = None) -> list[Path]:
    """扫描 telemetry 目录下所有 1p_failed_events.*.json 文件"""
    scan_dir = Path(telemetry_dir) if telemetry_dir else TELEMETRY_DIR
    if not scan_dir.exists():
        print(f"[WARN] Telemetry 目录不存在: {scan_dir}")
        return []

    files = sorted(scan_dir.glob("1p_failed_events.*.json"))
    if not files:
        print(f"[WARN] Telemetry 目录下没有 JSONL 文件: {scan_dir}")
    return files


def parse_jsonl(file_path: Path) -> list[dict]:
    """读取 JSONL 文件，返回事件列表"""
    events = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError) as e:
        print(f"[WARN] 无法读取 {file_path}: {e}")
    return events


def parse_event(raw: dict) -> dict | None:
    """解析单条事件，提取有效字段"""
    event_data = raw.get("event_data", {})
    event_name = event_data.get("event_name", "")
    if event_name not in RELEVANT_EVENTS:
        return None

    meta = decode_metadata(event_data.get("additional_metadata", ""))

    result = {
        "event_name": event_name,
        "session_id": event_data.get("session_id", ""),
        "timestamp": event_data.get("client_timestamp", ""),
        "model": event_data.get("model", ""),
        "metadata": meta,
    }
    return result


def parse_api_success(parsed: dict) -> ApiSuccessEvent | None:
    """从解析后的事件构建 ApiSuccessEvent"""
    meta = parsed["metadata"]
    if not meta:
        return None
    return ApiSuccessEvent(
        model=parsed["model"] or meta.get("model", ""),
        message_count=meta.get("messageCount", 0),
        message_tokens=meta.get("messageTokens", 0),
        input_tokens=meta.get("inputTokens", 0),
        output_tokens=meta.get("outputTokens", 0),
        cached_input_tokens=meta.get("cachedInputTokens", 0),
        uncached_input_tokens=meta.get("uncachedInputTokens", 0),
        duration_ms=meta.get("durationMs", 0),
        cost_usd=meta.get("costUSD", 0.0),
        stop_reason=meta.get("stop_reason", ""),
        query_chain_id=meta.get("queryChainId"),
        query_depth=meta.get("queryDepth", 0),
        session_id=parsed["session_id"],
        timestamp=parsed["timestamp"],
    )


def parse_tool_use_success(parsed: dict) -> ToolUseSuccessEvent | None:
    """从解析后的事件构建 ToolUseSuccessEvent"""
    meta = parsed["metadata"]
    if not meta:
        return None
    return ToolUseSuccessEvent(
        tool_name=meta.get("toolName", ""),
        duration_ms=meta.get("durationMs", 0),
        tool_result_size_bytes=meta.get("toolResultSizeBytes", 0),
        tool_input_size_bytes=meta.get("toolInputSizeBytes", 0),
        bash_command_len=meta.get("bashCommandLen"),
        file_extension=meta.get("fileExtension"),
        file_path_len=meta.get("filePathLen"),
        heap_used_delta_bytes=meta.get("heapUsedDeltaBytes"),
        external_delta_bytes=meta.get("externalDeltaBytes"),
        query_chain_id=meta.get("queryChainId"),
        query_depth=meta.get("queryDepth", 0),
        session_id=parsed["session_id"],
        timestamp=parsed["timestamp"],
    )


def parse_context_size(parsed: dict) -> ContextSizeEvent | None:
    """从解析后的事件构建 ContextSizeEvent"""
    meta = parsed["metadata"]
    if not meta:
        return None
    return ContextSizeEvent(
        total_context_size=meta.get("total_context_size", 0),
        git_status_size=meta.get("git_status_size", 0),
        claude_md_size=meta.get("claude_md_size", 0),
        non_mcp_tools_tokens=meta.get("non_mcp_tools_tokens", 0),
        mcp_tools_tokens=meta.get("mcp_tools_tokens", 0),
        project_file_count_rounded=meta.get("project_file_count_rounded", 0),
        session_id=parsed["session_id"],
        timestamp=parsed["timestamp"],
    )


def parse_file_read_reread(parsed: dict) -> FileReadRereadEvent | None:
    """从解析后的事件构建 FileReadRereadEvent"""
    meta = parsed["metadata"]
    return FileReadRereadEvent(
        prior_op=meta.get("priorOp", ""),
        session_id=parsed["session_id"],
        timestamp=parsed["timestamp"],
    )


PARSER_MAP = {
    "tengu_api_success": parse_api_success,
    "tengu_tool_use_success": parse_tool_use_success,
    "tengu_context_size": parse_context_size,
    "tengu_file_read_reread": parse_file_read_reread,
}


def build_timelines(
    telemetry_dir: str | Path | None = None,
) -> list[SessionTimeline]:
    """主流程：扫描 → 解析 → 构建 SessionTimeline 列表"""
    files = scan_telemetry_files(telemetry_dir)
    if not files:
        return []

    # session_id -> timeline builder
    timelines: dict[str, SessionTimeline] = {}

    all_parsed_events: list[dict] = []

    # 第一遍：解所有文件，过滤出有用事件
    for fpath in files:
        raw_events = parse_jsonl(fpath)
        for raw in raw_events:
            parsed = parse_event(raw)
            if parsed:
                all_parsed_events.append(parsed)

    # 第二遍：按事件类型分类解析，填入对应 timeline
    for parsed in all_parsed_events:
        sid = parsed["session_id"]
        if sid not in timelines:
            timelines[sid] = SessionTimeline(session_id=sid, model=parsed["model"])

        tl = timelines[sid]
        event_name = parsed["event_name"]
        parser = PARSER_MAP.get(event_name)

        if parser is None:
            continue

        obj = parser(parsed)
        if obj is None:
            continue

        if isinstance(obj, ApiSuccessEvent):
            tl.api_calls.append(obj)
            if obj.model and not tl.model:
                tl.model = obj.model
        elif isinstance(obj, ToolUseSuccessEvent):
            tl.tool_calls.append(obj)
        elif isinstance(obj, ContextSizeEvent):
            tl.context_sizes.append(obj)
        elif isinstance(obj, FileReadRereadEvent):
            tl.rereads.append(obj)

    return list(timelines.values())


def print_summary(timelines: list[SessionTimeline]) -> None:
    """打印数据接入摘要"""
    if not timelines:
        print("\n  ❌ 没有找到任何可解析的 telemetry 数据。")
        print("  预期路径: ~/.claude/telemetry/1p_failed_events.*.json")
        return

    total_cost = sum(tl.total_cost_usd for tl in timelines)
    total_calls = sum(tl.total_api_calls for tl in timelines)
    total_tool_calls = sum(len(tl.tool_calls) for tl in timelines)

    print(f"\n  📊 数据接入摘要")
    print(f"  {'─' * 40}")
    print(f"  Session 数:      {len(timelines)}")
    print(f"  API 调用总数:    {total_calls}")
    print(f"  Tool 调用总数:   {total_tool_calls}")
    print(f"  累计成本 (USD):  ${total_cost:.4f}")

    for tl in timelines:
        print(f"\n  📝 Session: {tl.session_id[:12]}...")
        print(f"     模型:      {tl.model or '未知'}")
        print(f"     API 调用:  {tl.total_api_calls} 次")
        print(f"     Tool 调用: {len(tl.tool_calls)} 次")
        print(f"     成本:      ${tl.total_cost_usd:.4f}")
        print(f"     总 input:  {tl.total_input_tokens:,} tokens")
        print(f"     总 output: {tl.total_output_tokens:,} tokens")
        print(f"     Cache 率:  {tl.cache_hit_rate:.1%}")
        if tl.rereads:
            print(f"     重复读取:   {len(tl.rereads)} 次")
