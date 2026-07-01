"""Tests for plugins/security-guidance/hooks/security_reminder_hook.py."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from security_reminder_hook import (
    check_patterns,
    cleanup_old_state_files,
    extract_content_from_input,
    get_state_file,
    load_state,
    save_state,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "plugins" / "security-guidance" / "hooks" / "security_reminder_hook.py"


def run_hook(input_data, home, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.setdefault("ENABLE_SECURITY_REMINDER", "1")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(input_data) if input_data is not None else "not json",
        capture_output=True,
        text=True,
        env=env,
    )


class TestCheckPatterns:
    def test_github_actions_workflow_path_is_flagged(self):
        rule_name, reminder = check_patterns(".github/workflows/ci.yml", "")
        assert rule_name == "github_actions_workflow"
        assert "Command Injection" in reminder

    def test_leading_slash_is_normalized(self):
        rule_name, _ = check_patterns("/.github/workflows/ci.yaml", "")
        assert rule_name == "github_actions_workflow"

    def test_substring_pattern_matches_content(self):
        rule_name, reminder = check_patterns("foo.js", "eval(userInput)")
        assert rule_name == "eval_injection"
        assert "eval()" in reminder

    def test_first_matching_pattern_in_list_order_wins(self):
        # eval_injection comes before os_system_injection in SECURITY_PATTERNS;
        # content matching both should resolve to the earlier rule.
        rule_name, _ = check_patterns("foo.py", "eval(x); os.system(x)")
        assert rule_name == "eval_injection"

    def test_no_match_returns_none_none(self):
        assert check_patterns("foo.py", "print('hello')") == (None, None)

    def test_empty_content_does_not_crash_substring_check(self):
        assert check_patterns("foo.py", "") == (None, None)


class TestExtractContentFromInput:
    def test_write_tool_returns_content(self):
        assert extract_content_from_input("Write", {"content": "hello"}) == "hello"

    def test_edit_tool_returns_new_string(self):
        assert extract_content_from_input("Edit", {"new_string": "hi"}) == "hi"

    def test_multiedit_tool_joins_new_strings(self):
        tool_input = {"edits": [{"new_string": "a"}, {"new_string": "b"}]}
        assert extract_content_from_input("MultiEdit", tool_input) == "a b"

    def test_multiedit_with_no_edits_returns_empty_string(self):
        assert extract_content_from_input("MultiEdit", {"edits": []}) == ""

    def test_unknown_tool_returns_empty_string(self):
        assert extract_content_from_input("Bash", {"command": "ls"}) == ""


class TestGetStateFile:
    def test_path_includes_session_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        path = get_state_file("abc123")
        assert path == str(tmp_path / ".claude" / "security_warnings_state_abc123.json")


class TestLoadSaveState:
    def test_load_state_for_nonexistent_file_returns_empty_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert load_state("nosession") == set()

    def test_save_then_load_roundtrips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_state("session1", {"a-rule1", "b-rule2"})
        assert load_state("session1") == {"a-rule1", "b-rule2"}

    def test_load_state_with_corrupt_json_returns_empty_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        state_file = get_state_file("badsession")
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            f.write("not valid json")
        assert load_state("badsession") == set()


class TestCleanupOldStateFiles:
    def test_removes_files_older_than_30_days(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        old_file = claude_dir / "security_warnings_state_old.json"
        old_file.write_text("[]")
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        cleanup_old_state_files()

        assert not old_file.exists()

    def test_keeps_recent_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        recent_file = claude_dir / "security_warnings_state_recent.json"
        recent_file.write_text("[]")

        cleanup_old_state_files()

        assert recent_file.exists()

    def test_ignores_non_matching_filenames(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        unrelated_file = claude_dir / "settings.json"
        unrelated_file.write_text("{}")
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(unrelated_file, (old_time, old_time))

        cleanup_old_state_files()

        assert unrelated_file.exists()

    def test_missing_claude_dir_does_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "does-not-exist"))
        cleanup_old_state_files()  # should not raise


class TestMain:
    def test_disabled_via_env_exits_0(self, tmp_path):
        result = run_hook(
            {"tool_name": "Edit", "tool_input": {"file_path": "foo.py", "new_string": "eval(x)"}},
            tmp_path,
            extra_env={"ENABLE_SECURITY_REMINDER": "0"},
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_invalid_json_exits_0(self, tmp_path):
        result = run_hook(None, tmp_path)
        assert result.returncode == 0

    def test_non_file_tool_exits_0(self, tmp_path):
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": "eval(x)"}}, tmp_path)
        assert result.returncode == 0

    def test_missing_file_path_exits_0(self, tmp_path):
        result = run_hook({"tool_name": "Write", "tool_input": {"content": "eval(x)"}}, tmp_path)
        assert result.returncode == 0

    def test_flagged_content_blocks_with_reminder_on_stderr(self, tmp_path):
        result = run_hook(
            {
                "session_id": "sess-a",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py", "content": "eval(x)"},
            },
            tmp_path,
        )
        assert result.returncode == 2
        assert "eval()" in result.stderr

    def test_safe_content_exits_0_with_no_stderr(self, tmp_path):
        result = run_hook(
            {
                "session_id": "sess-b",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py", "content": "print('hi')"},
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_repeat_warning_in_same_session_is_suppressed(self, tmp_path):
        first = run_hook(
            {
                "session_id": "sess-c",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py", "content": "eval(x)"},
            },
            tmp_path,
        )
        second = run_hook(
            {
                "session_id": "sess-c",
                "tool_name": "Edit",
                "tool_input": {"file_path": "foo.py", "new_string": "eval(y)"},
            },
            tmp_path,
        )
        assert first.returncode == 2
        assert second.returncode == 0
        assert second.stderr == ""

    def test_same_warning_in_different_session_is_not_suppressed(self, tmp_path):
        first = run_hook(
            {
                "session_id": "sess-d",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py", "content": "eval(x)"},
            },
            tmp_path,
        )
        second = run_hook(
            {
                "session_id": "sess-e",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py", "content": "eval(x)"},
            },
            tmp_path,
        )
        assert first.returncode == 2
        assert second.returncode == 2
