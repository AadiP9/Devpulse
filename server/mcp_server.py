"""
DevPulse MCP Server — Protocol Layer
Implements MCP spec 2025-11-25 Streamable HTTP transport.
"""

import logging
from typing import Any

from tools import (
    get_ci_status,
    get_pr_summary,
    get_test_results,
    get_repo_activity,
    summarize_with_bedrock,
    trace_breakage,
)

logger = logging.getLogger("devpulse.mcp")

MCP_VERSION = "2025-11-25"

SERVER_INFO = {
    "name": "devpulse",
    "version": "1.0.0",
    "description": "Developer workflow assistant — query your CI, PRs, and test results via Alexa+",
}

CAPABILITIES = {"tools": {}}

TOOLS: list[dict] = [
    {
        "name": "get_test_results",
        "description": (
            "Fetch the latest test run results from a GitHub Actions workflow. "
            "Returns pass/fail counts, the names of failed jobs and steps, "
            "and an AI-generated voice-ready summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub repository in owner/repo format, e.g. 'acme/backend'"
                },
                "workflow": {
                    "type": "string",
                    "description": "Optional workflow file name, e.g. 'test.yml'. Defaults to the most recent run."
                }
            },
            "required": ["repo"]
        }
    },
    {
        "name": "get_ci_status",
        "description": (
            "Get the current CI pipeline status for a GitHub repository. "
            "Returns the 5 most recent workflow runs with conclusions, branch names, and elapsed time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "GitHub repository in owner/repo format"},
                "branch": {"type": "string", "description": "Filter by branch. Defaults to all branches."}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "get_pr_summary",
        "description": (
            "List open pull requests with review status, line change counts, and merge readiness."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "GitHub repository in owner/repo format"},
                "limit": {
                    "type": "integer",
                    "description": "Max PRs to return. Defaults to 5.",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10
                }
            },
            "required": ["repo"]
        }
    },
    {
        "name": "get_repo_activity",
        "description": (
            "Summarize commit and issue activity over the past N days. "
            "Includes commit count, contributors, and issue open/close counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "GitHub repository in owner/repo format"},
                "days": {
                    "type": "integer",
                    "description": "Days of history to include. Defaults to 7.",
                    "default": 7,
                    "minimum": 1,
                    "maximum": 90
                }
            },
            "required": ["repo"]
        }
    },
    {
        "name": "trace_breakage",
        "description": (
            "Find the specific commit that broke CI and explain why it is the likely cause. "
            "Compares the last passing run to the first failing run, lists the commits that "
            "landed between them, cross-references changed files against what failed, "
            "and returns an AI-reasoned explanation of who broke main and why."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub repository in owner/repo format"
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to inspect. Defaults to all branches."
                },
                "workflow": {
                    "type": "string",
                    "description": "Optional workflow file name, e.g. 'test.yml'."
                }
            },
            "required": ["repo"]
        }
    }
]

_sessions: dict[str, dict] = {}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _ok(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _err(id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def _needs_ai(raw: str) -> bool:
    """
    Returns False when the raw text is already a short, complete, voice-ready
    statement that does not benefit from AI summarization.

    Cases that do NOT need AI:
      - No CI runs exist for the repo
      - No open PRs
      - CI is currently passing (already concise)
      - Any other short informational message under 80 chars
    """
    short_circuit_prefixes = (
        "no workflow runs found",
        "no open pull requests",
        "ci is currently passing",
        "no github actions",
        "ci on ",          # trace_breakage "CI is broken across all runs" messages
        "no commits",
    )
    lower = raw.strip().lower()
    if any(lower.startswith(p) for p in short_circuit_prefixes):
        return False
    if len(raw.strip()) < 80:
        return False
    return True


# ─────────────────────────────────────────────
# Tool dispatch
# ─────────────────────────────────────────────

async def _call_tool(name: str, arguments: dict) -> str:
    if name == "get_test_results":
        raw = await get_test_results(arguments["repo"], arguments.get("workflow"))
        if not _needs_ai(raw):
            return raw
        return await summarize_with_bedrock(
            f"Summarize these GitHub Actions test results for Alexa to read aloud:\n\n{raw}"
        )

    if name == "get_ci_status":
        raw = await get_ci_status(arguments["repo"], arguments.get("branch"))
        if not _needs_ai(raw):
            return raw
        return await summarize_with_bedrock(
            f"Summarize this CI pipeline status for Alexa to read aloud:\n\n{raw}"
        )

    if name == "get_pr_summary":
        raw = await get_pr_summary(arguments["repo"], arguments.get("limit", 5))
        if not _needs_ai(raw):
            return raw
        return await summarize_with_bedrock(
            f"Summarize these pull requests for a developer listening through Alexa:\n\n{raw}"
        )

    if name == "get_repo_activity":
        days = arguments.get("days", 7)
        raw = await get_repo_activity(arguments["repo"], days)
        if not _needs_ai(raw):
            return raw
        return await summarize_with_bedrock(
            f"Summarize this repository activity from the last {days} days for Alexa:\n\n{raw}"
        )

    if name == "trace_breakage":
        evidence = await trace_breakage(
            repo=arguments["repo"],
            branch=arguments.get("branch"),
            workflow=arguments.get("workflow"),
        )
        if not _needs_ai(evidence):
            return evidence
        return await summarize_with_bedrock(evidence)

    raise ValueError(f"Unknown tool: {name!r}")


# ─────────────────────────────────────────────
# MCP dispatch
# ─────────────────────────────────────────────

async def dispatch(message: dict, session_id: str) -> dict | None:
    method: str = message.get("method", "")
    params: dict = message.get("params") or {}
    msg_id: Any = message.get("id")

    if msg_id is None:
        if method == "notifications/initialized":
            logger.debug("Client initialized in session %s", session_id)
        return None

    if method == "initialize":
        _sessions[session_id] = {
            "clientInfo": params.get("clientInfo", {}),
            "protocolVersion": params.get("protocolVersion", MCP_VERSION),
        }
        logger.info(
            "Session %s initialized — client: %s",
            session_id,
            params.get("clientInfo", {}).get("name", "unknown"),
        )
        return _ok(msg_id, {
            "protocolVersion": MCP_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        })

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name: str = params.get("name", "")
        tool_args: dict = params.get("arguments") or {}

        if not tool_name:
            return _err(msg_id, -32602, "Missing required parameter: name")

        try:
            result_text = await _call_tool(tool_name, tool_args)
            return _ok(msg_id, {
                "content": [{"type": "text", "text": result_text}],
                "isError": False,
            })
        except ValueError as e:
            return _ok(msg_id, {
                "content": [{"type": "text", "text": str(e)}],
                "isError": True,
            })
        except Exception as e:
            logger.exception("Tool %r raised an unexpected error", tool_name)
            return _ok(msg_id, {
                "content": [{"type": "text", "text": f"Tool error: {str(e)}"}],
                "isError": True,
            })

    return _err(msg_id, -32601, f"Method not found: {method!r}")


async def dispatch_batch(messages: list[dict], session_id: str) -> list[dict]:
    results = []
    for message in messages:
        result = await dispatch(message, session_id)
        if result is not None:
            results.append(result)
    return results