"""
Thin wrapper: call the local `claude` CLI via subprocess.
Provides a pure LLM call without requiring ANTHROPIC_API_KEY
(uses the pre-authed OAuth session on the machine).
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

CLAUDE_BIN = "claude"          # overridden by config.CLAUDE_CLI_PATH
TIMEOUT_SEC = 60               # overridden by config.CLAUDE_CLI_TIMEOUT_SEC


class ClaudeCliError(RuntimeError):
    def __init__(self, msg: str, returncode: int = -1, stderr: str = ""):
        super().__init__(msg)
        self.returncode = returncode
        self.stderr = stderr


def call_claude(
    prompt: str,
    *,
    model: str = "claude-haiku-4-5",
    system: str | None = None,
    json_schema: dict | None = None,
    max_tokens: int = 2048,
    timeout: int | None = None,
    cli_path: str | None = None,
) -> str:
    """
    Call the claude CLI non-interactively and return the text response.

    Parameters
    ----------
    prompt        : user-turn text
    model         : Claude model identifier
    system        : optional system prompt (appended to default, not replaced)
    json_schema   : if set, pass --json-schema to enforce structured output
    max_tokens    : ceiling via --max-budget-usd (approximate; CLI controls actual)
    timeout       : seconds before SIGKILL; defaults to TIMEOUT_SEC
    cli_path      : path to claude binary; defaults to CLAUDE_BIN

    Returns
    -------
    str  response text stripped of leading/trailing whitespace

    Raises
    ------
    ClaudeCliError  on non-zero exit or empty output
    """
    binary = cli_path or CLAUDE_BIN
    sec = timeout or TIMEOUT_SEC
    call_id = uuid.uuid4().hex[:8]

    cmd = [
        binary,
        "-p",                       # non-interactive print mode
        "--model", model,
        "--max-budget-usd", "0.20", # hard cost ceiling per call
        "--no-session-persistence", # don't pollute session history
        "--tools", "",              # disable all built-in tools — pure LLM
        "--output-format", "text",
    ]

    if system:
        cmd += ["--append-system-prompt", system]

    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema)]

    cmd.append(prompt)

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(
            f"claude CLI timed out after {sec}s",
            returncode=-1,
            stderr="",
        ) from exc

    duration_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "claude_cli_call",
        call_id=call_id,
        model=model,
        duration_ms=duration_ms,
        exit_code=result.returncode,
        stderr_snippet=result.stderr[:120] if result.stderr else "",
    )

    if result.returncode != 0:
        raise ClaudeCliError(
            f"claude CLI exited {result.returncode}: {result.stderr[:300]}",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    text = result.stdout.strip()
    if not text:
        raise ClaudeCliError(
            "claude CLI returned empty output",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    return text


def find_claude_cli(path: str = CLAUDE_BIN) -> str:
    """
    Verify the claude binary is reachable. Returns the resolved path or
    raises ClaudeCliError if not found.
    """
    import shutil
    resolved = shutil.which(path)
    if resolved:
        return resolved
    # Try absolute default
    default = Path("/Users/kingme/.local/bin/claude")
    if default.exists():
        return str(default)
    raise ClaudeCliError(
        f"claude CLI not found at '{path}' or {default}. "
        "Install with: npm install -g @anthropic-ai/claude-code"
    )
