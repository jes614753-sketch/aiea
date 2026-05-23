import json
import tempfile
import unittest
from pathlib import Path

from aiea.ingestion import build_timelines
from aiea.learner import (
    analyze_bloated_commands,
    analyze_poison_files,
    generate_contextignore_suggestions,
)
from aiea.models import SessionTimeline, ToolUseSuccessEvent
from aiea.sniffers import get_all_sniffers
from aiea.sniffers.death_loop import DeathLoopSniffer
from aiea.sniffers.toxic_file import ToxicFileSniffer


def _write_session_jsonl(root: Path, session_id: str) -> Path:
    path = root / "project" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-05-23T10:00:00Z",
            "message": {
                "id": "msg-1",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-read",
                        "name": "Read",
                        "input": {
                            "file_path": "/repo/node_modules/react/index.js"
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-bash",
                        "name": "Bash",
                        "input": {
                            "command": "npm run build --verbose"
                        },
                    },
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-05-23T10:00:05Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-read",
                        "content": [{"type": "text", "text": "x" * 120000}],
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-bash",
                        "content": [{"type": "text", "text": "Exit code 1\n" + "y" * 25000}],
                        "is_error": True,
                    },
                ]
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


class RegressionTests(unittest.TestCase):
    def test_sniffers_are_registered(self):
        self.assertIn("toxic_file", get_all_sniffers())
        self.assertIn("death_loop", get_all_sniffers())
        self.assertIn("bloated_context", get_all_sniffers())

    def test_ingestion_preserves_file_path_and_bash_command_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_session_jsonl(Path(tmp), "session-1")

            timelines = build_timelines(tmp)

        calls = {tc.tool_name: tc for tc in timelines[0].tool_calls}
        self.assertEqual(calls["Read"].file_path, "/repo/node_modules/react/index.js")
        self.assertEqual(calls["Read"].file_name, "index.js")
        self.assertEqual(calls["Bash"].bash_command_text, "npm run build --verbose")
        self.assertEqual(calls["Bash"].bash_command_len, len("npm run build --verbose"))
        self.assertGreaterEqual(calls["Bash"].duration_ms, 5000)

    def test_learner_suggests_directory_patterns_for_path_poison(self):
        timelines = []
        for i in range(3):
            tl = SessionTimeline(session_id=f"s{i}")
            tl.tool_calls.append(ToolUseSuccessEvent(
                tool_name="Read",
                duration_ms=0,
                tool_result_size_bytes=120000,
                tool_input_size_bytes=100,
                file_extension="js",
                file_path="/repo/node_modules/react/index.js",
                file_path_len=len("/repo/node_modules/react/index.js"),
                file_name="index.js",
            ))
            timelines.append(tl)

        poison_files = analyze_poison_files(timelines, min_sessions=3)
        suggestions = generate_contextignore_suggestions(poison_files)

        self.assertEqual(poison_files[0]["poison_category"], "dependency")
        self.assertIn("node_modules/", suggestions)
        self.assertNotIn("index.js", suggestions)

    def test_bloated_commands_group_by_real_command(self):
        timelines = []
        for i in range(2):
            tl = SessionTimeline(session_id=f"s{i}")
            tl.tool_calls.append(ToolUseSuccessEvent(
                tool_name="Bash",
                duration_ms=0,
                tool_result_size_bytes=25000,
                tool_input_size_bytes=100,
                bash_command_len=len("npm run build --verbose"),
                bash_command_text="npm run build --verbose",
            ))
            timelines.append(tl)

        commands = analyze_bloated_commands(timelines)

        self.assertEqual(commands[0]["command"], "npm run build --verbose")
        self.assertEqual(commands[0]["call_count"], 2)

    def test_toxic_file_sniffer_matches_path_patterns(self):
        tl = SessionTimeline(session_id="s1")
        tl.tool_calls.append(ToolUseSuccessEvent(
            tool_name="Read",
            duration_ms=0,
            tool_result_size_bytes=120000,
            tool_input_size_bytes=100,
            file_extension="js",
            file_path="/repo/dist/assets/app.js",
            file_name="app.js",
        ))

        findings = ToxicFileSniffer().sniff(tl)

        self.assertEqual(findings[0].details["category"], "编译产物")

    def test_death_loop_requires_failed_or_timeout_commands(self):
        success_timeline = SessionTimeline(session_id="ok")
        for _ in range(3):
            success_timeline.tool_calls.append(ToolUseSuccessEvent(
                tool_name="Bash",
                duration_ms=0,
                tool_result_size_bytes=100,
                tool_input_size_bytes=100,
                bash_command_len=len("npm test"),
                bash_command_text="npm test",
                tool_status="success",
            ))
        self.assertEqual(DeathLoopSniffer().sniff(success_timeline), [])

        failed_timeline = SessionTimeline(session_id="bad")
        for _ in range(3):
            failed_timeline.tool_calls.append(ToolUseSuccessEvent(
                tool_name="Bash",
                duration_ms=0,
                tool_result_size_bytes=100,
                tool_input_size_bytes=100,
                bash_command_len=len("npm test"),
                bash_command_text="npm test",
                tool_status="failed",
            ))

        findings = DeathLoopSniffer().sniff(failed_timeline)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].details["command"], "npm test")


if __name__ == "__main__":
    unittest.main()
