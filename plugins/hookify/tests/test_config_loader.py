"""Tests for plugins/hookify/core/config_loader.py."""

from hookify.core.config_loader import (
    Condition,
    Rule,
    extract_frontmatter,
    load_rule_file,
    load_rules,
)

DANGEROUS_RM = """---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\\s+-rf
action: block
---

Dangerous rm command detected!
"""

SENSITIVE_FILES = """---
name: warn-sensitive-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \\.env$|\\.env\\.|credentials|secrets
---

Sensitive file detected
"""


class TestExtractFrontmatter:
    def test_no_frontmatter_returns_content_unchanged(self):
        content = "Just a plain markdown file with no frontmatter."
        frontmatter, message = extract_frontmatter(content)
        assert frontmatter == {}
        assert message == content

    def test_truncated_frontmatter_without_closing_marker(self):
        content = "---\nname: broken\n"
        frontmatter, message = extract_frontmatter(content)
        assert frontmatter == {}
        assert message == content

    def test_simple_key_value_pairs(self):
        frontmatter, message = extract_frontmatter(DANGEROUS_RM)
        assert frontmatter["name"] == "block-dangerous-rm"
        assert frontmatter["enabled"] is True
        assert frontmatter["event"] == "bash"
        assert frontmatter["pattern"] == "rm\\s+-rf"
        assert frontmatter["action"] == "block"
        assert message == "Dangerous rm command detected!"

    def test_boolean_coercion_is_case_insensitive(self):
        content = "---\nenabled: True\nlocked: FALSE\n---\nbody"
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["enabled"] is True
        assert frontmatter["locked"] is False

    def test_quoted_values_are_unquoted(self):
        content = '---\nname: "quoted-name"\npattern: \'single-quoted\'\n---\nbody'
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["name"] == "quoted-name"
        assert frontmatter["pattern"] == "single-quoted"

    def test_multiline_condition_list(self):
        frontmatter, message = extract_frontmatter(SENSITIVE_FILES)
        assert frontmatter["conditions"] == [
            {
                "field": "file_path",
                "operator": "regex_match",
                "pattern": "\\.env$|\\.env\\.|credentials|secrets",
            }
        ]
        assert message == "Sensitive file detected"

    def test_inline_comma_separated_dict_item(self):
        content = (
            "---\n"
            "name: inline-rule\n"
            "event: bash\n"
            "conditions:\n"
            "  - field: command, operator: regex_match, pattern: curl\n"
            "---\n"
            "body"
        )
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["conditions"] == [
            {"field": "command", "operator": "regex_match", "pattern": "curl"}
        ]

    def test_inline_dict_pattern_containing_unquoted_comma_is_preserved(self):
        content = (
            "---\n"
            "conditions:\n"
            "  - field: command, operator: contains, pattern: foo, bar\n"
            "---\n"
            "body"
        )
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["conditions"][0]["pattern"] == "foo, bar"

    def test_inline_dict_pattern_containing_quoted_comma_is_preserved(self):
        content = (
            "---\n"
            "conditions:\n"
            '  - field: command, operator: contains, pattern: "foo, bar"\n'
            "---\n"
            "body"
        )
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["conditions"][0] == {
            "field": "command",
            "operator": "contains",
            "pattern": "foo, bar",
        }

    def test_comments_and_blank_lines_are_skipped(self):
        content = (
            "---\n"
            "# this is a comment\n"
            "name: with-comments\n"
            "\n"
            "enabled: true\n"
            "---\n"
            "body"
        )
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter == {"name": "with-comments", "enabled": True}

    def test_simple_list_without_dict_items(self):
        content = "---\ntags:\n  - foo\n  - bar\n---\nbody"
        frontmatter, _ = extract_frontmatter(content)
        assert frontmatter["tags"] == ["foo", "bar"]


class TestConditionFromDict:
    def test_defaults(self):
        condition = Condition.from_dict({})
        assert condition.field == ""
        assert condition.operator == "regex_match"
        assert condition.pattern == ""

    def test_explicit_values(self):
        condition = Condition.from_dict(
            {"field": "file_path", "operator": "contains", "pattern": ".env"}
        )
        assert condition.field == "file_path"
        assert condition.operator == "contains"
        assert condition.pattern == ".env"


