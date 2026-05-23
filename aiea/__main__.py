#!/usr/bin/env python3
"""AIEA — AI Efficiency Auditor

用法:
    python -m aiea scan
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
    import json as _json

    session_filter = args.session
    output_file = args.output
    json_output = getattr(args, "json", False)

    # 1. 数据接入（从 ~/.claude/projects/ 读取 session JSONL）
    if not json_output:
        print(f"\n  🔍 扫描 session JSONL: ~/.claude/projects/")
    timelines = build_timelines()

    if not timelines:
        if not json_output:
            print("\n  ❌ 没有找到可解析的 session 数据。")
            print("  请先运行 Claude Code 产生一些日志后重试。")
        return 1

    if session_filter:
        timelines = [
            tl for tl in timelines
            if tl.session_id.startswith(session_filter)
        ]
        if not timelines:
            if not json_output:
                print(f"\n  ❌ 没有找到匹配 session_id 前缀 '{session_filter}' 的 session。")
            return 1

    if not json_output:
        print_summary(timelines)

    # 2. 运行嗅探器
    if not json_output:
        print(f"\n  🔎 运行嗅探器...")
    all_findings: dict[str, list] = {}
    for tl in timelines:
        findings = run_all(tl)
        all_findings[tl.session_id] = findings

    # 3. 输出
    if json_output:
        # JSON 输出
        report = {
            "sessions": len(timelines),
            "total_cost_usd": sum(tl.total_cost_usd for tl in timelines),
            "total_findings": sum(len(fs) for fs in all_findings.values()),
            "timelines": [],
            "findings": {},
        }
        for tl in timelines:
            report["timelines"].append({
                "session_id": tl.session_id,
                "model": tl.model,
                "api_calls": tl.total_api_calls,
                "tool_calls": len(tl.tool_calls),
                "cost_usd": tl.total_cost_usd,
                "input_tokens": tl.total_input_tokens,
                "output_tokens": tl.total_output_tokens,
                "cache_hit_rate": tl.cache_hit_rate,
            })
            session_findings = all_findings.get(tl.session_id, [])
            if session_findings:
                report["findings"][tl.session_id] = [
                    {
                        "severity": f.severity,
                        "category": f.category,
                        "message": f.message,
                        "estimated_waste_usd": f.estimated_waste_usd,
                        "details": f.details,
                    }
                    for f in session_findings
                ]
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    elif output_file:
        md = generate_markdown(timelines, all_findings)
        out_path = Path(output_file)
        out_path.write_text(md, encoding="utf-8")
        print(f"\n  📄 报告已写入: {out_path.resolve()}")
        print_report(timelines, all_findings)
    else:
        print_report(timelines, all_findings)

    total_findings = sum(len(fs) for fs in all_findings.values())
    if not json_output:
        print(f"  ✅ 完成! 共扫描 {len(timelines)} 个 session, {total_findings} 条发现。\n")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from aiea.learner import run_learning, print_learning_summary, write_learning_artifacts

    report = run_learning(min_sessions=args.min_sessions)
    print_learning_summary(report)

    if args.output_dir:
        write_learning_artifacts(report, args.output_dir)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AIEA — AI Efficiency Auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m aiea scan                        # 扫描所有 session\n"
            "  python -m aiea scan --session abc123       # 只分析特定 session\n"
            "  python -m aiea scan --output report.md     # 生成 Markdown 报告\n"
            "  python -m aiea learn                       # 历史学习分析\n"
            "  python -m aiea learn --output-dir ./       # 生成建议草案文件\n"
        ),
    )

    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="扫描并分析 session JSONL 日志")
    scan_parser.add_argument(
        "--session", type=str, default=None,
        help="session_id 前缀过滤",
    )
    scan_parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="输出 Markdown 报告文件路径",
    )
    scan_parser.add_argument(
        "--json", action="store_true", default=False,
        help="以 JSON 格式输出结果",
    )

    learn_parser = sub.add_parser("learn", help="从历史 session 学习浪费模式，生成建议草案")
    learn_parser.add_argument(
        "--min-sessions", type=int, default=3,
        help="最少跨 session 数 (默认: 3)",
    )
    learn_parser.add_argument(
        "--output-dir", type=str, default=None,
        help="输出建议文件的目录 (不指定则只打印摘要)",
    )

    args = parser.parse_args()
    if args.command == "scan":
        return cmd_scan(args)
    elif args.command == "learn":
        return cmd_learn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
