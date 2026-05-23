"""AIEA - DeathLoopSniffer: 检测命令失败重试死循环"""

from ..models import SessionTimeline, WasteFinding, ToolUseSuccessEvent
from . import register, Sniffer


# 死循环判定：至少连续几次
DEATH_LOOP_MIN_COUNT = 3
# 命令长度相似度阈值（差异 <30% 视为同一条）
CMD_LEN_RATIO = 0.3


@register("death_loop")
class DeathLoopSniffer(Sniffer):
    """检测连续失败的相似 Bash 命令（死循环重试）"""

    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        findings: list[WasteFinding] = []

        bash_calls = [
            tc for tc in timeline.tool_calls
            if tc.tool_name in ("Bash", "PowerShell")
        ]

        if len(bash_calls) < DEATH_LOOP_MIN_COUNT:
            return findings

        # 滑动窗口检测连续相似命令
        groups: list[list[ToolUseSuccessEvent]] = []
        current_group: list[ToolUseSuccessEvent] = [bash_calls[0]]

        for i in range(1, len(bash_calls)):
            prev = bash_calls[i - 1]
            curr = bash_calls[i]

            # 命令长度差异 < 30% 视为相似
            cmd_similar = False
            if prev.bash_command_len and curr.bash_command_len:
                diff_ratio = (
                    abs(prev.bash_command_len - curr.bash_command_len)
                    / max(prev.bash_command_len, 1)
                )
                cmd_similar = diff_ratio < CMD_LEN_RATIO

            if cmd_similar:
                current_group.append(curr)
            else:
                if len(current_group) >= DEATH_LOOP_MIN_COUNT:
                    groups.append(current_group)
                current_group = [curr]

        if len(current_group) >= DEATH_LOOP_MIN_COUNT:
            groups.append(current_group)

        # 分析每组
        for group in groups:
            count = len(group)
            total_result_size = sum(tc.tool_result_size_bytes for tc in group)

            # 有 bash_timeout 事件佐证则严重等级提高
            has_timeout_evidence = bool(timeline.bash_timeouts)
            severity = "high" if (count >= 5 or has_timeout_evidence) else "medium"

            # 估算浪费
            waste_usd = 0.0
            # 取最后 count 次 API call 的 70% 作为浪费
            for a in timeline.api_calls[-count:]:
                waste_usd += a.cost_usd * 0.7

            findings.append(WasteFinding(
                severity=severity,
                category="death_loop",
                message=(
                    f"可能的重试死循环: 连续 {count} 次相似命令 "
                    f"(总输出 {total_result_size:,} bytes)"
                    + (" [有 timeout 事件佐证]" if has_timeout_evidence else "")
                ),
                estimated_waste_usd=round(waste_usd, 4),
                details={
                    "fix_suggestion": "检查命令失败原因再重试；使用 pipe 或 tee 捕获中间结果；避免在循环中反复调用相同 Bash 命令。",
                    "retry_count": count,
                    "total_output_bytes": total_result_size,
                    "wasted_api_calls": min(count, len(timeline.api_calls)),
                    "cmd_len_range": (
                        min(tc.bash_command_len or 0 for tc in group),
                        max(tc.bash_command_len or 0 for tc in group),
                    ),
                    "has_timeout_evidence": has_timeout_evidence,
                },
            ))

        return findings
