"""AIEA - ToxicFileSniffer: 检测毒药文件进入上下文"""

import re

from ..models import SessionTimeline, WasteFinding, ToolUseSuccessEvent
from . import register, Sniffer


# 毒药文件模式： (扩展名模式, 匹配函数, 描述)
TOXIC_PATTERNS = [
    # lockfile
    (r"^(lock|lockb)$", "lockfile", "high"),
    (r"^package-lock\.json$", "lockfile", "high"),
    (r"^yarn\.lock$", "lockfile", "high"),
    (r"^pnpm-lock\.yaml$", "lockfile", "high"),
    (r"^composer\.lock$", "lockfile", "high"),
    (r"^Cargo\.lock$", "lockfile", "high"),
    (r"^Gemfile\.lock$", "lockfile", "high"),
    # 编译产物
    (r"^min\.(js|css)$", "编译产物", "high"),
    (r"^(bundle|chunk)\..*\.(js|css)$", "编译产物", "high"),
    (r"^\.next", "编译产物", "medium"),
    (r"^dist/", "编译产物", "medium"),
    (r"^build/", "编译产物", "medium"),
    # 二进制/媒体
    (r"^(png|jpg|jpeg|gif|ico|svg|webp)$", "媒体文件", "medium"),
    (r"^(woff2?|eot|ttf|otf)$", "字体文件", "medium"),
    (r"^(pdf|zip|tar|gz|7z|rar)$", "二进制归档", "medium"),
    # 生成文件
    (r"^\.min\.", "minified", "high"),
    (r"\.generated\.", "生成文件", "medium"),
    (r"^terraform\.tfstate", "生成文件", "medium"),
    (r"__pycache__", "缓存文件", "medium"),
    (r"\.pyc$", "缓存文件", "low"),
    (r"\.class$", "编译产物", "medium"),
]


def _calc_waste_estimate(tl: SessionTimeline, count: int, severity: str) -> float:
    """估算浪费成本"""
    if count == 0:
        return 0.0
    # 假设每次读文件平均消耗 ~5000 cached tokens 的上下文
    avg_cost_per_read = 0.001  # ~$0.001 per read for context
    if severity == "high":
        avg_cost_per_read = 0.003
    return round(count * avg_cost_per_read, 4)


def _match_toxic(file_ext: str | None) -> tuple[str, str] | None:
    """匹配毒药模式，返回 (category, severity) 或 None"""
    if not file_ext:
        return None
    ext_lower = file_ext.strip().lower()
    for pattern, category, severity in TOXIC_PATTERNS:
        if re.match(pattern, ext_lower):
            return category, severity
    return None


@register("toxic_file")
class ToxicFileSniffer(Sniffer):
    """检测 lockfile、编译产物等毒药文件进入上下文"""

    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        findings: list[WasteFinding] = []
        # 按扩展名分组统计
        ext_counts: dict[str, list[ToolUseSuccessEvent]] = {}
        ext_first_match: dict[str, tuple[str, str]] = {}

        for tc in timeline.tool_calls:
            if tc.tool_name not in ("Read", "Edit"):
                continue
            match = _match_toxic(tc.file_extension)
            if match:
                category, severity = match
                key = tc.file_extension or ""
                if key not in ext_counts:
                    ext_counts[key] = []
                    ext_first_match[key] = (category, severity)
                ext_counts[key].append(tc)

        for ext, events in ext_counts.items():
            category, severity = ext_first_match[ext]
            count = len(events)
            waste = _calc_waste_estimate(timeline, count, severity)
            findings.append(WasteFinding(
                severity=severity,
                category=f"toxic_file_{category}",
                message=f"毒药文件 .{ext} 被读入上下文 {count} 次 ({category})",
                estimated_waste_usd=waste,
                details={
                    "file_extension": ext,
                    "category": category,
                    "read_count": count,
                    "tool_calls": [
                        {"tool": e.tool_name, "size": e.tool_result_size_bytes}
                        for e in events[:5]
                    ],
                },
            ))

        return findings
