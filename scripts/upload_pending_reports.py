#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

from push_report import DEFAULT_API_KEY, DEFAULT_APP_URL, main as push_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload all uncommitted work-report markdown files in the current repo to the report hub."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root or any path inside the repo. Defaults to the current directory.",
    )
    parser.add_argument(
        "--app-url",
        default=DEFAULT_APP_URL,
        help="Base URL of the hosted app.",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="Report hub API key.",
    )
    parser.add_argument(
        "--source",
        default="work-report-updater-bulk-sync",
        help="Source label for uploaded reports.",
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


def resolve_repo_name(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    remote_url = result.stdout.strip()
    if result.returncode != 0 or not remote_url:
        return repo_root.name

    github_match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote_url)
    if github_match:
        return github_match.group(1)
    return remote_url


def list_pending_report_files(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to inspect git status for pending report files.")

    report_paths: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        rel_path = line[3:].strip()
        if " -> " in rel_path:
            rel_path = rel_path.split(" -> ", 1)[1]
        path = repo_root / rel_path
        if path.name.startswith("work-report-") and path.suffix == ".md" and path.exists():
            report_paths.append(path)

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in report_paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    return unique_paths


def parse_report_date(report_path: Path) -> str | None:
    match = re.match(r"work-report-([a-z]{3})-(\d{1,2})-(\d{4})-", report_path.name)
    if not match:
        return None

    month_name, day, year = match.groups()
    report_date = dt.datetime.strptime(f"{month_name} {day} {year}", "%b %d %Y").date()
    return report_date.isoformat()


def parse_report_title(report_text: str, fallback: str) -> str:
    latest_title: str | None = None
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            latest_title = stripped[2:].strip() or fallback
    return latest_title or fallback


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root(Path(args.repo_root))
    repo_name = resolve_repo_name(repo_root)
    report_files = list_pending_report_files(repo_root)

    if not report_files:
        print("No uncommitted work report files found.")
        return 0

    uploaded = 0
    for report_path in report_files:
        report_text = report_path.read_text(encoding="utf-8").strip()
        title = parse_report_title(report_text, report_path.stem)
        report_date = parse_report_date(report_path)

        argv = [
            "push_report.py",
            "--app-url",
            args.app_url,
            "--api-key",
            args.api_key,
            "--project-name",
            repo_root.name,
            "--repo-name",
            repo_name,
            "--title",
            title,
            "--detail-file",
            str(report_path),
            "--source",
            args.source,
        ]
        if report_date:
            argv.extend(["--report-date", report_date])

        original_argv = sys.argv
        try:
            sys.argv = argv
            result = push_main()
        finally:
            sys.argv = original_argv

        if result != 0:
            return result
        uploaded += 1

    print(f"Uploaded {uploaded} pending report file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
