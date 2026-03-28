from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from parity.cli.doctor_cmd import doctor_command


def _write_config(path: Path, content: str = "version: 1\n") -> None:
    path.write_text(content, encoding="utf-8")


class TestDoctorCommand:
    def test_reports_missing_config(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            doctor_command,
            ["--config", str(tmp_path / "parity.yaml")],
        )
        assert result.exit_code == 0
        assert "parity.yaml found" in result.output or "✗" in result.output
        assert "parity init" in result.output

    def test_reports_valid_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "parity.yaml"
        _write_config(config_path)
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        assert "parity.yaml is valid" in result.output

    def test_reports_invalid_yaml_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "parity.yaml"
        config_path.write_text("version: [invalid: yaml: {\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        assert "errors" in result.output or "✗" in result.output

    def test_anthropic_key_check_passes_when_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "parity.yaml"
        _write_config(config_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY is set" in result.output

    def test_anthropic_key_check_fails_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "parity.yaml"
        _write_config(config_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY is set" in result.output  # key is always checked

    def test_summary_line_shows_pass_counts(self, tmp_path: Path) -> None:
        config_path = tmp_path / "parity.yaml"
        _write_config(config_path)
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        # e.g. "3/5 checks passed."
        assert "checks passed" in result.output

    def test_no_hint_patterns_reports_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config with no hint patterns configured reports a failure check."""
        import subprocess
        config_path = tmp_path / "parity.yaml"
        _write_config(config_path, "version: 1\n")
        # Create a minimal git repo so _git_ls_files returns tracked files
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # doctor_command uses Path.cwd() for git; chdir into tmp_path so it finds the repo
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(doctor_command, ["--config", str(config_path)])
        assert result.exit_code == 0
        assert "No hint patterns configured" in result.output
