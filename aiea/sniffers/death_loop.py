"""AIEA - DeathLoopSniffer: 检测命令失败重试死循环"""

from ..models import SessionTimeline, WasteFinding, ToolUseSuccessEvent, ApiSuccessEvent
from . import register, Sniffer


def _levenshtein_ratio(a: str, b: str) -> float:
    """计算两个字符串的编辑距离相似度 (0~1)"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # 简化版：只比较前 50 字符的编辑距离
    a_short = a[:50]
    b_short = b[:50]
    m, n = len(a_short), len(b_short)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a_short[i - 1] == b_short[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    distance = dp[n]
    max_len = max(m, n)
    if max_len == 0:
        return 1.0
    return 1.0 - (distance / max_len)


# 判断是否"相近"的阈值
SIMILARITY_THRESHOLD = 0.8
# 死循环判定：至少连续几次
DEATH_LOOP_MIN_COUNT = 3


@register("death_loop")
class DeathLoopSniffer(Sniffer):
    """检测连续失败的相似 Bash 命令（死循环重试）"""

    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        findings: list[WasteFinding] = []

        # 收集所有 Bash 调用，保留顺序
        bash_calls = [
            tc for tc in timeline.tool_calls
            if tc.tool_name == "Bash"
        ]

        if len(bash_calls) < DEATH_LOOP_MIN_COUNT:
            return findings

        # 滑动窗口检测连续相似命令
        groups: list[list[ToolUseSuccessEvent]] = []
        current_group: list[ToolUseSuccessEvent] = [bash_calls[0]]

        for i in range(1, len(bash_calls)):
            prev = bash_calls[i - 1]
            curr = bash_calls[i]

            sim = _levenshtein_ratio(
                str(prev.bash_command_len or 0),
                str(curr.bash_command_len or 0),
            )

            # 连续相同长度的命令大概率是同一条命令
            if prev.bash_command_len and curr.bash_command_len:
                cmd_similar = (
                    abs(prev.bash_command_len - curr.bash_command_len)
                    / max(prev.bash_command_len, 1)
                    < 0.3
                )
            else:
                cmd_similar = False

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
            # 估算浪费：假设每次重试消耗一轮 API call
            waste_usd = 0.0
            # 找对应的 API call。在 group 时间范围内
            group_start = group[0].timestamp
            group_end = group[-1].timestamp
            wasted_api_calls = [
                a for a in timeline.api_calls
                if group_start <= a.timestamp <= group_end
            ] if group_start and group_end else timeline.api_calls[:count]

            for a in wasted_api_calls[:count]:
                waste_usd += a.cost_usd * 0.7  # 70% 是浪费的

            findings.append(WasteFinding(
                severity="high" if count >= 5 else "medium",
                category="death_loop",
                message=(
                    f"可能的重试死循环: 连续 {count} 次相似命令 "
                    f"(总输出 {total_result_size:,} bytes)"
                ),
                estimated_waste_usd=round(waste_usd, 4),
                details={
                    "retry_count": count,
                    "total_output_bytes": total_result_size,
                    "wasted_api_calls": len(wasted_api_calls[:count]),
                    "cmd_len_range": (
                        min(tc.bash_command_len or 0 for tc in group),
                        max(tc.bash_command_len or 0 for tc in group),
                    ),
                },
            ))

        return findings
