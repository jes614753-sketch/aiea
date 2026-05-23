# AIEA - AI Efficiency Auditor for Claude Code

AIEA audits local Claude Code session JSONL logs and explains where token waste comes from: repeated reads, toxic files, bloated shell output, failed-command loops, and large edit/write calls.

It is not a token dashboard. The goal is to answer three questions:

- Why did this session spend so much context?
- Where did the waste happen?
- What project rules should prevent it next time?

## Version

Current version: `0.3.0`

## What's New in 0.3.0

- Added the learner module for historical waste-pattern analysis.
- Preserved actionable metadata such as full file paths and real bash command text.
- Improved `.contextignore` suggestions so directory poisons like `node_modules/` do not become unsafe basename-only ignores such as `index.js`.
- Grouped bloated command findings by real command text instead of command-length buckets.

## What's New in 0.2.0

- Switched the audit source to Claude Code local session JSONL under `~/.claude/projects`.
- Extracted `tool_use` and `tool_result` entries from message content.
- Added forensic-style audit data for file paths, command text, result sizes, and estimated command duration.

## Install

```bash
git clone https://github.com/jes614753-sketch/aiea.git
cd aiea
pip install -e .
```

AIEA uses the Python standard library only.

## Usage

```bash
# Scan local Claude Code session JSONL logs
python -m aiea scan

# Filter by session id prefix
python -m aiea scan --session <session_id_prefix>

# Output a Markdown report
python -m aiea scan --output report.md

# Output JSON for automation
python -m aiea scan --json

# Learn recurring waste patterns from historical sessions
python -m aiea learn

# Write learning artifacts such as context-ignore suggestions
python -m aiea learn --output-dir .
```

## Detected Waste Patterns

| Sniffer | Detects | Severity |
| --- | --- | --- |
| `toxic_file` | Dependency folders, build artifacts, lockfiles, media/binary files read into context | high / medium |
| `bloated_context` | Shell commands with very large output | critical / high / medium |
| `death_loop` | Repeated failed or timed-out similar commands | high / medium |

## How It Works

```text
Claude Code JSONL -> ingestion -> SessionTimeline -> sniffers -> findings -> report / learner
```

The ingestion layer reads local JSONL session files, extracts tool calls and tool results, then builds session timelines for the sniffers.

The learner aggregates historical findings and can suggest project-level prevention rules, especially for `.contextignore` and `CLAUDE.md`.

## Relationship to cc-token-governor

AIEA is the audit side of the workflow. It finds waste after a session has happened.

`cc-token-governor` is the runtime side. It turns audit findings and learned corrections into Claude Code hook policies that warn, block, or inject learned project guidance before the next wasteful action.

Recommended workflow:

1. Run Claude Code normally.
2. Use AIEA to audit local JSONL session logs.
3. Convert findings into `.contextignore`, `CLAUDE.md`, and policy suggestions.
4. Use `cc-token-governor` to enforce the recurring lessons at runtime.

## License

MIT