class TestRuleFromDict:
    def test_legacy_pattern_with_bash_event_infers_command_field(self):
        rule = Rule.from_dict({"event": "bash", "pattern": r"rm\s+-rf"}, "msg")
        assert rule.conditions == [
            Condition(field="command", operator="regex_match", pattern=r"rm\s+-rf")
        ]

    def test_legacy_pattern_with_file_event_infers_new_text_field(self):
        rule = Rule.from_dict({"event": "file", "pattern": r"console\.log\("}, "msg")
        assert rule.conditions[0].field == "new_text"

    def test_legacy_pattern_with_other_event_infers_content_field(self):
        rule = Rule.from_dict({"event": "stop", "pattern": "foo"}, "msg")
        assert rule.conditions[0].field == "content"

    def test_explicit_conditions_take_priority_over_legacy_pattern(self):
        frontmatter = {
            "event": "bash",
            "pattern": "should-be-ignored",
            "conditions": [{"field": "command", "operator": "equals", "pattern": "ls"}],
        }
        rule = Rule.from_dict(frontmatter, "msg")
        assert len(rule.conditions) == 1
        assert rule.conditions[0].pattern == "ls"

    def test_defaults(self):
        rule = Rule.from_dict({}, "")
        assert rule.name == "unnamed"
        assert rule.enabled is True
        assert rule.event == "all"
        assert rule.action == "warn"
        assert rule.tool_matcher is None
        assert rule.conditions == []

    def test_message_is_stripped(self):
        rule = Rule.from_dict({}, "  \n  hello world  \n")
        assert rule.message == "hello world"


class TestLoadRuleFile:
    def test_valid_file_returns_rule(self, tmp_path):
        rule_file = tmp_path / "hookify.test.local.md"
        rule_file.write_text(DANGEROUS_RM)
        rule = load_rule_file(str(rule_file))
        assert rule.name == "block-dangerous-rm"
        assert rule.action == "block"

    def test_missing_frontmatter_returns_none(self, tmp_path, capsys):
        rule_file = tmp_path / "hookify.bad.local.md"
        rule_file.write_text("no frontmatter here")
        rule = load_rule_file(str(rule_file))
        assert rule is None
        assert "missing YAML frontmatter" in capsys.readouterr().err

    def test_nonexistent_file_returns_none(self, tmp_path, capsys):
        rule = load_rule_file(str(tmp_path / "does-not-exist.local.md"))
        assert rule is None
        assert "Cannot read" in capsys.readouterr().err


class TestLoadRules:
    def test_loads_enabled_rules_from_dot_claude_dir(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hookify.rm.local.md").write_text(DANGEROUS_RM)
        monkeypatch.chdir(tmp_path)

        rules = load_rules()
        assert len(rules) == 1
        assert rules[0].name == "block-dangerous-rm"

    def test_disabled_rules_are_excluded(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hookify.off.local.md").write_text(
            DANGEROUS_RM.replace("enabled: true", "enabled: false")
        )
        monkeypatch.chdir(tmp_path)

        assert load_rules() == []

    def test_event_filter_excludes_non_matching_rules(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hookify.rm.local.md").write_text(DANGEROUS_RM)  # event: bash
        monkeypatch.chdir(tmp_path)

        assert load_rules(event="file") == []
        assert len(load_rules(event="bash")) == 1

    def test_event_all_matches_any_filter(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        content = DANGEROUS_RM.replace("event: bash", "event: all")
        (claude_dir / "hookify.rm.local.md").write_text(content)
        monkeypatch.chdir(tmp_path)

        assert len(load_rules(event="file")) == 1

    def test_invalid_file_is_skipped_but_others_still_load(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hookify.bad.local.md").write_text("not frontmatter")
        (claude_dir / "hookify.good.local.md").write_text(DANGEROUS_RM)
        monkeypatch.chdir(tmp_path)

        rules = load_rules()
        assert len(rules) == 1
        assert rules[0].name == "block-dangerous-rm"

    def test_no_matching_files_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # no .claude dir at all
        assert load_rules() == []
