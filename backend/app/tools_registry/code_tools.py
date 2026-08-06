"""Sandboxed Python execution (env-flagged; dev-first).

`docker run --rm --network=none` with cpu/mem/time limits — the code cannot
reach the network, the host filesystem (beyond its tmp workdir), or persist.
Disabled unless CODE_INTERPRETER=true; keep it off in prod until the
launch-gate security review.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.ai.base import ToolDefinition
from app.core.config import settings
from app.core.logging import log
from app.tools_registry.dispatcher import register_tool

_TIMEOUT_S = 15
_MAX_OUTPUT = 4000
_IMAGE = "python:3.12-slim"


async def _run_python(args: dict[str, Any]) -> str:
    code = str(args.get("code", ""))
    if not code.strip():
        return "Error: 'code' is required."
    with tempfile.TemporaryDirectory() as workdir:
        Path(workdir, "main.py").write_text(code)
        process = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "--network=none",
            "--memory=256m",
            "--cpus=1",
            "--pids-limit=64",
            "--read-only",
            "--tmpfs", "/tmp:size=16m",  # noqa: S108 — inside the container
            "-v", f"{workdir}:/work:ro",
            _IMAGE, "python", "/work/main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            return f"Error: execution exceeded {_TIMEOUT_S}s."
    out = stdout.decode(errors="replace")[:_MAX_OUTPUT]
    err = stderr.decode(errors="replace")[:1000]
    log.info("code_interpreter.ran", exit_code=process.returncode)
    if process.returncode != 0:
        return f"Exit code {process.returncode}.\nstdout:\n{out}\nstderr:\n{err}"
    return out or "(no output)"


def register_code_tools() -> int:
    """Register run_python only when explicitly enabled."""
    if not settings.CODE_INTERPRETER:
        return 0
    register_tool(
        ToolDefinition(
            name="run_python",
            description=(
                "Execute a short Python (stdlib-only) script in a locked-down sandbox "
                "(no network, 15s limit) and return its stdout. Use for calculations, "
                "data transforms, and quick simulations."
            ),
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python source"}},
                "required": ["code"],
            },
        ),
        _run_python,
    )
    return 1
