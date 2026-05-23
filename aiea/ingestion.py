"""AIEA — 数据接入层

从 ~/.claude/projects/ 下的 Session JSONL 读取 API / Tool 事件，
构建 SessionTimeline。零侵入，不依赖 telemetry 或 proxy。
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .models import (
    ApiSuccessEvent,
    ToolUseSuccessEvent,
    SessionTimeline,
)

# ─── 路径 ───────────────────────────────────────────────

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# ─── 错误计数器 ─────────────────────────────────────────

_parse_errors: dict[str, int] = {"json_decode": 0, "other": 0}


def reset_error_counters():
    _parse_errors["json_decode"] = 0
    _parse_errors["other"] = 0


def get_error_summary() -> str:
    total = sum(_parse_errors.values())
    parts = [f"{k}={v}" for k, v in _parse_errors.items() if v > 0]
    return f"总计 {total} 个解析错误 ({', '.join(parts)})" if total else "无"


# ─── 定价表 (USD / 1M tokens, 2025-05) ──────────────────

_RATES: dict[str, tuple[float, float, float, float]] = {
    # (input, output, cache_read, cache_write)
    "claude-opus-4-20250514": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-20250514": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-3-5-20241022": (0.25, 1.25, 0.025, 0.3),
    # 简短别名
    "claude-opus-4": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-3-5": (0.25, 1.25, 0.025, 0.3),
}


def _estimate_cost(usage: dict, model: str) -> float:
    """从 usage 和 model 推算本次调用的成本"""
    rates = None
    for key, val in _RATES.items():
        if key in model:
            rates = val
            break
    if rates is None:
        return 0.0

    inp_p, out_p, cr_p, cw_p = rates
    cost = (
        usage.get("input_tokens", 0) / 1_000_000 * inp_p
        + usage.get("output_tokens", 0) / 1_000_000 * out_p
        + usage.get("cache_read_input_tokens", 0) / 1_000_000 * cr_p
        + usage.get("cache_creation_input_tokens", 0) / 1_000_000 * cw_p
    )
    return round(cost, 8)


def _infer_bash_status(result_content, tool_block: dict) -> str:
    """推断 bash 命令执行状态（按优先级降序）"""
    # 1. is_error 标记（result_content 是数组时检查）
    if isinstance(result_content, list) and result_content:
        first = result_content[0]
        if isinstance(first, dict) and first.get("is_error"):
            return "failed"

    # 2. text 中的 Exit code（result_content 可以是字符串或数组）
    if isinstance(result_content, str):
        m = re.search(r"Exit code (\d+)", result_content)
        if m:
            return "success" if m.group(1) == "0" else "failed"
        if "timed out" in result_content.lower():
            return "timeout"
    elif isinstance(result_content, list):
        for item in result_content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                m = re.search(r"Exit code (\d+)", text)
                if m:
                    return "success" if m.group(1) == "0" else "failed"
                if "timed out" in text.lower():
                    return "timeout"
                break

    return "success"


def _extract_result_size(content) -> int:
    """计算工具结果的 UTF-8 字节数（跳过 base64 图片）"""
    if not content:
        return 0
    # content 可以是字符串（简单结果）或数组（结构化结果）
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    total = 0
    for item in content:
        if isinstance(item, str):
            total += len(item.encode("utf-8"))
        elif isinstance(item, dict) and item.get("type") == "text":
            total += len(item.get("text", "").encode("utf-8"))
        elif isinstance(item, dict) and item.get("type") == "image":
            src = item.get("source", {})
            if src.get("type") == "base64":
                total += len(src.get("data", ""))
    return total


def _extract_input_size(content: list[dict] | None) -> int:
    """计算工具输入的 UTF-8 字节数"""
    if not content:
        return 0
    total = 0
    for item in content:
        if item.get("type") == "tool_use":
            total += len(json.dumps(item.get("input", {}), ensure_ascii=False).encode("utf-8"))
    return total


def _parse_timestamp(value: str) -> datetime | None:
    """解析 Claude Code JSONL 中的 ISO timestamp。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _estimate_duration_ms(start: str, end: str) -> int:
    """用 tool_use 与 tool_result 的时间戳粗略估算工具耗时。"""
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if not start_dt or not end_dt:
        return 0
    delta_ms = int((end_dt - start_dt).total_seconds() * 1000)
    return max(delta_ms, 0)


