"""A sandboxed Python code-execution tool for the grading LLM.

Only questions with `Question.needs_python_sandbox=True` get this tool bound
in (see `grading.py`'s `grade_answer`) -- most rubric questions never touch
this module at all.

**Deliberately not the `langchain-sandbox` PyPI package.** That package
(`langchain_sandbox.PyodideSandboxTool`, last released as 0.0.6) pins
`langchain-core<0.4.0`, which conflicts outright with this project's
`langchain>=1.3.14` (which requires `langchain-core>=1.6.0`) -- installing it
would downgrade the whole langchain/langgraph stack and break
`create_agent` (langchain 1.x only). What it actually *does*, though, is
thin: spawn `deno run <permissions> jsr:@langchain/pyodide-sandbox@0.0.4 -c
<code>` and parse a JSON envelope off stdout. That JSR package is Deno's
sandboxed Python runtime (Pyodide, i.e. CPython compiled to WASM) -- the
real security boundary -- and is independent of the Python-side wrapper
version. This module re-implements just the (stateless, sync) slice of that
wrapper directly against this project's own langchain-core, so the two
version ranges never have to coexist. If `langchain-sandbox` ever relaxes
its `langchain-core` pin, revisit whether this module is still worth
carrying separately.

Verified manually (see AGENTS.md) with a real `deno` binary: a stateless
call needs `--allow-net=cdn.jsdelivr.net` even for something as trivial as
`print(1+1)` -- Pyodide always bootstraps `micropip`/`packaging` from
jsdelivr on a cold cache, and lazily fetches any other imported package
(numpy, pandas, ...) the same way, auto-detected from the code's `import`
statements. Nothing else needs network, filesystem, env, subprocess, or FFI
access, so every other Deno permission stays off.
"""

import json
import logging
import shutil
import subprocess
import time
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# The JSR package langchain-sandbox itself wraps -- see module docstring.
# Pinned, like langchain-sandbox pins it, so a new release can't silently
# change sandboxed behavior underneath a running deployment.
PYODIDE_SANDBOX_PKG = "jsr:@langchain/pyodide-sandbox@0.0.4"

# The only host Pyodide's package loader needs to bootstrap itself and fetch
# any auto-detected import (numpy, pandas, ...) -- verified manually, see
# module docstring. Deliberately not `allow_net=True`: the grading LLM only
# ever gets this tool for questions a rubric author opted into, but the code
# it runs is still driven by a student's submitted answer, so keep network
# access scoped to exactly what Pyodide itself needs rather than opening it
# to arbitrary outbound requests.
ALLOWED_NET_HOSTS = ["cdn.jsdelivr.net"]

DEFAULT_TIMEOUT_SECONDS = 60.0


def python_sandbox_available() -> bool:
    """Whether a `deno` binary is on PATH. Doesn't check it actually runs --
    `PythonSandboxTool._run` surfaces that failure with a clear message at
    grading time instead, so a rubric with no python-sandbox questions never
    pays for this check at all."""
    return shutil.which("deno") is not None


class _PythonSandboxToolInput(BaseModel):
    code: str = Field(description="Python code to execute. Use print(...) to produce output.")


class PythonSandboxTool(BaseTool):
    """Runs Python code in a Deno-sandboxed Pyodide runtime (see module
    docstring). Stateless: each call is an independent interpreter with no
    variables carried over from a previous call in the same grading run --
    grading is a single question at a time, so there's no multi-turn session
    worth the added complexity of `langchain-sandbox`'s stateful mode (which
    needs a LangGraph checkpointer + injected state, see its docstring).
    """

    name: str = "python_sandbox"
    description: str = (
        "Run Python code in a sandbox to check the student's work -- e.g. "
        "execute a snippet they wrote, verify a claimed command's output, "
        "or compute something the answer asserts. Input must be valid "
        "Python; print(...) whatever you need to see, since only stdout is "
        "returned. Common packages (numpy, pandas, ...) are available and "
        "auto-installed on import. This tool is optional: use it only when "
        "running code would actually help you judge the answer."
    )
    args_schema: type[BaseModel] = _PythonSandboxToolInput
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def _run(self, code: str, **kwargs: Any) -> str:  # noqa: ARG002 -- run_manager etc. unused
        if not python_sandbox_available():
            raise RuntimeError(
                "This question needs the Python sandbox tool, but Deno is not installed "
                "(or not on PATH) in this environment. Install it from https://deno.land, "
                "or see AGENTS.md's 'Python sandbox' section."
            )

        cmd = [
            "deno",
            "run",
            "--allow-env",
            "--allow-read=node_modules",
            "--allow-write=node_modules",
            f"--allow-net={','.join(ALLOWED_NET_HOSTS)}",
            "--node-modules-dir=auto",
            PYODIDE_SANDBOX_PKG,
            "-c",
            code,
        ]

        logger.info("Running python sandbox (%d char(s) of code)...", len(code))
        logger.debug("Code:\n%s", code)
        start = time.perf_counter()
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, `code` passed as a single arg
                cmd, capture_output=True, text=True, timeout=self.timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired:
            logger.warning("Python sandbox timed out after %.0fs", self.timeout_seconds)
            return f"Execution timed out after {self.timeout_seconds:.0f} seconds."
        elapsed = time.perf_counter() - start

        if not proc.stdout:
            # The JSON envelope below comes from the sandboxed script itself;
            # nothing on stdout means Deno (or the JSR fetch) failed before
            # that script ever ran -- stderr is the only useful signal then.
            logger.warning("Python sandbox produced no output in %.2fs: %s", elapsed, proc.stderr)
            return f"Sandbox failed to run: {proc.stderr.strip() or '(no output)'}"

        envelope = json.loads(proc.stdout)
        stdout = envelope.get("stdout") or ""
        stderr = envelope.get("stderr")
        success = bool(envelope.get("success"))
        logger.info("Python sandbox finished in %.2fs (success=%s)", elapsed, success)
        logger.debug("Python sandbox stdout:\n%s", stdout)

        if not success or stderr:
            logger.debug("Python sandbox stderr:\n%s", stderr)
            return f"Execution failed:\n{stderr or stdout}"

        return stdout or "(no output -- did you forget to print()?)"


def get_python_sandbox_tool() -> PythonSandboxTool:
    """Cheap, side-effect-free construction -- the Deno check happens lazily
    inside `_run`, not here, so building the tool (e.g. to hand to an agent)
    never requires Deno to be installed unless the LLM actually calls it."""
    return PythonSandboxTool()
