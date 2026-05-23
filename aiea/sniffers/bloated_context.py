"""AIEA - BloatedContextSniffer: 检测 Bash 输出过大导致上下文膨胀"""

from ..models import SessionTimeline, WasteFinding, ToolUseSuccessEvent
from . import register, Sniffer


# 工具结果大小阈值 (bytes)
THRESHOLDS = {
    "warn": 5_000,      # 5KB — 提示注意
    "high": 20_000,     # 20KB — 明显浪费
    "critical": 100_000, # 100KB+ — 严重
}


@register("bloated_context")
class BloatedContextSniffer(Sniffer):
    """检测 Bash 命令输出过大，导致大量 token 被浪费在冗余输出上"""

    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        findings: list[WasteFinding] = []

        # 找出所有结果过大的 Tool 调用
        oversized: list[ToolUseSuccessEvent] = []

        for tc in timeline.tool_calls:
            if tc.tool_name == "Bash" and tc.tool_result_size_bytes > THRESHOLDS["warn"]:
                oversized.append(tc)

        if not oversized:
            return findings

        # 进一步归类
        high_count = sum(1 for tc in oversized if tc.tool_result_size_bytes > THRESHOLDS["high"])
        critical_count = sum(1 for tc in oversized if tc.tool_result_size_bytes > THRESHOLDS["critical"])
        max_size = max(tc.tool_result_size_bytes for tc in oversized)
        total_waste_bytes = sum(tc.tool_result_size_bytes for tc in oversized)

        # 估算浪费成本 (假设 1 token ≈ 4 bytes, 每 token $0.000003)
        waste_tokens = total_waste_bytes // 4
        waste_usd = round(waste_tokens * 0.000003, 4)

        # 顶级严重等级
        if critical_count > 0:
            severity = "critical"
        elif high_count > 0:
            severity = "high"
        else:
            severity = "medium"

        # 输出
        if oversized:
            findings.append(WasteFinding(
                severity=severity,
                category="bloated_bash_output",
                message=(
                    f"Bash 输出过大: {len(oversized)} 次调用中 "
                    f"{high_count} 次 > 20KB, {critical_count} 次 > 100KB, "
                    f"最大 {max_size:,} bytes"
                ),
                estimated_waste_usd=waste_usd,
                details={
                    "total_oversized_calls": len(oversized),
                    "high_count": high_count,
                    "critical_count": critical_count,
                    "max_output_bytes": max_size,
                    "total_waste_bytes": total_waste_bytes,
                    "estimated_waste_tokens": waste_tokens,
                    "top_offenders": [
                        {
                            "size_bytes": tc.tool_result_size_bytes,
                            "cmd_len": tc.bash_command_len,
                        }
                        for tc in sorted(oversized, key=lambda x: -x.tool_result_size_bytes)[:5]
                    ],
                },
            ))

        return findings
