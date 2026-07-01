"""Tests for examples/hooks/bash_command_validator_example.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bash_command_validator_example import _validate_command

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "examples" / "hooks" / "bash_command_validator_example.py"


def run_hook(input_data):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )


class TestValidateCommand:
    def test_grep_at_start_without_pipe_is_flagged(self):
        issues = _validate_command("grep foo bar.txt")
        assert len(issues) == 1
        assert "rg" in issues[0]

    def test_grep_at_start_with_later_pipe_is_not_flagged(self):
        assert _validate_command("grep foo | wc -l") == []

    def test_grep_not_at_start_of_command_is_not_flagged(self):
        assert _validate_command("echo foo | grep bar") == []

    def test_find_with_name_flag_is_flagged(self):
        issues = _validate_command("find . -name '*.py'")
        assert len(issues) == 1
        assert "find -name" in issues[0]

    def test_find_without_name_flag_is_not_flagged(self):
        assert _validate_command("find . -type f") == []

    def test_command_matching_no_rules_returns_empty_list(self):
        assert _validate_command("ls -la") == []

    def test_rules_are_mutually_exclusive_due_to_start_anchor(self):
        # Both patterns anchor on ^, so a command can only ever match the
        # rule for whichever command word actually starts the string.
        assert len(_validate_command("grep foo bar.txt; find . -name '*.py'")) == 1
        assert len(_validate_command("find . -name '*.py'; grep foo bar.txt")) == 1


class TestMain:
    def test_invalid_json_exits_1_with_stderr(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Invalid JSON input" in result.stderr

    def test_non_bash_tool_exits_0_with_no_output(self):
        result = run_hook({"tool_name": "Write", "tool_input": {"command": "grep foo"}})
        assert result.returncode == 0
        assert result.stderr == ""

    def test_missing_tool_name_exits_0(self):
        result = run_hook({"tool_input": {"command": "grep foo"}})
        assert result.returncode == 0

    def test_empty_command_exits_0(self):
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": ""}})
        assert result.returncode == 0

    def test_missing_command_exits_0(self):
        result = run_hook({"tool_name": "Bash", "tool_input": {}})
        assert result.returncode == 0

    def test_flagged_command_exits_2_with_bulleted_stderr(self):
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": "grep foo bar.txt"}})
        assert result.returncode == 2
        assert result.stderr.startswith("• ")
        assert "rg" in result.stderr

    def test_clean_command_exits_0_with_no_stderr(self):
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        assert result.returncode == 0
        assert result.stderr == ""
