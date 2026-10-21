"""
trace_breakage — DevPulse's signature tool.

Answers the question every engineer asks when CI goes red: "Who broke main and why?"
"""

import httpx
from datetime import datetime, timezone
from typing import Optional

from config import settings


def _headers() -> dict:
    """Build GitHub API headers. Authorization is only added when a token is set."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _validate_repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Repository must be 'owner/repo', got: '{repo}'")
    return parts[0], parts[1]


def _elapsed(iso_str: str) -> str:
    if not iso_str:
        return "unknown time ago"
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    diff = datetime.now(timezone.utc) - dt
    mins = int(diff.total_seconds() / 60)
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


async def trace_breakage(
    repo: str,
    branch: Optional[str] = None,
    workflow: Optional[str] = None,
) -> str:
    owner, name = _validate_repo(repo)

    # ── Step 1: Fetch the 20 most recent runs ──────────────────────────────
    params: dict = {"per_page": 20}
    if branch:
        params["branch"] = branch
    if workflow:
        params["workflow_id"] = workflow

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{settings.github_api_url}/repos/{owner}/{name}/actions/runs",
            headers=_headers(),
            params=params,
        )
        r.raise_for_status()

    runs = r.json().get("workflow_runs", [])
    if not runs:
        return f"No workflow runs found for {repo}."

    latest = runs[0]
    latest_conclusion = latest.get("conclusion")
    if latest_conclusion == "success":
        return (
            f"CI is currently passing on {repo} "
            f"(branch: {latest['head_branch']}, "
            f"run #{latest['run_number']}, "
            f"last ran {_elapsed(latest.get('run_started_at', ''))})."
        )

    # ── Step 2: Find the success → failure transition ──────────────────────
    first_failing_run = None
    last_passing_run = None

    for run in runs:
        conclusion = run.get("conclusion")
        if conclusion in ("failure", "timed_out", "startup_failure"):
            first_failing_run = run
        elif conclusion == "success" and first_failing_run is not None:
            last_passing_run = run
            break

    if first_failing_run is None:
        return (
            f"CI on {repo} is broken, but all recent runs have failed. "
            "I can't find a passing baseline to compare against. "
            "The breakage may predate the last 20 runs."
        )

    if last_passing_run is None:
        return (
            f"CI on {repo} has been failing across all recent runs. "
            f"The earliest visible failure was run #{runs[-1]['run_number']} "
            f"on {runs[-1]['head_branch']}. "
            "Fetch more run history to find the exact breakage point."
        )

    failing_sha = first_failing_run["head_sha"]
    passing_sha = last_passing_run["head_sha"]
    failed_at   = _elapsed(first_failing_run.get("run_started_at", ""))

    # ── Step 3: Get failed job names ──────────────────────────────────────
    async with httpx.AsyncClient(timeout=15) as client:
        jr = await client.get(
            f"{settings.github_api_url}/repos/{owner}/{name}/actions/runs/{first_failing_run['id']}/jobs",
            headers=_headers(),
        )
        jobs = jr.json().get("jobs", []) if jr.status_code == 200 else []

    failed_jobs  = [j["name"] for j in jobs if j.get("conclusion") in ("failure", "timed_out")]
    failed_steps = []
    for job in jobs:
        if job.get("conclusion") in ("failure", "timed_out"):
            for step in job.get("steps", []):
                if step.get("conclusion") in ("failure", "timed_out"):
                    failed_steps.append(f"{job['name']} / {step['name']}")

    # ── Step 4: Compare commits between the two SHAs ─────────────────────
    async with httpx.AsyncClient(timeout=15) as client:
        compare_r = await client.get(
            f"{settings.github_api_url}/repos/{owner}/{name}/compare/{passing_sha}...{failing_sha}",
            headers=_headers(),
        )

    if compare_r.status_code != 200:
        return (
            f"CI on {repo} broke {failed_at} (run #{first_failing_run['run_number']}). "
            f"Failed: {', '.join(failed_jobs) or 'unknown jobs'}. "
            "Could not retrieve commit comparison."
        )

    compare        = compare_r.json()
    commits_between = compare.get("commits", [])
    changed_files   = compare.get("files", [])

    # ── Step 5: Build evidence report for Bedrock ─────────────────────────
    lines = [
        f"BREAKAGE TRACE for {repo}",
        f"Branch: {first_failing_run['head_branch']}",
        f"CI went red: {failed_at} (run #{first_failing_run['run_number']})",
        f"Last green run: #{last_passing_run['run_number']} ({_elapsed(last_passing_run.get('run_started_at', ''))})",
        "",
        "WHAT FAILED:",
        *[f"  - {j}" for j in (failed_jobs or ["Unknown — check logs"])],
    ]

    if failed_steps:
        lines.append("FAILED STEPS:")
        lines.extend(f"  - {s}" for s in failed_steps[:6])

    lines.append(f"\nCOMMITS LANDED BETWEEN LAST GREEN AND FIRST RED ({len(commits_between)} total):")
    for c in commits_between:
        commit_data = c.get("commit", {})
        author      = commit_data.get("author", {}).get("name", "unknown")
        message     = commit_data.get("message", "").split("\n")[0][:80]
        sha_short   = c.get("sha", "")[:8]
        lines.append(f"  [{sha_short}] {author}: {message}")

    if changed_files:
        lines.append(f"\nFILES CHANGED ({len(changed_files)} total):")
        schema_files = [f["filename"] for f in changed_files if any(k in f["filename"].lower() for k in ("migrat", "schema", "model", "db"))]
        test_files   = [f["filename"] for f in changed_files if any(k in f["filename"].lower() for k in ("test", "spec", "fixture"))]
        config_files = [f["filename"] for f in changed_files if any(k in f["filename"].lower() for k in ("config", ".env", "settings", "yaml", "yml", ".toml"))]
        other_files  = [f["filename"] for f in changed_files if f["filename"] not in schema_files + test_files + config_files]

        if schema_files: lines.append(f"  Schema/DB:  {', '.join(schema_files[:4])}")
        if config_files: lines.append(f"  Config:     {', '.join(config_files[:4])}")
        if test_files:   lines.append(f"  Tests:      {', '.join(test_files[:4])}")
        if other_files:  lines.append(f"  Other:      {', '.join(other_files[:4])}")

    lines.append(
        "\nTASK: Based on what failed and what changed, identify the single most likely "
        "culprit commit and explain the connection between the changed files and the "
        "failure. Speak as if responding to a developer through Alexa+ — be direct, "
        "specific, and under 80 words. Start with who and what, not with 'Based on'."
    )

    return "\n".join(lines)