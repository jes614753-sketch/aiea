"""AIEA - 终端报告输出"""

from ..models import SessionTimeline, WasteFinding


# ANSI 颜色代码
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
GREEN = "\033[92m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

SEVERITY_COLORS = {
    "critical": RED,
    "high": RED,
    "medium": YELLOW,
    "low": BLUE,
}

SEVERITY_LABELS = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}


def _color(severity: str, text: str) -> str:
    color = SEVERITY_COLORS.get(severity, RESET)
    return f"{color}{text}{RESET}"


def print_waste_finding(f: WasteFinding, index: int) -> None:
    """打印单条发现"""
    sev_color = SEVERITY_COLORS.get(f.severity, "")
    label = SEVERITY_LABELS.get(f.severity, "?")
    print(
        f"\n  {sev_color}● [{label}]{RESET} "
        f"{BOLD}{f.message}{RESET}"
    )
    if f.estimated_waste_usd > 0:
        print(f"    {DIM}估算浪费: ${f.estimated_waste_usd:.4f}{RESET}")
    # 显示详细统计
    details = f.details
    if details.get("read_count"):
        print(f"    {DIM}读取次数: {details['read_count']}{RESET}")
    if details.get("retry_count"):
        print(f"    {DIM}重试次数: {details['retry_count']}{RESET}")
    if details.get("total_waste_bytes"):
        waste_kb = details["total_waste_bytes"] / 1024
        print(f"    {DIM}总输出: {waste_kb:.1f} KB{RESET}")
    if details.get("fix_suggestion"):
        print(f"    {GREEN}  修复建议: {details['fix_suggestion']}{RESET}")


def print_timeline_summary(tl: SessionTimeline) -> None:
    """打印 Session 摘要"""
    sev_color = GREEN
    print(f"\n  {BOLD}{'=' * 40}{RESET}")
    print(f"  {BOLD}📋 Session: {tl.session_id[:16]}...{RESET}")
    print(f"  {BOLD}{'=' * 40}{RESET}")

    print(f"  {CYAN}模型:{RESET}        {tl.model or '未知'}")
    print(f"  {CYAN}API 调用:{RESET}     {tl.total_api_calls}")
    print(f"  {CYAN}Tool 调用:{RESET}   {len(tl.tool_calls)}")
    print(f"  {CYAN}总成本:{RESET}      ${tl.total_cost_usd:.4f}")

    if tl.total_input_tokens > 0 or tl.total_output_tokens > 0:
        print(f"  {CYAN}Input tokens:{RESET}  {tl.total_input_tokens:,} "
              f"(cached: {tl.total_cached_tokens:,})")
        print(f"  {CYAN}Output tokens:{RESET} {tl.total_output_tokens:,}")
        print(f"  {CYAN}Cache 命中率:{RESET}  {tl.cache_hit_rate:.1%}")


def print_report(
    timelines: list[SessionTimeline],
    findings: dict[str, list[WasteFinding]],
) -> None:
    """打印完整报告"""
    print(f"\n  {BOLD}{'█' * 44}{RESET}")
    print(f"  {BOLD}  AIEA — AI Efficiency Auditor{RESET}")
    print(f"  {BOLD}{'█' * 44}{RESET}")

    total_cost = sum(tl.total_cost_usd for tl in timelines)
    total_findings = sum(len(fs) for fs in findings.values())

    print(f"\n  📊 总览")
    print(f"  {'─' * 30}")
    print(f"  Session 数:      {len(timelines)}")
    print(f"  总成本:          ${total_cost:.4f}")
    print(f"  检测出浪费模式:  {total_findings} 条")

    # Per-tool 归因
    tool_stats: dict[str, dict] = {}
    for tl in timelines:
        for tc in tl.tool_calls:
            name = tc.tool_name
            if name not in tool_stats:
                tool_stats[name] = {"count": 0, "failed": 0, "total_result_bytes": 0}
            tool_stats[name]["count"] += 1
            if tc.tool_status == "failed":
                tool_stats[name]["failed"] += 1
            tool_stats[name]["total_result_bytes"] += tc.tool_result_size_bytes

    if tool_stats:
        print(f"\n  🔧 工具归因 Top 10")
        print(f"  {'─' * 50}")
        print(f"  {'工具':<16} {'调用':>6} {'失败':>6} {'输出总量':>10}")
        for name, stats in sorted(tool_stats.items(), key=lambda x: -x[1]["count"])[:10]:
            kb = stats["total_result_bytes"] / 1024
            failed_str = f" ({stats['failed']} fail)" if stats["failed"] > 0 else ""
            print(f"  {name:<16} {stats['count']:>6} {failed_str:>6} {kb:>8.1f} KB")

    # 按 session 输出
    for tl in timelines:
        print_timeline_summary(tl)
        session_findings = findings.get(tl.session_id, [])
        if session_findings:
            print(f"\n  {YELLOW}⚠ 检测结果:{RESET}")
            for i, f in enumerate(session_findings):
                print_waste_finding(f, i)
        else:
            print(f"\n  {GREEN}✓ 未检测到明显的浪费模式{RESET}")

    # 总浪费估算
    total_waste = sum(
        f.estimated_waste_usd
        for fs in findings.values()
        for f in fs
    )
    if total_waste > 0:
        print(f"\n  {BOLD}{'─' * 30}{RESET}")
        print(f"  {BOLD}💰 总可避免浪费: ${total_waste:.4f} USD{RESET}")
        if total_cost > 0:
            waste_pct = (total_waste / total_cost) * 100
            print(f"  {BOLD}   占比: {waste_pct:.1f}%{RESET}")

    print()