def _extract_bash_command(content: list[dict] | None) -> str | None:
    """提取 bash/PowerShell 命令原文。"""
    if not content:
        return None
    for item in content:
        if item.get("type") == "tool_use" and item.get("name") in ("Bash", "PowerShell"):
            cmd = item.get("input", {}).get("command", "")
            return cmd if cmd else None
    return None


def _extract_bash_cmd_len(content: list[dict] | None) -> int | None:
    """提取 bash/PowerShell 命令长度"""
    cmd = _extract_bash_command(content)
    return len(cmd) if cmd else None


def _extract_file_info(content: list[dict] | None) -> tuple[int | None, int | None, str | None, str | None]:
    """提取文件相关工具的 (路径长度, 扩展名, 文件名, 完整路径)"""
    if not content:
        return None, None, None, None
    file_tools = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
    for item in content:
        if item.get("type") == "tool_use" and item.get("name") in file_tools:
            inp = item.get("input", {})
            fp = inp.get("file_path", "")
            if fp:
                ext = Path(fp).suffix.lstrip(".").lower() or None
                name = Path(fp).name
                return len(fp), ext, name, fp
    return None, None, None, None


# ─── Session JSONL 解析 ─────────────────────────────────

def _read_session_jsonl(jsonl_path: Path) -> list[ApiSuccessEvent]:
    """读取单个 session JSONL 文件，提取 ApiSuccessEvent 列表"""
    events: list[ApiSuccessEvent] = []
    session_id = jsonl_path.stem  # 文件名就是 session UUID
    seen_msg_ids: set[str] = set()  # 去重：同一 message.id 可能出现多次

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    _parse_errors["json_decode"] += 1
                    continue

                # 只处理 assistant 消息
                if entry.get("type") != "assistant":
                    continue

                # 跳过子 agent / sidechain
                if entry.get("isSidechain"):
                    continue

                msg = entry.get("message", {})
                model = msg.get("model", "")
                usage = msg.get("usage", {})
                timestamp = entry.get("timestamp", "")
                msg_id = msg.get("id", "")

                # 跳过没有 usage 的消息（不应该出现）
                if not usage:
                    continue

                # 按 message.id 去重（同一 API 响应被拆成多行）
                if msg_id and msg_id in seen_msg_ids:
                    continue
                if msg_id:
                    seen_msg_ids.add(msg_id)

                cost = _estimate_cost(usage, model)

                events.append(ApiSuccessEvent(
                    model=model,
                    message_count=1,
                    message_tokens=usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cached_input_tokens=usage.get("cache_read_input_tokens", 0),
                    uncached_input_tokens=usage.get("input_tokens", 0),
                    duration_ms=0,
                    cost_usd=cost,
                    stop_reason="",
                    query_chain_id=session_id,
                    query_depth=0,
                    session_id=session_id,
                    timestamp=timestamp,
                ))
    except (IOError, OSError) as e:
        print(f"[WARN] 无法读取 {jsonl_path}: {e}")

    return events


def _extract_tool_calls_from_session(jsonl_path: Path) -> list[ToolUseSuccessEvent]:
    """从 session JSONL 提取工具调用事件"""
    tool_calls: list[ToolUseSuccessEvent] = []
    session_id = jsonl_path.stem

    # 第一遍：收集所有 tool_use 的 id → 内容映射
    tool_use_map: dict[str, dict] = {}

    # 第二遍用的数据结构
    tool_results: dict[str, dict] = {}

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 收集 assistant 的 tool_use
                if entry.get("type") == "assistant" and not entry.get("isSidechain"):
                    msg = entry.get("message", {})
                    for content_block in msg.get("content", []):
                        if content_block.get("type") == "tool_use":
                            tool_use_map[content_block["id"]] = {
                                "name": content_block.get("name", ""),
                                "input": content_block.get("input", {}),
                                "content": [content_block],
                                "timestamp": entry.get("timestamp", ""),
                            }

                # 收集 user 的 tool_result
                elif entry.get("type") == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for item in content:
                            if item.get("type") == "tool_result":
                                tool_results[item["tool_use_id"]] = {
                                    "content": item.get("content", []),
                                    "is_error": item.get("is_error", False),
                                    "toolUseResult": item.get("toolUseResult", {}),
                                    "timestamp": entry.get("timestamp", ""),
                                }
    except (IOError, OSError) as e:
        print(f"[WARN] 无法读取 {jsonl_path}: {e}")
        return []

    # 组装 ToolUseSuccessEvent
    for tool_id, use_info in tool_use_map.items():
        result = tool_results.get(tool_id, {})
        result_content = result.get("content")
        tool_name = use_info["name"]

        status = "unknown"
        exit_code = None
        if tool_name in ("Bash", "PowerShell"):
            status = _infer_bash_status(result_content, use_info)

        bash_command = _extract_bash_command(use_info["content"])
        bash_cmd_len = len(bash_command) if bash_command else None
        file_path_len, file_ext, file_name, file_path = _extract_file_info(use_info["content"])
        result_timestamp = result.get("timestamp", "")
        duration_ms = _estimate_duration_ms(use_info.get("timestamp", ""), result_timestamp)

        tool_calls.append(ToolUseSuccessEvent(
            tool_name=tool_name,
            duration_ms=duration_ms,
            tool_result_size_bytes=_extract_result_size(result_content),
            tool_input_size_bytes=_extract_input_size(use_info["content"]),
            tool_use_id=tool_id,
            bash_command_len=bash_cmd_len,
            bash_command_text=bash_command,
            file_extension=file_ext,
            file_path_len=file_path_len,
            file_path=file_path,
            file_name=file_name,
            result_timestamp=result_timestamp,
            duration_ms_estimated=bool(duration_ms),
            session_id=session_id,
            timestamp=use_info.get("timestamp", ""),
            tool_status=status,
            exit_code=exit_code,
        ))

    return tool_calls


