#!/usr/bin/env python3
"""AIEA — AI Efficiency Auditor

用法:
    python -m aiea scan
    python -m aiea scan --dir ~/.claude/telemetry
    python -m aiea scan --session <session_id_prefix>
    python -m aiea scan --output report.md
"""

import argparse
import sys
from pathlib import Path

from aiea.ingestion import build_timelines, print_summary
from aiea.sniffers import run_all
from aiea.reporters import print_report, generate_markdown


def cmd_scan(args: argparse.Namespace) -> int:
    telemetry_dir = args.dir
    session_filter = args.session
    output_file = args.output

    # 1. 数据接入
    print(f"\n  🔍 扫描 telemetry 目录: {telemetry_dir or '~/.claude/telemetry/'}")
    timelines = build_timelines(telemetry_dir)

    if not timelines:
        print("\n  ❌ 没有找到可解析的 telemetry 数据。")
        print("  请先运行 Claude Code 产生一些日志后重试。")
        return 1

    if session_filter:
        timelines = [
            tl for tl in timelines
            if tl.session_id.startswith(session_filter)
        ]
        if not timelines:
            print(f"\n  ❌ 没有找到匹配 session_id 前缀 '{session_filter}' 的 session。")
            return 1

    print_summary(timelines)

    # 2. 运行嗅探器
    print(f"\n  🔎 运行嗅探器...")
    all_findings: dict[str, list] = {}
    for tl in timelines:
        findings = run_all(tl)
        all_findings[tl.session_id] = findings

    # 3. 输出
    if output_file:
        md = generate_markdown(timelines, all_findings)
        out_path = Path(output_file)
        out_path.write_text(md, encoding="utf-8")
        print(f"\n  📄 报告已写入: {out_path.resolve()}")
        print_report(timelines, all_findings)
    else:
        print_report(timelines, all_findings)

    total_findings = sum(len(fs) for fs in all_findings.values())
    print(f"  ✅ 完成! 共扫描 {len(timelines)} 个 session, {total_findings} 条发现。\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AIEA — AI Efficiency Auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m aiea scan                        # 扫描默认 telemetry 目录\n"
            "  python -m aiea scan --dir /path/to/logs    # 指定目录\n"
            "  python -m aiea scan --session abc123       # 只分析特定 session\n"
            "  python -m aiea scan --output report.md     # 生成 Markdown 报告\n"
        ),
    )

    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="扫描并分析 telemetry 日志")
    scan_parser.add_argument(
        "--dir", type=str, default=None,
        help="telemetry 目录路径 (默认: ~/.claude/telemetry)",
    )
    scan_parser.add_argument(
        "--session", type=str, default=None,
        help="session_id 前缀过滤",
    )
    scan_parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="输出 Markdown 报告文件路径",
    )

    args = parser.parse_args()
    if args.command == "scan":
        return cmd_scan(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
