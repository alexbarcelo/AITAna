"""Unit tests for grading/sandbox.py -- all against a faked `deno` subprocess,
never a real one. See sandbox.py's module docstring for how this was
verified manually against a real Deno + Pyodide instead."""

import json
import subprocess

import pytest

from aitana.grading import sandbox


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["deno"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_python_sandbox_available_reflects_which(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")
    assert sandbox.python_sandbox_available() is True

    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    assert sandbox.python_sandbox_available() is False


def test_get_python_sandbox_tool_construction_never_checks_for_deno(monkeypatch):
    """Constructing the tool must stay side-effect-free (see its docstring)
    -- assert this by making shutil.which explode if it's ever called."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: (_ for _ in ()).throw(AssertionError("should not check")))
    tool = sandbox.get_python_sandbox_tool()
    assert tool.name == "python_sandbox"


def test_run_raises_clear_error_when_deno_missing(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    tool = sandbox.PythonSandboxTool()

    with pytest.raises(RuntimeError, match="Deno is not installed"):
        tool._run("print(1)")


def test_run_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")
    envelope = json.dumps({"success": True, "stdout": "42", "stderr": None, "result": None})
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **kw: _fake_completed(stdout=envelope))

    tool = sandbox.PythonSandboxTool()
    result = tool._run("print(6 * 7)")

    assert result == "42"


def test_run_surfaces_execution_errors(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")
    envelope = json.dumps({"success": False, "stdout": None, "stderr": "ValueError: boom", "result": None})
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **kw: _fake_completed(stdout=envelope))

    tool = sandbox.PythonSandboxTool()
    result = tool._run("raise ValueError('boom')")

    assert "Execution failed" in result
    assert "boom" in result


def test_run_reports_when_deno_itself_fails_before_producing_json(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")
    monkeypatch.setattr(
        sandbox.subprocess, "run", lambda *a, **kw: _fake_completed(stdout="", stderr="network error", returncode=1)
    )

    tool = sandbox.PythonSandboxTool()
    result = tool._run("print(1)")

    assert "Sandbox failed to run" in result
    assert "network error" in result


def test_run_reports_timeout(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")

    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="deno", timeout=1)

    monkeypatch.setattr(sandbox.subprocess, "run", _raise_timeout)

    tool = sandbox.PythonSandboxTool(timeout_seconds=1)
    result = tool._run("while True: pass")

    assert "timed out" in result


def test_run_command_includes_scoped_net_permission(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/deno")
    captured = {}

    def _capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout=json.dumps({"success": True, "stdout": "ok"}))

    monkeypatch.setattr(sandbox.subprocess, "run", _capture_run)

    sandbox.PythonSandboxTool()._run("print('ok')")

    cmd = captured["cmd"]
    assert cmd[0] == "deno"
    assert f"--allow-net={','.join(sandbox.ALLOWED_NET_HOSTS)}" in cmd
    assert sandbox.PYODIDE_SANDBOX_PKG in cmd
    # No blanket filesystem/subprocess/FFI/unrestricted-net access.
    assert not any(flag.startswith("--allow-run") for flag in cmd)
    assert not any(flag.startswith("--allow-ffi") for flag in cmd)
    assert "--allow-net" in " ".join(cmd) and "--allow-net=true" not in cmd
