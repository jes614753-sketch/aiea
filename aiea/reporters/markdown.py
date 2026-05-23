"""AIEA - Markdown 报告输出"""

from datetime import datetime
from ..models import SessionTimeline, WasteFinding

SEVERITY_ICONS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}


def generate_markdown(
    timelines: list[SessionTimeline],
    findings: dict[str, list[WasteFinding]],
) -> str:
    """生成 Markdown 报告内容"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_cost = sum(tl.total_cost_usd for tl in timelines)
    total_findings = sum(len(fs) for fs in findings.values())
    total_waste = sum(
        f.estimated_waste_usd
        for fs in findings.values()
        for f in fs
    )

    lines = [
        f"# AI Efficiency Auditor 报告",
        f"",
        f"**生成时间**: {now}",
        f"**Session 数**: {len(timelines)}",
        f"**总成本**: ${total_cost:.4f} USD",
        f"**检测到浪费**: {total_findings} 条 (估算 ${total_waste:.4f} USD)",
        f"",
        f"---",
        f"",
    ]

    for tl in timelines:
        lines.append(f"## Session: `{tl.session_id[:20]}...`")
        lines.append(f"")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 模型 | {tl.model or '未知'} |")
        lines.append(f"| API 调用 | {tl.total_api_calls} |")
        lines.append(f"| Tool 调用 | {len(tl.tool_calls)} |")
        lines.append(f"| 总成本 | ${tl.total_cost_usd:.4f} |")
        lines.append(f"| Input tokens | {tl.total_input_tokens:,} |")
        lines.append(f"| Output tokens | {tl.total_output_tokens:,} |")
        lines.append(f"| Cache 命中率 | {tl.cache_hit_rate:.1%} |")
        lines.append(f"")

        session_findings = findings.get(tl.session_id, [])
        if session_findings:
            lines.append(f"### ⚠ 检测到的浪费模式")
            lines.append(f"")
            lines.append(f"| 等级 | 类别 | 描述 | 估算浪费 |")
            lines.append(f"|---|---|---|---|")
            for f in session_findings:
                icon = SEVERITY_ICONS.get(f.severity, "⚪")
                waste_str = f"${f.estimated_waste_usd:.4f}" if f.estimated_waste_usd > 0 else "-"
                lines.append(f"| {icon} {f.severity.upper()} | {f.category} | {f.message} | {waste_str} |")
            lines.append(f"")
        else:
            lines.append(f"✅ 未检测到明显浪费模式")
            lines.append(f"")

    # 附录: 原始数据参考
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## 附录")
    lines.append(f"")
    lines.append(f"### 数据源")
    lines.append(f"")
    lines.append(f"```")
    lines.append(f"~/.claude/telemetry/1p_failed_events.*.json")
    lines.append(f"```")
    lines.append(f"")
    lines.append(f"### 嗅探器版本")
    lines.append(f"")
    lines.append(f"- `toxic_file`: 检测 lockfile、编译产物等毒药文件")
    lines.append(f"- `bloated_context`: 检测 Bash 输出过大")
    lines.append(f"- `death_loop`: 检测命令失败重试死循环")
    lines.append(f"")

    return "\n".join(lines)
