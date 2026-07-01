"""Tests for plugins/hookify/core/rule_engine.py."""

import pytest

from hookify.core.config_loader import Condition, Rule
from hookify.core.rule_engine import RuleEngine


def make_rule(
    name="r",
    conditions=None,
    action="warn",
    tool_matcher=None,
    message="msg",
    event="bash",
):
    return Rule(
        name=name,
        enabled=True,
        event=event,
        conditions=conditions or [],
        action=action,
        tool_matcher=tool_matcher,
        message=message,
    )


@pytest.fixture
def engine():
    return RuleEngine()


class TestEvaluateRules:
    def test_no_rules_returns_empty_dict(self, engine):
        assert engine.evaluate_rules([], {"tool_name": "Bash", "tool_input": {}}) == {}

    def test_no_matching_rule_returns_empty_dict(self, engine):
        rule = make_rule(conditions=[Condition("command", "contains", "rm -rf")])
        input_data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        assert engine.evaluate_rules([rule], input_data) == {}

    def test_warning_rule_allows_operation_with_message(self, engine):
        rule = make_rule(
            name="warn-rule",
            action="warn",
            conditions=[Condition("command", "contains", "console.log")],
            message="please remove debug logging",
        )
        input_data = {"tool_name": "Bash", "tool_input": {"command": "echo console.log(1)"}}
        result = engine.evaluate_rules([rule], input_data)
        assert "[warn-rule]" in result["systemMessage"]
        assert "please remove debug logging" in result["systemMessage"]
        assert "hookSpecificOutput" not in result
        assert "decision" not in result

    def test_blocking_rule_on_pretooluse_denies_permission(self, engine):
        rule = make_rule(
            name="block-rule",
            action="block",
            conditions=[Condition("command", "regex_match", r"rm\s+-rf")],
        )
        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
        result = engine.evaluate_rules([rule], input_data)
        assert result["hookSpecificOutput"] == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
        }
        assert "[block-rule]" in result["systemMessage"]

    def test_blocking_rule_on_stop_event_returns_decision_block(self, engine, tmp_path):
        transcript = tmp_path / "transcript.txt"
        transcript.write_text("only ran the app, no tests")
        rule = make_rule(
            name="require-tests",
            action="block",
            event="stop",
            conditions=[Condition("transcript", "not_contains", "pytest")],
        )
        input_data = {
            "hook_event_name": "Stop",
            "reason": "done",
            "transcript_path": str(transcript),
        }
        result = engine.evaluate_rules([rule], input_data)
        assert result["decision"] == "block"
        assert "[require-tests]" in result["reason"]
        assert "[require-tests]" in result["systemMessage"]

    def test_blocking_rule_on_unknown_event_returns_message_only(self, engine):
        rule = make_rule(
            name="generic-block",
            action="block",
            conditions=[Condition("command", "contains", "x")],
        )
        input_data = {
            "hook_event_name": "SomeOtherEvent",
            "tool_name": "Bash",
            "tool_input": {"command": "x"},
        }
        result = engine.evaluate_rules([rule], input_data)
        assert result == {"systemMessage": "**[generic-block]**\nmsg"}

    def test_blocking_takes_priority_and_drops_warnings(self, engine):
        block_rule = make_rule(
            name="blocker", action="block", conditions=[Condition("command", "contains", "rm")]
        )
        warn_rule = make_rule(
            name="warner", action="warn", conditions=[Condition("command", "contains", "rm")]
        )
        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm file"},
        }
        result = engine.evaluate_rules([block_rule, warn_rule], input_data)
        assert "blocker" in result["systemMessage"]
        assert "warner" not in result["systemMessage"]

    def test_multiple_blocking_rules_combine_messages(self, engine):
        rule_a = make_rule(
            name="a", action="block", message="A msg", conditions=[Condition("command", "contains", "x")]
        )
        rule_b = make_rule(
            name="b", action="block", message="B msg", conditions=[Condition("command", "contains", "x")]
        )
        input_data = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "x"}}
        result = engine.evaluate_rules([rule_a, rule_b], input_data)
        assert "[a]" in result["systemMessage"] and "A msg" in result["systemMessage"]
        assert "[b]" in result["systemMessage"] and "B msg" in result["systemMessage"]


class TestRuleMatching:
    def test_rule_with_no_conditions_never_matches(self, engine):
        rule = make_rule(conditions=[])
        assert engine._rule_matches(rule, {"tool_name": "Bash", "tool_input": {"command": "x"}}) is False

    def test_tool_matcher_filters_out_non_matching_tool(self, engine):
        rule = make_rule(tool_matcher="Edit", conditions=[Condition("file_path", "contains", ".env")])
        input_data = {"tool_name": "Bash", "tool_input": {"file_path": ".env"}}
        assert engine._rule_matches(rule, input_data) is False

    def test_tool_matcher_wildcard_matches_any_tool(self, engine):
        rule = make_rule(tool_matcher="*", conditions=[Condition("command", "contains", "x")])
        input_data = {"tool_name": "AnyTool", "tool_input": {"command": "x"}}
        assert engine._rule_matches(rule, input_data) is True

    def test_tool_matcher_or_pattern(self, engine):
        rule = make_rule(tool_matcher="Edit|Write", conditions=[Condition("file_path", "contains", "x")])
        for tool in ("Edit", "Write"):
            input_data = {"tool_name": tool, "tool_input": {"file_path": "x"}}
            assert engine._rule_matches(rule, input_data) is True
        input_data = {"tool_name": "MultiEdit", "tool_input": {"file_path": "x"}}
        assert engine._rule_matches(rule, input_data) is False

    def test_all_conditions_must_match(self, engine):
        rule = make_rule(
            conditions=[
                Condition("command", "contains", "rm"),
                Condition("command", "contains", "nonexistent-substring"),
            ]
        )
        input_data = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        assert engine._rule_matches(rule, input_data) is False


