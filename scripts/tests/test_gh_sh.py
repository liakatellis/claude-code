"""Tests for scripts/gh.sh — the allowlist wrapper around the `gh` CLI.

These tests never hit the network: a stub `gh` executable is placed first
on PATH that just echoes its own invocation, so we can assert on exactly
what gh.sh would have run.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GH_SH = REPO_ROOT / "scripts" / "gh.sh"

FAKE_GH_SCRIPT = """#!/usr/bin/env bash
echo "GH_HOST=$GH_HOST"
echo "ARGS:$*"
"""


@pytest.fixture
def fake_gh_path(tmp_path):
    """Directory containing a stub `gh` executable, for prepending to PATH."""
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(FAKE_GH_SCRIPT)
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def run_gh_sh(args, fake_gh_path, env_overrides=None, clear_repo=False):
    env = os.environ.copy()
    env["PATH"] = f"{fake_gh_path}:{env['PATH']}"
    env["GITHUB_REPOSITORY"] = "anthropics/claude-code"
    env.pop("GH_REPO", None)
    if clear_repo:
        env.pop("GITHUB_REPOSITORY", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(GH_SH), *args],
        env=env,
        capture_output=True,
        text=True,
    )


class TestRepoValidation:
    def test_missing_repo_env_fails(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "1"], fake_gh_path, clear_repo=True)
        assert result.returncode == 1
        assert "owner/repo format" in result.stderr

    def test_repo_without_slash_fails(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "1"], fake_gh_path, {"GITHUB_REPOSITORY": "no-slash"})
        assert result.returncode == 1
        assert "owner/repo format" in result.stderr

    def test_repo_with_extra_slash_fails(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "1"], fake_gh_path, {"GITHUB_REPOSITORY": "a/b/c"})
        assert result.returncode == 1
        assert "owner/repo format" in result.stderr

    def test_valid_repo_is_exported_and_gh_host_is_set(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "1"], fake_gh_path)
        assert result.returncode == 0
        assert "GH_HOST=github.com" in result.stdout


class TestSubcommandAllowlist:
    @pytest.mark.parametrize(
        "args",
        [
            ["issue", "close", "1"],
            ["repo", "delete"],
            ["pr", "merge", "1"],
            ["issue", "edit", "1"],
            ["label", "create", "x"],
        ],
    )
    def test_disallowed_subcommands_are_rejected(self, fake_gh_path, args):
        result = run_gh_sh(args, fake_gh_path)
        assert result.returncode == 1
        assert (
            "only 'issue view', 'issue list', 'search issues', 'label list' are allowed"
            in result.stderr
        )

    @pytest.mark.parametrize(
        "args",
        [
            ["issue", "view", "123"],
            ["issue", "list", "--state", "open"],
            ["search", "issues", "bug report"],
            ["label", "list"],
        ],
    )
    def test_allowed_subcommands_invoke_gh(self, fake_gh_path, args):
        result = run_gh_sh(args, fake_gh_path)
        assert result.returncode == 0


class TestIssueView:
    def test_requires_exactly_one_numeric_argument(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "abc"], fake_gh_path)
        assert result.returncode == 1
        assert "numeric issue number" in result.stderr

    def test_rejects_extra_positional_arguments(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "1", "2"], fake_gh_path)
        assert result.returncode == 1
        assert "numeric issue number" in result.stderr

    def test_accepts_numeric_issue_with_comments_flag(self, fake_gh_path):
        result = run_gh_sh(["issue", "view", "42", "--comments"], fake_gh_path)
        assert result.returncode == 0
        assert "ARGS:issue view 42 --comments" in result.stdout


class TestIssueAndLabelList:
    def test_issue_list_rejects_positional_arguments(self, fake_gh_path):
        result = run_gh_sh(["issue", "list", "open-issues"], fake_gh_path)
        assert result.returncode == 1
        assert "do not accept positional arguments" in result.stderr

    def test_label_list_rejects_positional_arguments(self, fake_gh_path):
        result = run_gh_sh(["label", "list", "bug"], fake_gh_path)
        assert result.returncode == 1
        assert "do not accept positional arguments" in result.stderr

    def test_passes_through_allowed_flags(self, fake_gh_path):
        result = run_gh_sh(["issue", "list", "--state", "open", "--limit", "20"], fake_gh_path)
        assert result.returncode == 0
        assert "ARGS:issue list --state open --limit 20" in result.stdout

    def test_flag_with_equals_syntax_does_not_consume_next_arg(self, fake_gh_path):
        result = run_gh_sh(["issue", "list", "--state=open"], fake_gh_path)
        assert result.returncode == 0
        assert "ARGS:issue list --state=open" in result.stdout


class TestSearchIssues:
    def test_passes_query_and_repo_flag(self, fake_gh_path):
        result = run_gh_sh(["search", "issues", "crash on startup"], fake_gh_path)
        assert result.returncode == 0
        assert (
            "ARGS:search issues crash on startup --repo anthropics/claude-code"
            in result.stdout
        )

    @pytest.mark.parametrize(
        "query", ["repo:foo bar", "REPO:foo bar", "org:anthropics", "user:someone"]
    )
    def test_rejects_scoping_qualifiers_case_insensitively(self, fake_gh_path, query):
        result = run_gh_sh(["search", "issues", query], fake_gh_path)
        assert result.returncode == 1
        assert "must not contain repo:, org:, or user:" in result.stderr


class TestFlagAllowlist:
    def test_disallowed_flag_is_rejected(self, fake_gh_path):
        result = run_gh_sh(["issue", "list", "--json", "title"], fake_gh_path)
        assert result.returncode == 1
        assert "only --comments, --state, --limit, --label flags are allowed" in result.stderr

    def test_allowed_flags_pass_through(self, fake_gh_path):
        result = run_gh_sh(["issue", "list", "--label", "bug"], fake_gh_path)
        assert result.returncode == 0
        assert "ARGS:issue list --label bug" in result.stdout
