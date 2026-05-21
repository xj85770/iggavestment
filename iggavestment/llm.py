"""
LLM router: Anthropic SDK when ANTHROPIC_API_KEY is set (CI/server),
falls back to claude CLI subprocess (local OAuth dev path).

Exposes the same call_claude() signature as claude_cli.py so callers
only need to change their import.
"""
from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

_BACKEND: str | None = None   # set on first call

# FIX I: cost telemetry accumulator — reset each pipeline run
_usage: dict = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def reset_usage() -> None:
    """Reset per-run token counters. Call at pipeline start."""
    _usage["input_tokens"] = 0
    _usage["output_tokens"] = 0
    _usage["calls"] = 0


def get_usage() -> dict:
    """Return accumulated token usage for the current run."""
    inp = _usage["input_tokens"]
    out = _usage["output_tokens"]
    # Haiku pricing: $0.80/M input, $4.00/M output (as of 2026-05)
    cost = round(inp * 0.00000080 + out * 0.00000400, 6)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "calls": _usage["calls"],
        "estimated_cost_usd": cost,
    }


def _detect_backend() -> str:
    global _BACKEND
    if _BACKEND:
        return _BACKEND
    if os.environ.get("ANTHROPIC_API_KEY"):
        _BACKEND = "sdk"
        log.info("llm_backend", backend="anthropic-sdk")
    else:
        _BACKEND = "cli"
        log.info("llm_backend", backend="claude-cli-subprocess")
    return _BACKEND


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
    Call Claude and return the response text.

    Routing:
    - ANTHROPIC_API_KEY set → anthropic SDK (no binary needed; works in CI)
    - else                  → claude CLI subprocess (local OAuth session)
    """
    backend = _detect_backend()

    if backend == "sdk":
        return _call_sdk(prompt, model=model, system=system,
                         json_schema=json_schema, max_tokens=max_tokens)
    else:
        from .claude_cli import call_claude as _cli_call, ClaudeCliError  # noqa: F401
        result = _cli_call(
            prompt,
            model=model,
            system=system,
            json_schema=json_schema,
            max_tokens=max_tokens,
            timeout=timeout,
            cli_path=cli_path,
        )
        # FIX I: CLI path — token counts unavailable; estimate from prompt length
        _usage["calls"] += 1
        _usage["input_tokens"]  += max(0, len(prompt) // 4)   # rough 4-char/token estimate
        _usage["output_tokens"] += max(0, len(result) // 4)
        return result


def _call_sdk(
    prompt: str,
    *,
    model: str,
    system: str | None,
    json_schema: dict | None,
    max_tokens: int,
) -> str:
    """Call Anthropic SDK directly."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic SDK not installed. Install with: uv sync --extra ci"
        ) from exc

    client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    # FIX I: accumulate token usage
    if hasattr(response, "usage") and response.usage:
        _usage["input_tokens"]  += getattr(response.usage, "input_tokens",  0)
        _usage["output_tokens"] += getattr(response.usage, "output_tokens", 0)
    _usage["calls"] += 1
    text = response.content[0].text.strip()
    if not text:
        from .claude_cli import ClaudeCliError
        raise ClaudeCliError("SDK returned empty response", returncode=0)
    return text


# Re-export ClaudeCliError so callers that do `from .llm import ClaudeCliError` work
from .claude_cli import ClaudeCliError  # noqa: E402, F401
