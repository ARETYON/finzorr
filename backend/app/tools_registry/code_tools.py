"""Sandboxed Python execution (env-flagged; dev-first).

`docker run --rm --network=none` as an unprivileged user with all capabilities
dropped, plus cpu/mem/pids/time limits — the code cannot reach the network,
the host filesystem (beyond its read-only workdir), escalate privileges, or
persist. The container is named so a timeout can `docker kill` it (killing
only the client process would leave the container running). Disabled unless
CODE_INTERPRETER=true; keep it off in prod until the launch-gate security
review.
"""

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.ai.base import ToolDefinition
from app.core.config import settings
from app.core.logging import log
from app.tools_registry.dispatcher import register_tool

_TIMEOUT_S = 15
_PULL_TIMEOUT_S = 120
_MAX_OUTPUT = 4000
_IMAGE = "python:3.12-slim"


async def _ensure_image() -> str | None:
    """Pull the sandbox image outside the execution budget; None if ready."""
    inspect = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", _IMAGE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    if await inspect.wait() == 0:
        return None
    log.info("code_interpreter.pulling_image", image=_IMAGE)
    pull = await asyncio.create_subprocess_exec(
        "docker", "pull", _IMAGE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        if await asyncio.wait_for(pull.wait(), timeout=_PULL_TIMEOUT_S) != 0:
            return f"Error: could not pull sandbox image {_IMAGE}."
    except TimeoutError:
        pull.kill()
        await pull.wait()
        return "Error: sandbox image pull timed out — try again shortly."
    return None


async def _kill_container(name: str) -> None:
    killer = await asyncio.create_subprocess_exec(
        "docker", "kill", name,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await killer.wait()


async def _run_python(args: dict[str, Any]) -> str:
    code = str(args.get("code", ""))
    if not code.strip():
        return "Error: 'code' is required."
    pull_error = await _ensure_image()
    if pull_error:
        return pull_error
    container = f"finzorr-sandbox-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory() as workdir:
        script = Path(workdir, "main.py")
        script.write_text(code)
        # The container runs as nobody (65534) — the bind mount must be
        # world-readable or the sandboxed process can't open its own script.
        Path(workdir).chmod(0o755)
        script.chmod(0o644)
        process = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "--name", container,
            "--network=none",
            "--memory=256m",
            "--cpus=1",
            "--pids-limit=64",
            "--read-only",
            "--user", "65534:65534",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:size=16m",  # noqa: S108 — inside the container
            "-v", f"{workdir}:/work:ro",
            _IMAGE, "python", "/work/main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_TIMEOUT_S)
        except TimeoutError:
            await _kill_container(container)
            process.kill()
            await process.wait()
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
        # Cap, not a wait: normal runs finish in seconds. The budget covers a
        # one-time image pull plus container start/teardown margin.
        timeout_s=_TIMEOUT_S + _PULL_TIMEOUT_S + 10.0,
    )
    return 1
