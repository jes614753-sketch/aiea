"""AIEA - ToxicFileSniffer: 检测毒药文件进入上下文"""

import re

from ..models import SessionTimeline, WasteFinding, ToolUseSuccessEvent
from . import register, Sniffer


# 毒药文件模式： (模式, 匹配目标, 严重等级, 匹配方式)
# 匹配方式: "ext"=仅扩展名, "file"=匹配完整文件名, "path"=匹配路径片段
TOXIC_PATTERNS = [
    # lockfile (扩展名匹配)
    (r"^(lock|lockb)$", "lockfile", "high", "ext"),
    (r"^package-lock\.json$", "lockfile", "high", "file"),
    (r"^yarn\.lock$", "lockfile", "high", "file"),
    (r"^pnpm-lock\.yaml$", "lockfile", "high", "file"),
    (r"^composer\.lock$", "lockfile", "high", "file"),
    (r"^Cargo\.lock$", "lockfile", "high", "file"),
    (r"^Gemfile\.lock$", "lockfile", "high", "file"),
    # 编译产物
    (r"^min\.(js|css)$", "编译产物", "high", "file"),
    (r"^(bundle|chunk)\..*\.(js|css)$", "编译产物", "high", "file"),
    (r"(^|/)dist/", "编译产物", "medium", "path"),
    (r"(^|/)build/", "编译产物", "medium", "path"),
    (r"(^|/)\.next/", "编译产物", "medium", "path"),
    # 二进制/媒体
    (r"^(png|jpg|jpeg|gif|ico|svg|webp)$", "媒体文件", "medium", "ext"),
    (r"^(woff2?|eot|ttf|otf)$", "字体文件", "medium", "ext"),
    (r"^(pdf|zip|tar|gz|7z|rar)$", "二进制归档", "medium", "ext"),
    # 生成文件
    (r"^\.min\.", "minified", "high", "file"),
    (r"\.generated\.", "生成文件", "medium", "path"),
    (r"^terraform\.tfstate", "生成文件", "medium", "file"),
    (r"__pycache__", "缓存目录", "medium", "path"),
    (r"^pyc$", "缓存文件", "low", "ext"),
    (r"^class$", "编译产物", "medium", "ext"),
]


def _calc_waste_estimate(tl: SessionTimeline, count: int, severity: str) -> float:
    """估算浪费成本"""
    if count == 0:
        return 0.0
    avg_cost_per_read = 0.003 if severity == "high" else 0.001
    return round(count * avg_cost_per_read, 4)


def _normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").lower()


def _match_toxic(
    file_ext: str | None,
    file_name_hint: str | None = None,
    file_path: str | None = None,
) -> tuple[str, str] | None:
    """匹配毒药模式。优先用完整路径，再 fallback 到文件名和扩展名。"""
    candidates = []

    if file_path:
        candidates.append(("path", _normalize_path(file_path)))
    if file_name_hint:
        candidates.append(("file", _normalize_path(file_name_hint)))
    if file_ext:
        ext_lower = file_ext.strip().lower()
        candidates.append(("ext", ext_lower))

    for match_type, value in candidates:
        for pattern, category, severity, match_style in TOXIC_PATTERNS:
            if match_style == match_type:
                if re.search(pattern, value):
                    return category, severity
    return None


@register("toxic_file")
class ToxicFileSniffer(Sniffer):
    """检测 lockfile、编译产物等毒药文件进入上下文"""

    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        findings: list[WasteFinding] = []
        ext_counts: dict[str, list[ToolUseSuccessEvent]] = {}
        ext_first_match: dict[str, tuple[str, str]] = {}

        for tc in timeline.tool_calls:
            if tc.tool_name not in ("Read", "Edit"):
                continue
            match = _match_toxic(tc.file_extension, tc.file_name, tc.file_path)
            if match:
                category, severity = match
                key = tc.file_path or tc.file_name or tc.file_extension or ""
                if key not in ext_counts:
                    ext_counts[key] = []
                    ext_first_match[key] = (category, severity)
                ext_counts[key].append(tc)

        for target, events in ext_counts.items():
            category, severity = ext_first_match[target]
            count = len(events)
            waste = _calc_waste_estimate(timeline, count, severity)
            findings.append(WasteFinding(
                severity=severity,
                category=f"toxic_file_{category}",
                message=f"毒药文件 {target} 被读入上下文 {count} 次 ({category})",
                estimated_waste_usd=waste,
                details={
                    "file_extension": events[0].file_extension,
                    "file_path": target,
                    "category": category,
                    "read_count": count,
                    "fix_suggestion": f"在 CLAUDE.md 中提示不要读取 {target}，或在 .contextignore 中排除对应目录/文件。",
                    "tool_calls": [
                        {
                            "tool": e.tool_name,
                            "size": e.tool_result_size_bytes,
                            "path": e.file_path,
                        }
                        for e in events[:5]
                    ],
                },
            ))

        return findings