class TestCheckCondition:
    @pytest.mark.parametrize(
        "operator,pattern,field_value,expected",
        [
            ("regex_match", r"^rm\s", "rm -rf /", True),
            ("regex_match", r"^rm\s", "echo rm", False),
            ("contains", "foo", "foobar", True),
            ("contains", "foo", "bar", False),
            ("equals", "exact", "exact", True),
            ("equals", "exact", "exact ", False),
            ("not_contains", "foo", "bar", True),
            ("not_contains", "foo", "foobar", False),
            ("starts_with", "foo", "foobar", True),
            ("starts_with", "foo", "barfoo", False),
            ("ends_with", "bar", "foobar", True),
            ("ends_with", "bar", "barfoo", False),
            ("unknown_operator", "x", "x", False),
        ],
    )
    def test_operators(self, engine, operator, pattern, field_value, expected):
        condition = Condition(field="command", operator=operator, pattern=pattern)
        result = engine._check_condition(condition, "Bash", {"command": field_value}, {})
        assert result is expected

    def test_missing_field_never_matches(self, engine):
        condition = Condition(field="nonexistent", operator="contains", pattern="x")
        assert engine._check_condition(condition, "Bash", {}, {}) is False


class TestExtractField:
    def test_direct_tool_input_field(self, engine):
        assert engine._extract_field("custom_field", "Bash", {"custom_field": "value"}, {}) == "value"

    def test_direct_tool_input_field_non_string_is_stringified(self, engine):
        assert engine._extract_field("count", "Bash", {"count": 5}, {}) == "5"

    def test_bash_command_field(self, engine):
        assert engine._extract_field("command", "Bash", {"command": "ls -la"}, {}) == "ls -la"

    def test_write_content_field(self, engine):
        assert engine._extract_field("content", "Write", {"content": "hello"}, {}) == "hello"

    def test_edit_content_falls_back_to_new_string(self, engine):
        assert engine._extract_field("content", "Edit", {"new_string": "hi"}, {}) == "hi"

    def test_edit_new_text_alias_maps_to_new_string(self, engine):
        assert engine._extract_field("new_text", "Edit", {"new_string": "hi"}, {}) == "hi"

    def test_edit_old_text_alias_maps_to_old_string(self, engine):
        assert engine._extract_field("old_text", "Edit", {"old_string": "bye"}, {}) == "bye"

    def test_edit_file_path(self, engine):
        assert engine._extract_field("file_path", "Edit", {"file_path": "/tmp/x"}, {}) == "/tmp/x"

    def test_multiedit_file_path(self, engine):
        assert engine._extract_field("file_path", "MultiEdit", {"file_path": "/tmp/x"}, {}) == "/tmp/x"

    def test_multiedit_concatenates_new_text_across_edits(self, engine):
        tool_input = {"edits": [{"new_string": "a"}, {"new_string": "b"}]}
        assert engine._extract_field("new_text", "MultiEdit", tool_input, {}) == "a b"

    def test_stop_event_reason_field(self, engine):
        input_data = {"reason": "done working"}
        assert engine._extract_field("reason", "", {}, input_data) == "done working"

    def test_stop_event_transcript_field_reads_file(self, engine, tmp_path):
        transcript = tmp_path / "transcript.txt"
        transcript.write_text("ran pytest successfully")
        input_data = {"transcript_path": str(transcript)}
        assert engine._extract_field("transcript", "", {}, input_data) == "ran pytest successfully"

    def test_stop_event_transcript_missing_file_returns_empty_string(self, engine, capsys):
        input_data = {"transcript_path": "/nonexistent/path/transcript.txt"}
        assert engine._extract_field("transcript", "", {}, input_data) == ""
        assert "not found" in capsys.readouterr().err

    def test_userpromptsubmit_user_prompt_field(self, engine):
        input_data = {"user_prompt": "do the thing"}
        assert engine._extract_field("user_prompt", "", {}, input_data) == "do the thing"

    def test_unknown_field_returns_none(self, engine):
        assert engine._extract_field("nonexistent", "Bash", {}, {}) is None


class TestRegexMatch:
    def test_valid_pattern_matches(self, engine):
        assert engine._regex_match(r"\d+", "abc123") is True

    def test_valid_pattern_no_match(self, engine):
        assert engine._regex_match(r"^\d+$", "abc123") is False

    def test_invalid_pattern_is_caught_and_returns_false(self, engine, capsys):
        assert engine._regex_match("(unclosed", "anything") is False
        assert "Invalid regex pattern" in capsys.readouterr().err

    def test_matches_are_case_insensitive(self, engine):
        assert engine._regex_match("HELLO", "say hello there") is True
