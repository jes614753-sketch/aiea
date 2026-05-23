"""AIEA — 历史学习模块

从所有历史 session 中聚合浪费模式，生成建议草案。
不自动修改配置，只输出待人工确认的候选项。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .ingestion import build_timelines
from .models import SessionTimeline

# ─── 常量 ───────────────────────────────────────────────

LARGE_OUTPUT_THRESHOLD = 20_000  # 20KB
LARGE_READ_THRESHOLD = 100_000   # 100KB 累计
MIN_SESSIONS_DEFAULT = 3

# 毒药文件模式（与 toxic_file.py 一致）
POISON_PATTERNS = [
    (r"^(lock|lockb)$", "lockfile"),
    (r"^yarn\.lock$", "lockfile"),
    (r"^pnpm-lock\.yaml$", "lockfile"),
    (r"^composer\.lock$", "lockfile"),
    (r"^Cargo\.lock$", "lockfile"),
    (r"^Gemfile\.lock$", "lockfile"),
    (r"^package-lock\.json$", "lockfile"),
    (r"^min\.(js|css)$", "compiled"),
    (r"^(bundle|chunk)\..*\.(js|css)$", "compiled"),
    (r"\.min\.", "compiled"),
    (r"\.pyc$", "cache"),
    (r"\.class$", "compiled"),
    (r"__pycache__", "cache"),
    (r"\.next", "build_output"),
    (r"^terraform\.tfstate", "generated"),
    (r"\.generated\.", "generated"),
]

PATH_POISON_PATTERNS = [
    (r"(^|/)dist/", "build_output", "dist/"),
    (r"(^|/)build/", "build_output", "build/"),
    (r"(^|/)node_modules/", "dependency", "node_modules/"),
    (r"(^|/)vendor/", "dependency", "vendor/"),
    (r"(^|/)coverage/", "generated", "coverage/"),
    (r"(^|/)\.git/", "vcs", ".git/"),
]


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def _classify_poison(path_or_name: str) -> str | None:
    """判断文件路径或文件名是否匹配毒药模式"""
    name_lower = _normalize_path(path_or_name)
    for pattern, category in POISON_PATTERNS:
        if re.search(pattern, name_lower):
            return category
    for pattern, category, _suggestion in PATH_POISON_PATTERNS:
        if re.search(pattern, name_lower):
            return category
    return None


def _suggest_ignore_pattern(path_or_name: str, category: str | None) -> str:
    """为 .contextignore 生成尽量不误伤的候选模式。"""
    normalized = _normalize_path(path_or_name)
    for pattern, _category, suggestion in PATH_POISON_PATTERNS:
        if re.search(pattern, normalized):
            return suggestion
    return Path(path_or_name).name if "/" in normalized else path_or_name


def _display_command(command: str | None, fallback: str) -> str:
    if not command:
        return fallback
    compact = " ".join(command.split())
    return compact if len(compact) <= 120 else compact[:117] + "..."


# ─── 聚合分析 ───────────────────────────────────────────

def analyze_file_reads(timelines: list[SessionTimeline], min_sessions: int) -> list[dict]:
    """分析文件读取频率（仅 Read 工具）"""
    stats: dict[str, dict] = {}

    for tl in timelines:
        for tc in tl.tool_calls:
            if tc.tool_name != "Read" or not (tc.file_path or tc.file_name):
                continue
            key = tc.file_path or tc.file_name
            display_name = tc.file_name or key
            category = _classify_poison(key)
            if key not in stats:
                stats[key] = {
                    "file_name": display_name,
                    "file_path": tc.file_path,
                    "read_count": 0,
                    "sessions": set(),
                    "total_bytes": 0,
                    "failed_count": 0,
                    "poison_category": category,
                    "suggested_pattern": _suggest_ignore_pattern(key, category),
                }
            s = stats[key]
            s["read_count"] += 1
            s["sessions"].add(tl.session_id)
            s["total_bytes"] += tc.tool_result_size_bytes
            if tc.tool_status == "failed":
                s["failed_count"] += 1

    # 过滤：跨 session 读取且累计 bytes 超过阈值
    results = []
    for s in stats.values():
        if len(s["sessions"]) < min_sessions:
            continue
        if s["total_bytes"] < LARGE_READ_THRESHOLD:
            continue
        results.append({
            "file_name": s["file_name"],
            "file_path": s.get("file_path"),
            "read_count": s["read_count"],
            "session_count": len(s["sessions"]),
            "total_bytes": s["total_bytes"],
            "failed_count": s["failed_count"],
            "poison_category": s["poison_category"],
            "confidence": "high" if s["poison_category"] else "medium",
            "suggested_pattern": s["suggested_pattern"],
        })

    return sorted(results, key=lambda x: -x["total_bytes"])


def analyze_bloated_commands(timelines: list[SessionTimeline]) -> list[dict]:
    """分析大输出 Bash/PowerShell 命令"""
    stats: dict[str, dict] = {}

    for tl in timelines:
        for tc in tl.tool_calls:
            if tc.tool_name not in ("Bash", "PowerShell"):
                continue
            if tc.tool_result_size_bytes < LARGE_OUTPUT_THRESHOLD:
                continue
            if tc.bash_command_text:
                bucket = _display_command(tc.bash_command_text, "unknown_cmd")
            elif tc.bash_command_len:
                bucket = f"cmd_len_{tc.bash_command_len // 10 * 10}-{tc.bash_command_len // 10 * 10 + 9}"
            else:
                bucket = "unknown_cmd"
            if bucket not in stats:
                stats[bucket] = {
                    "command": bucket,
                    "count": 0,
                    "sessions": set(),
                    "total_bytes": 0,
                    "max_bytes": 0,
                }
            s = stats[bucket]
            s["count"] += 1
            s["sessions"].add(tl.session_id)
            s["total_bytes"] += tc.tool_result_size_bytes
            s["max_bytes"] = max(s["max_bytes"], tc.tool_result_size_bytes)

    results = []
    for s in stats.values():
        if len(s["sessions"]) < 2:
            continue
        results.append({
            "command": s["command"],
            "command_bucket": s["command"],
            "call_count": s["count"],
            "session_count": len(s["sessions"]),
            "total_bytes": s["total_bytes"],
            "max_bytes": s["max_bytes"],
            "confidence": "medium",
        })

    return sorted(results, key=lambda x: -x["total_bytes"])


def analyze_failure_rates(timelines: list[SessionTimeline]) -> list[dict]:
    """分析工具失败率"""
    stats: dict[str, dict] = {}

    for tl in timelines:
        for tc in tl.tool_calls:
            name = tc.tool_name
            if name not in stats:
                stats[name] = {"tool": name, "total": 0, "failed": 0, "timeout": 0}
            stats[name]["total"] += 1
            if tc.tool_status == "failed":
                stats[name]["failed"] += 1
            elif tc.tool_status == "timeout":
                stats[name]["timeout"] += 1

    results = []
    for s in stats.values():
        if s["total"] < 5:
            continue
        rate = (s["failed"] + s["timeout"]) / s["total"]
        if rate < 0.2:
            continue
        results.append({
            "tool_name": s["tool"],
            "total_calls": s["total"],
            "failures": s["failed"],
            "timeouts": s["timeout"],
            "failure_rate": round(rate, 3),
            "confidence": "high" if rate > 0.5 else "medium",
        })

    return sorted(results, key=lambda x: -x["failure_rate"])


def analyze_poison_files(timelines: list[SessionTimeline], min_sessions: int) -> list[dict]:
    """分析毒药文件读入统计"""
    stats: dict[tuple[str, str], dict] = {}

    for tl in timelines:
        for tc in tl.tool_calls:
            if tc.tool_name != "Read" or not (tc.file_path or tc.file_name):
                continue
            key_path = tc.file_path or tc.file_name
            category = _classify_poison(key_path)
            if not category:
                continue
            suggested_pattern = _suggest_ignore_pattern(key_path, category)
            key = (suggested_pattern, category)
            if key not in stats:
                stats[key] = {
                    "file_name": tc.file_name,
                    "file_path": tc.file_path,
                    "category": category,
                    "suggested_pattern": suggested_pattern,
                    "count": 0,
                    "sessions": set(),
                    "total_bytes": 0,
                }
            s = stats[key]
            s["count"] += 1
            s["sessions"].add(tl.session_id)
            s["total_bytes"] += tc.tool_result_size_bytes

    results = []
    for s in stats.values():
        if len(s["sessions"]) < min_sessions:
            continue
        results.append({
            "file_name": s["file_name"],
            "file_path": s.get("file_path"),
            "poison_category": s["category"],
            "read_count": s["count"],
            "session_count": len(s["sessions"]),
            "total_bytes": s["total_bytes"],
            "confidence": "high",
            "suggested_pattern": s["suggested_pattern"],
        })

    return sorted(results, key=lambda x: -x["total_bytes"])


# ─── 建议生成 ───────────────────────────────────────────

def generate_contextignore_suggestions(poison_files: list[dict]) -> list[str]:
    """从毒药文件统计生成 .contextignore 候选模式"""
    patterns = []
    seen = set()
    for pf in poison_files:
        pattern = pf["suggested_pattern"]
        if pattern not in seen:
            patterns.append(pattern)
            seen.add(pattern)
    return patterns


def generate_claude_md_suggestions(
    file_reads: list[dict],
    bloated_commands: list[dict],
    failure_rates: list[dict],
) -> list[dict]:
    """生成 CLAUDE.md 候选规则"""
    suggestions = []

    # 基于文件读取
    poison_reads = [f for f in file_reads if f.get("poison_category")]
    if poison_reads:
        categories = set(f["poison_category"] for f in poison_reads)
        if "lockfile" in categories:
            suggestions.append({
                "rule": "不要读取 lockfile（package-lock.json, yarn.lock, pnpm-lock.yaml 等）。用 Glob 确认存在即可。",
                "reason": f"历史中 lockfile 被读取 {sum(f['read_count'] for f in poison_reads if f['poison_category']=='lockfile')} 次，累计 {sum(f['total_bytes'] for f in poison_reads if f['poison_category']=='lockfile') // 1024} KB",
                "confidence": "high",
            })
        if "build_output" in categories:
            suggestions.append({
                "rule": "不要读取构建产物（dist/, build/, .next/）。这些文件通常很大且变化频繁。",
                "reason": f"历史中构建产物被读取 {sum(f['read_count'] for f in poison_reads if f['poison_category']=='build_output')} 次",
                "confidence": "high",
            })

    # 基于大输出
    if bloated_commands:
        total_waste_kb = sum(c["total_bytes"] for c in bloated_commands) // 1024
        suggestions.append({
            "rule": "对大文件使用 ripgrep 或 grep 替代全量读取。Bash 输出超过 20KB 时用 head/tail 截断。",
            "reason": f"历史中 {len(bloated_commands)} 类命令累计产生 {total_waste_kb} KB 大输出",
            "confidence": "medium",
        })

    # 基于失败率
    high_fail = [f for f in failure_rates if f["failure_rate"] > 0.4]
    if high_fail:
        tools = ", ".join(f["tool_name"] for f in high_fail)
        suggestions.append({
            "rule": f"注意 {tools} 的高失败率，检查命令语法和参数后再执行。",
            "reason": f"历史失败率: {', '.join(f'{f['tool_name']}={f['failure_rate']:.0%}' for f in high_fail)}",
            "confidence": "medium",
        })

    return suggestions


# ─── 主入口 ─────────────────────────────────────────────

def run_learning(min_sessions: int = MIN_SESSIONS_DEFAULT) -> dict:
    """运行历史学习分析，返回结构化报告"""
    timelines = build_timelines()
    if not timelines:
        return {"error": "没有找到 session 数据"}

    # 聚合分析
    file_reads = analyze_file_reads(timelines, min_sessions)
    bloated_commands = analyze_bloated_commands(timelines)
    failure_rates = analyze_failure_rates(timelines)
    poison_files = analyze_poison_files(timelines, min_sessions)

    # 生成建议
    contextignore = generate_contextignore_suggestions(poison_files)
    claude_md = generate_claude_md_suggestions(file_reads, bloated_commands, failure_rates)

    # 时间范围
    all_timestamps = []
    for tl in timelines:
        for a in tl.api_calls:
            if a.timestamp:
                all_timestamps.append(a.timestamp)
    time_range = [min(all_timestamps), max(all_timestamps)] if all_timestamps else ["", ""]

    report = {
        "schema_version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "params": {
            "min_sessions": min_sessions,
            "large_output_threshold_bytes": LARGE_OUTPUT_THRESHOLD,
            "large_read_threshold_bytes": LARGE_READ_THRESHOLD,
        },
        "source": {
            "session_count": len(timelines),
            "time_range": time_range,
        },
        "file_reads": file_reads[:20],
        "bloated_commands": bloated_commands[:10],
        "high_failure_tools": failure_rates,
        "poison_files": poison_files[:20],
        "contextignore_suggestions": contextignore,
        "claude_md_suggestions": claude_md,
    }

    return report


def write_learning_artifacts(report: dict, output_dir: str | Path = ".") -> None:
    """写入学习产物文件"""
    out = Path(output_dir)

    # 1. learning-report.json
    json_path = out / "learning-report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📄 JSON 报告: {json_path}")

    # 2. .contextignore.suggested
    if report.get("contextignore_suggestions"):
        lines = [
            "# AIEA 建议的忽略模式",
            f"# 生成时间: {report['generated_at']}",
            f"# 分析 session 数: {report['source']['session_count']}",
            "# 人工确认后重命名为 .contextignore",
            "",
        ]
        for pattern in report["contextignore_suggestions"]:
            lines.append(pattern)
        ci_path = out / ".contextignore.suggested"
        ci_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  📄 忽略建议: {ci_path}")

    # 3. CLAUDE.md.suggestions
    if report.get("claude_md_suggestions"):
        lines = [
            "# AIEA 建议的 CLAUDE.md 规则",
            f"# 生成时间: {report['generated_at']}",
            "# 人工筛选后添加到项目 CLAUDE.md",
            "",
        ]
        for s in report["claude_md_suggestions"]:
            lines.append(f"## 建议 (置信度: {s['confidence']})")
            lines.append(f"**规则**: {s['rule']}")
            lines.append(f"**依据**: {s['reason']}")
            lines.append("")
        md_path = out / "CLAUDE.md.suggestions"
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  📄 CLAUDE.md 建议: {md_path}")


def print_learning_summary(report: dict) -> None:
    """打印学习摘要"""
    if "error" in report:
        print(f"\n  ❌ {report['error']}")
        return

    src = report["source"]
    print(f"\n  📊 历史学习分析")
    print(f"  {'─' * 40}")
    print(f"  分析 session 数: {src['session_count']}")
    print(f"  时间范围: {src['time_range'][0][:10]} ~ {src['time_range'][1][:10]}")

    # 文件读取
    if report["file_reads"]:
        print(f"\n  📖 高频读取文件 Top 5")
        for f in report["file_reads"][:5]:
            poison_tag = f" [{f['poison_category']}]" if f.get("poison_category") else ""
            print(f"    {f['file_name']:<40} {f['read_count']:>4} 次 / {f['session_count']} session / {f['total_bytes']//1024:>6} KB{poison_tag}")

    # 毒药文件
    if report["poison_files"]:
        print(f"\n  ☠ 毒药文件统计 Top 5")
        for p in report["poison_files"][:5]:
            print(f"    {p['file_name']:<40} {p['read_count']:>4} 次 / {p['session_count']} session / {p['total_bytes']//1024:>6} KB [{p['poison_category']}]")

    # 大输出
    if report["bloated_commands"]:
        print(f"\n  📦 大输出命令 Top 5")
        for c in report["bloated_commands"][:5]:
            print(f"    {c['command']:<40} {c['call_count']:>4} 次 / {c['session_count']} session / {c['total_bytes']//1024:>6} KB (max {c['max_bytes']//1024} KB)")

    # 失败率
    if report["high_failure_tools"]:
        print(f"\n  ⚠ 高失败率工具")
        for f in report["high_failure_tools"]:
            print(f"    {f['tool_name']:<16} {f['failure_rate']:.0%} ({f['failures']}/{f['total_calls']})")

    # 建议
    if report["contextignore_suggestions"]:
        print(f"\n  📝 .contextignore 建议 ({len(report['contextignore_suggestions'])} 条)")
        for p in report["contextignore_suggestions"][:5]:
            print(f"    + {p}")
        if len(report["contextignore_suggestions"]) > 5:
            print(f"    ... 还有 {len(report['contextignore_suggestions'])-5} 条")

    if report["claude_md_suggestions"]:
        print(f"\n  📝 CLAUDE.md 建议 ({len(report['claude_md_suggestions'])} 条)")
        for s in report["claude_md_suggestions"]:
            print(f"    [{s['confidence']}] {s['rule'][:60]}...")