def scan_projects_dir(projects_dir: str | Path | None = None) -> list[Path]:
    """扫描 ~/.claude/projects/ 下所有 session JSONL 文件（递归子目录）"""
    scan_dir = Path(projects_dir) if projects_dir else PROJECTS_DIR
    if not scan_dir.exists():
        print(f"[WARN] Projects 目录不存在: {scan_dir}")
        return []

    # 递归扫描，跳过 subagents 子目录
    files = sorted(
        p for p in scan_dir.rglob("*.jsonl")
        if "subagents" not in p.parts
        and "tool-results" not in p.parts
    )
    if not files:
        print(f"[WARN] 没有找到 session JSONL 文件: {scan_dir}")
    return files


def build_timelines(
    projects_dir: str | Path | None = None,
) -> list[SessionTimeline]:
    """主流程：扫描 session JSONL → 构建 SessionTimeline 列表"""
    reset_error_counters()
    timelines: dict[str, SessionTimeline] = {}

    files = scan_projects_dir(projects_dir)
    if not files:
        return []

    for fpath in files:
        sid = fpath.stem

        # API 调用
        api_events = _read_session_jsonl(fpath)

        # 工具调用
        tool_events = _extract_tool_calls_from_session(fpath)

        # 跳过空 session
        if not api_events and not tool_events:
            continue

        # 确定 model
        model = ""
        if api_events:
            model = api_events[0].model

        tl = SessionTimeline(session_id=sid, model=model)
        tl.api_calls = api_events
        tl.tool_calls = tool_events
        timelines[sid] = tl

    return list(timelines.values())


# ─── 工具结果聚合 ────────────────────────────────────────

def aggregate_tool_results(timelines: list[SessionTimeline]) -> dict[str, dict]:
    """聚合所有 session 的工具结果统计"""
    stats: dict[str, dict] = {}
    for tl in timelines:
        for tc in tl.tool_calls:
            name = tc.tool_name
            if name not in stats:
                stats[name] = {
                    "count": 0,
                    "total_input_bytes": 0,
                    "total_result_bytes": 0,
                    "statuses": {},
                }
            s = stats[name]
            s["count"] += 1
            s["total_input_bytes"] += tc.tool_input_size_bytes
            s["total_result_bytes"] += tc.tool_result_size_bytes
            status = tc.tool_status
            s["statuses"][status] = s["statuses"].get(status, 0) + 1
    return stats


# ─── 摘要输出 ────────────────────────────────────────────

def print_summary(timelines: list[SessionTimeline]) -> None:
    """打印数据接入摘要"""
    if not timelines:
        print("\n  ❌ 没有找到任何 session 数据。")
        print("  预期路径: ~/.claude/projects/<encoded-cwd>/*.jsonl")
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

        # bash 工具统计
        bash_calls = [t for t in tl.tool_calls if t.tool_name in ("Bash", "PowerShell")]
        if bash_calls:
            statuses = {}
            for t in bash_calls:
                statuses[t.tool_status] = statuses.get(t.tool_status, 0) + 1
            status_str = ", ".join(f"{k}:{v}" for k, v in statuses.items())
            print(f"     Bash 状态: {status_str}")
