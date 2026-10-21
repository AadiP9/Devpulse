"""
GitHub API tools for DevPulse.
Each function returns structured text that gets passed to Bedrock for voice-friendly summarization.
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
        raise ValueError(f"Repository must be in 'owner/repo' format, got: '{repo}'")
    return parts[0], parts[1]


async def get_ci_status(repo: str, branch: Optional[str] = None) -> str:
    owner, name = _validate_repo(repo)
    url = f"{settings.github_api_url}/repos/{owner}/{name}/actions/runs"
    params: dict = {"per_page": 10}
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()

    runs = data.get("workflow_runs", [])
    if not runs:
        return f"No workflow runs found for {repo}."

    lines = [f"CI status for {repo}" + (f" (branch: {branch})" if branch else "") + ":\n"]
    for run in runs[:5]:
        conclusion = run.get("conclusion") or "in_progress"
        started = run.get("run_started_at", "")
        elapsed = ""
        if started:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - started_dt
            mins = int(diff.total_seconds() / 60)
            elapsed = f"{mins} min ago" if mins < 60 else f"{mins // 60}h {mins % 60}m ago"

        lines.append(
            f"- [{conclusion.upper()}] {run['name']} "
            f"#{run['run_number']} on {run['head_branch']} "
            f"({elapsed}) — triggered by {run['triggering_actor']['login']}"
        )

    return "\n".join(lines)


async def get_pr_summary(repo: str, limit: int = 5) -> str:
    owner, name = _validate_repo(repo)
    url = f"{settings.github_api_url}/repos/{owner}/{name}/pulls"
    params = {
        "state": "open",
        "per_page": min(limit, 10),
        "sort": "updated",
        "direction": "desc",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        prs = r.json()

    if not prs:
        return f"No open pull requests in {repo}."

    lines = [f"{len(prs)} open pull request(s) in {repo}:\n"]
    for pr in prs:
        reviews_url = (
            f"{settings.github_api_url}/repos/{owner}/{name}/pulls/{pr['number']}/reviews"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            rv = await client.get(reviews_url, headers=_headers())
            reviews = rv.json() if rv.status_code == 200 else []

        approved = sum(1 for r in reviews if r["state"] == "APPROVED")
        changes  = sum(1 for r in reviews if r["state"] == "CHANGES_REQUESTED")
        mergeable = pr.get("mergeable_state", "unknown")

        # additions/deletions are NOT present in the PR list response —
        # only in the single-PR endpoint. Use .get() with a safe default.
        additions = pr.get("additions")
        deletions = pr.get("deletions")
        size_info = (
            f"+{additions} / -{deletions} lines"
            if additions is not None
            else "diff size not available"
        )

        lines.append(
            f"- PR #{pr['number']}: \"{pr['title']}\" by {pr['user']['login']}\n"
            f"  {approved} approval(s), {changes} change request(s), "
            f"mergeable: {mergeable}, {size_info}"
        )

    return "\n".join(lines)


async def get_test_results(repo: str, workflow: Optional[str] = None) -> str:
    owner, name = _validate_repo(repo)

    runs_url = f"{settings.github_api_url}/repos/{owner}/{name}/actions/runs"
    params: dict = {"per_page": 5}
    if workflow:
        params["workflow_id"] = workflow

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(runs_url, headers=_headers(), params=params)
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])

    if not runs:
        return f"No workflow runs found for {repo}."

    run = runs[0]
    run_id    = run["id"]
    run_name  = run["name"]
    conclusion = run.get("conclusion") or "in_progress"
    started    = run.get("run_started_at", "")

    elapsed = ""
    if started:
        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        diff = datetime.now(timezone.utc) - started_dt
        mins = int(diff.total_seconds() / 60)
        elapsed = f"{mins} minutes ago"

    jobs_url = (
        f"{settings.github_api_url}/repos/{owner}/{name}/actions/runs/{run_id}/jobs"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        jr = await client.get(jobs_url, headers=_headers())
        jr.raise_for_status()
        jobs = jr.json().get("jobs", [])

    passed  = [j for j in jobs if j.get("conclusion") == "success"]
    failed  = [j for j in jobs if j.get("conclusion") == "failure"]
    skipped = [j for j in jobs if j.get("conclusion") == "skipped"]

    lines = [
        f"Test run: {run_name} #{run['run_number']} on {run['head_branch']} "
        f"— {conclusion.upper()} ({elapsed})",
        f"Jobs: {len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped",
    ]

    if failed:
        lines.append("Failed jobs:")
        for j in failed:
            lines.append(f"  - {j['name']}")
            failed_steps = [
                s for s in j.get("steps", []) if s.get("conclusion") == "failure"
            ]
            for step in failed_steps:
                lines.append(f"    Step failed: {step['name']}")

    lines.append(
        f"Commit: {run['head_sha'][:8]} — {run['head_commit']['message'][:80]}"
    )
    return "\n".join(lines)


async def get_repo_activity(repo: str, days: int = 7) -> str:
    owner, name = _validate_repo(repo)
    from datetime import timedelta

    since     = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since.isoformat().replace("+00:00", "Z")

    async with httpx.AsyncClient(timeout=15) as client:
        commits_r = await client.get(
            f"{settings.github_api_url}/repos/{owner}/{name}/commits",
            headers=_headers(),
            params={"since": since_iso, "per_page": 30},
        )
        issues_r = await client.get(
            f"{settings.github_api_url}/repos/{owner}/{name}/issues",
            headers=_headers(),
            params={"since": since_iso, "state": "all", "per_page": 30},
        )

    commits = commits_r.json() if commits_r.status_code == 200 else []
    issues  = issues_r.json()  if issues_r.status_code  == 200 else []

    real_issues = [i for i in issues if "pull_request" not in i]
    prs         = [i for i in issues if "pull_request" in i]

    lines = [
        f"Repository activity for {repo} — last {days} days:\n",
        f"Commits: {len(commits)}",
    ]

    if commits:
        authors = list({c["commit"]["author"]["name"] for c in commits})
        lines.append(
            f"Contributors: {', '.join(authors[:5])}"
            + (" and more" if len(authors) > 5 else "")
        )
        lines.append("Recent commits:")
        for c in commits[:5]:
            msg = c["commit"]["message"].split("\n")[0][:72]
            lines.append(f"  - {msg} ({c['commit']['author']['name']})")

    lines.append(f"\nIssues opened: {sum(1 for i in real_issues if i['state'] == 'open')}")
    lines.append(f"Issues closed: {sum(1 for i in real_issues if i['state'] == 'closed')}")
    lines.append(f"Pull requests: {len(prs)}")

    return "\n".join(lines)