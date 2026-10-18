"""Offline tests for the verdict logic behind `make live-examples`.

The live scripts themselves hit real providers and are deliberately not run here.
What is tested is the part that turns their results into PASS / SKIP / FAIL, since a
mistake there would silently turn a failed release check into a green one.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import coolhand
from examples import _live_check

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "live-examples.sh"


def _logging_stats(**overrides):
    stats = {
        "interaction_count": 1,
        "delivery_failure_count": 0,
        "dropped_count": 0,
        "pending_delivery": 0,
    }
    stats.update(overrides)
    return {"logging": stats}


class TestSkipUnlessEnv:
    def test_exits_with_skip_code_when_a_key_is_missing(self, monkeypatch, capsys):
        monkeypatch.setenv("COOLHAND_API_KEY", "ch-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc:
            _live_check.skip_unless_env("live_x", "COOLHAND_API_KEY", "OPENAI_API_KEY")

        assert exc.value.code == _live_check.SKIP_EXIT_CODE
        assert (
            capsys.readouterr().out.strip() == "Skipping live_x: OPENAI_API_KEY not set"
        )

    def test_empty_value_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")

        with pytest.raises(SystemExit) as exc:
            _live_check.skip_unless_env("live_x", "OPENAI_API_KEY")

        assert exc.value.code == _live_check.SKIP_EXIT_CODE

    def test_returns_when_all_keys_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        _live_check.skip_unless_env("live_x", "OPENAI_API_KEY")


class TestModelFromEnv:
    def test_default_and_override(self, monkeypatch):
        monkeypatch.delenv("X_MODEL", raising=False)
        assert _live_check.model_from_env("X_MODEL", "dflt") == "dflt"
        monkeypatch.setenv("X_MODEL", "custom")
        assert _live_check.model_from_env("X_MODEL", "dflt") == "custom"
        monkeypatch.setenv("X_MODEL", "")
        assert _live_check.model_from_env("X_MODEL", "dflt") == "dflt"


class TestConfirmDelivery:
    @pytest.fixture
    def stats(self, monkeypatch):
        """Replace the barrier and stats with controllable fakes."""
        state = {"value": _logging_stats(), "shutdown_calls": 0}

        def fake_shutdown():
            state["shutdown_calls"] += 1

        monkeypatch.setattr(coolhand, "shutdown", fake_shutdown)
        monkeypatch.setattr(coolhand, "status", lambda: state["value"])
        return state

    def test_passes_after_waiting_for_the_delivery_barrier(self, stats, capsys):
        _live_check.confirm_delivery("live_x")

        assert stats["shutdown_calls"] == 1
        assert capsys.readouterr().out.startswith("PASS live_x")

    def test_fails_clearly_when_coolhand_is_not_initialized(self, stats, capsys):
        stats["value"] = {"error": "Not initialized"}

        with pytest.raises(SystemExit) as exc:
            _live_check.confirm_delivery("live_x")

        assert exc.value.code == 1
        assert "Coolhand is not initialized" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "overrides, expected",
        [
            ({"interaction_count": 0}, "expected 1 captured interaction, got 0"),
            ({"interaction_count": 2}, "expected 1 captured interaction, got 2"),
            ({"delivery_failure_count": 1}, "delivery failed"),
            ({"dropped_count": 1}, "dropped or not delivered"),
            ({"pending_delivery": 1}, "dropped or not delivered"),
        ],
    )
    def test_fails_with_exit_1(self, stats, capsys, overrides, expected):
        stats["value"] = _logging_stats(**overrides)

        with pytest.raises(SystemExit) as exc:
            _live_check.confirm_delivery("live_x")

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("FAIL live_x")
        assert expected in err


# scripts/ isn't in the sdist, so suites run from an sdist can't exercise the runner.
requires_runner = pytest.mark.skipif(
    not RUNNER.exists(), reason="scripts/live-examples.sh not present"
)


@requires_runner
class TestRunner:
    """Drive scripts/live-examples.sh with a stub `uv` that exits per-script codes."""

    @pytest.fixture
    def run_runner(self, tmp_path):
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "uv"
        # Last argument is the script path, e.g. examples/live_openai.py
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'name="$(basename "${@: -1}" .py)"\n'
            'var="STUB_EXIT_${name}"\n'
            'if [ "${!var}" = "77" ]; then echo "Skipping ${name}: key not set"; fi\n'
            'echo "stub ran ${name}"\n'
            'exit "${!var:-0}"\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        def run(**exit_codes):
            env = {
                **os.environ,
                "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
                "COOLHAND_EXAMPLES_NO_ZSH": "1",
                "COOLHAND_EXAMPLES_LOG_DIR": str(tmp_path / "logs"),
            }
            for name, code in exit_codes.items():
                env[f"STUB_EXIT_{name}"] = str(code)
            return subprocess.run(
                ["bash", str(RUNNER)],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        return run

    def test_all_pass(self, run_runner):
        result = run_runner()

        assert result.returncode == 0
        assert "PASS  live_openai" in result.stdout
        assert "PASS  live_anthropic" in result.stdout
        assert "2 passed, 0 skipped, 0 failed" in result.stdout

    def test_skip_is_not_a_failure_but_is_reported(self, run_runner):
        result = run_runner(live_openai=77)

        assert result.returncode == 0
        assert "SKIP  live_openai  (Skipping live_openai: key not set)" in result.stdout
        assert "PASS  live_anthropic" in result.stdout

    def test_all_skipped_exits_zero_with_warning(self, run_runner):
        result = run_runner(live_openai=77, live_anthropic=77)

        assert result.returncode == 0
        assert "WARNING: every script was skipped" in result.stdout

    def test_failure_exits_nonzero_even_alongside_pass_and_skip(self, run_runner):
        result = run_runner(live_openai=1, live_anthropic=77)

        assert result.returncode == 1
        assert "FAIL  live_openai  (exit 1" in result.stdout
        assert "SKIP  live_anthropic" in result.stdout

    def test_skip_text_in_a_passing_log_does_not_make_it_a_skip(
        self, run_runner, tmp_path
    ):
        # Verdict comes from the exit status alone: a PASS stays a PASS even if its
        # log happens to contain a line starting with "Skipping".
        stub = tmp_path / "bin" / "uv"
        stub.write_text(
            '#!/usr/bin/env bash\necho "Skipping something unrelated"\nexit 0\n'
        )

        result = run_runner()

        assert result.returncode == 0
        assert "PASS  live_openai" in result.stdout
        assert "SKIP" not in result.stdout


@requires_runner
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
class TestRunnerZshReexec:
    """The `zsh -ic` re-exec makes keys exported from ~/.zshrc visible."""

    @pytest.fixture
    def run_runner(self, tmp_path):
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "uv"
        # Exits 77 (skip) unless the key from the fake .zshrc reached the environment.
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'if [ -z "$ZSHRC_ONLY_KEY" ]; then\n'
            '  echo "Skipping x: key not set"; exit 77\n'
            "fi\n"
            "exit 0\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        zdot = tmp_path / "zdot"
        zdot.mkdir()
        (zdot / ".zshrc").write_text("export ZSHRC_ONLY_KEY=from-zshrc\n")

        def run(**extra_env):
            env = {
                **os.environ,
                "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
                "ZDOTDIR": str(zdot),
                "COOLHAND_EXAMPLES_LOG_DIR": str(tmp_path / "logs"),
            }
            for var in (
                "ZSHRC_ONLY_KEY",
                "COOLHAND_EXAMPLES_NO_ZSH",
                "COOLHAND_EXAMPLES_IN_ZSH",
            ):
                env.pop(var, None)
            env.update(extra_env)
            return subprocess.run(
                ["bash", str(RUNNER)],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        return run

    def test_reexec_makes_zshrc_keys_visible(self, run_runner):
        result = run_runner()

        assert result.returncode == 0
        assert "2 passed, 0 skipped, 0 failed" in result.stdout

    def test_opt_out_skips_the_reexec(self, run_runner):
        result = run_runner(COOLHAND_EXAMPLES_NO_ZSH="1")

        assert "0 passed, 2 skipped, 0 failed" in result.stdout

    def test_guard_prevents_a_second_reexec(self, run_runner):
        # Already inside the re-exec'd copy: .zshrc must not be sourced again.
        result = run_runner(COOLHAND_EXAMPLES_IN_ZSH="1")

        assert "0 passed, 2 skipped, 0 failed" in result.stdout
