#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


SEPARATOR = "====================="
DEFAULT_HUB_URL = "https://work-report-hub-production.up.railway.app"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append a completed-task bullet to a daily repo work report."
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Short completed-task summary to append as a bullet.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root or any path inside the repo. Defaults to the current directory.",
    )
    parser.add_argument(
        "--date",
        help="Override report date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--print-path",
        action="store_true",
        help="Print only the resolved report file path.",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("WORK_REPORT_HUB_URL", DEFAULT_HUB_URL),
        help="Optional report hub base URL. Defaults to WORK_REPORT_HUB_URL or the production hub domain.",
    )
    parser.add_argument(
        "--hub-api-key",
        default=os.environ.get("WORK_REPORT_HUB_API_KEY", ""),
        help="Optional report hub API key. Defaults to WORK_REPORT_HUB_API_KEY.",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("WORK_REPORT_HUB_SOURCE", "work-report-updater"),
        help="Optional source label for report hub uploads.",
    )
    parser.add_argument(
        "--skip-hub-upload",
        action="store_true",
        help="Skip uploading to the report hub even if the API key is configured.",
    )
    return parser.parse_args()


def resolve_repo_root(start_path: Path) -> Path:
    candidate = start_path.resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for path in [candidate, *candidate.parents]:
        if (path / ".git").exists():
            return path

    return candidate


def parse_report_date(raw_date: str | None) -> dt.date:
    if not raw_date:
        return dt.date.today()
    return dt.datetime.strptime(raw_date, "%Y-%m-%d").date()


def format_filename_date(report_date: dt.date) -> str:
    return f"{report_date.strftime('%b').lower()}-{report_date.day}-{report_date.year}"


def format_header_date(report_date: dt.date) -> str:
    return f"{report_date.strftime('%b')} {report_date.day}, {report_date.year}"


def slugify_repo_name(repo_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", repo_name.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "repo"


def build_initial_report(report_date: dt.date, repo_name: str, task: str) -> str:
    return (
        f"DATE  : {format_header_date(report_date)}\n"
        f"REPO NAME : {repo_name}\n\n"
        f"- {task}\n\n"
        f"{SEPARATOR}\n"
    )


def append_task(existing_text: str, task: str) -> str:
    lines = existing_text.splitlines()

    separator_index = None
    for index, line in enumerate(lines):
        if line.strip() == SEPARATOR:
            separator_index = index
            break

    bullet_line = f"- {task}"

    if separator_index is None:
        trimmed = existing_text.rstrip()
        if trimmed:
            return f"{trimmed}\n{bullet_line}\n\n{SEPARATOR}\n"
        return f"{bullet_line}\n\n{SEPARATOR}\n"

    while separator_index > 0 and lines[separator_index - 1].strip() == "":
        lines.pop(separator_index - 1)
        separator_index -= 1

    lines.insert(separator_index, bullet_line)
    lines.insert(separator_index + 1, "")
    return "\n".join(lines).rstrip() + "\n"


def resolve_repo_name_for_hub(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return repo_root.name

    remote_url = result.stdout.strip()
    if result.returncode != 0 or not remote_url:
        return repo_root.name

    github_match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote_url)
    if github_match:
        return github_match.group(1)

    return remote_url


def upload_report_to_hub(
    hub_url: str,
    api_key: str,
    project_name: str,
    repo_name: str,
    task: str,
    report_date: dt.date,
    report_text: str,
    source: str,
) -> None:
    payload = {
        "project_name": project_name,
        "repo_name": repo_name,
        "title": task,
        "detail": report_text,
        "report_date": report_date.isoformat(),
        "source": source,
    }
    request = urllib.request.Request(
        url=hub_url.rstrip("/") + "/api/reports",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            if response.status >= 300:
                raise RuntimeError(f"Report hub upload failed with status {response.status}.")
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Report hub upload failed: {exc.code} {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Report hub upload failed: {exc}") from exc


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root(Path(args.repo_root))
    report_date = parse_report_date(args.date)
    repo_name = repo_root.name
    report_filename = (
        f"work-report-{format_filename_date(report_date)}-"
        f"{slugify_repo_name(repo_name)}.md"
    )
    report_path = repo_root / report_filename
    task = " ".join(args.task.strip().split())

    if not task:
        raise ValueError("--task must not be blank.")

    if report_path.exists():
        updated_text = append_task(report_path.read_text(encoding="utf-8"), task)
    else:
        updated_text = build_initial_report(report_date, repo_name, task)

    report_path.write_text(updated_text, encoding="utf-8")

    if not args.skip_hub_upload and args.hub_api_key.strip():
        upload_report_to_hub(
            hub_url=args.hub_url,
            api_key=args.hub_api_key.strip(),
            project_name=repo_name,
            repo_name=resolve_repo_name_for_hub(repo_root),
            task=task,
            report_date=report_date,
            report_text=updated_text,
            source=args.source.strip() or "work-report-updater",
        )

    if args.print_path:
        print(report_path)
    else:
        print(f"Updated {report_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
