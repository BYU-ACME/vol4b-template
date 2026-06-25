#!/usr/bin/env python3
"""Non-destructive ACME environment health check for student dev containers."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent
CONFIG_PATH = UTILS_DIR / "check_config.json"

SECTION_LABELS = {
    "docker": "Docker & Python",
    "vscode": "VS Code",
    "github": "GitHub",
}


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    section: str
    name: str
    status: Status
    detail: str = ""
    fix: str = ""


def _use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(text: str, code: str) -> str:
    if not _use_color():
        return text
    colors = {
        "bold": "\x1b[1m",
        "green": "\x1b[32m",
        "yellow": "\x1b[33m",
        "red": "\x1b[31m",
        "cyan": "\x1b[36m",
        "reset": "\x1b[m",
    }
    return f"{colors.get(code, '')}{text}{colors['reset']}"


def _load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _load_devcontainer() -> dict:
    path = REPO_ROOT / ".devcontainer" / "devcontainer.json"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _has_network(host: str = "github.com", port: int = 443, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _add(results: list[CheckResult], section: str, name: str, status: Status,
         detail: str = "", fix: str = "") -> None:
    results.append(CheckResult(section, name, status, detail, fix))


def _check_container(results: list[CheckResult]) -> None:
    section = "docker"
    in_container = (
        Path("/.dockerenv").exists()
        or os.environ.get("REMOTE_CONTAINERS")
        or os.environ.get("CODESPACES")
    )
    if in_container:
        _add(results, section, "Container environment", Status.PASS, "Running inside a dev container")
    else:
        _add(
            results, section, "Container environment", Status.FAIL,
            "Does not appear to be running inside the ACME dev container",
            "In VS Code: Command Palette → Dev Containers: Reopen in Container",
        )

    image = _load_devcontainer().get("image", "")
    if isinstance(image, str) and image.strip():
        _add(results, section, "Dev container config", Status.PASS, f"Image: {image.strip()}")
    else:
        _add(
            results, section, "Dev container config", Status.FAIL,
            ".devcontainer/devcontainer.json is missing or has no image field",
            "Reopen in container from your assignment repo, or restore .devcontainer/devcontainer.json from the template",
        )


def _check_python(results: list[CheckResult], config: dict) -> None:
    section = "docker"
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    expected = config.get("python_version", "3.13")
    if version.startswith(expected):
        _add(results, section, "Python version", Status.PASS, version)
    else:
        _add(
            results, section, "Python version", Status.FAIL,
            f"Found {version}, expected {expected}.x",
            "Rebuild the dev container (Dev Containers: Rebuild Container)",
        )

    for package in config.get("import_packages", ["numpy", "matplotlib"]):
        try:
            importlib.import_module(package)
            _add(results, section, f"Import {package}", Status.PASS)
        except ImportError as exc:
            _add(
                results, section, f"Import {package}", Status.FAIL,
                str(exc),
                "Rebuild the dev container. If the problem persists, contact a TA.",
            )


def _check_kernel(results: list[CheckResult], config: dict) -> None:
    section = "docker"
    kernel_name = config.get("kernel_name", "acme")
    display = config.get("kernel_display_name", "ACME Python")
    try:
        proc = _run(["jupyter", "kernelspec", "list"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _add(
            results, section, "Jupyter kernel", Status.FAIL,
            "Could not run jupyter kernelspec list",
            f'Run: python -m ipykernel install --user --name {kernel_name} '
            f'--display-name "{display}"',
        )
        return

    if kernel_name in proc.stdout:
        _add(results, section, "Jupyter kernel", Status.PASS, f"'{kernel_name}' ({display})")
    else:
        _add(
            results, section, "Jupyter kernel", Status.FAIL,
            f"Kernel '{kernel_name}' not found",
            f'Rebuild the dev container, or run:\n'
            f'  python -m ipykernel install --user --name {kernel_name} '
            f'--display-name "{display}"',
        )


def _check_lint(results: list[CheckResult]) -> None:
    section = "docker"
    lint_path = _run(["bash", "-lc", "command -v lint"])
    if lint_path.returncode == 0 and lint_path.stdout.strip():
        _add(results, section, "lint command", Status.PASS, lint_path.stdout.strip())
    else:
        _add(
            results, section, "lint command", Status.WARN,
            "lint not found on PATH",
            "Rebuild or restart the dev container, or run: bash .utils/install_completions.sh",
        )


def _check_data_record(results: list[CheckResult], config: dict) -> None:
    section = "docker"
    record_rel = config.get("data_record_path", ".utils/acme-data/data_record.json")
    record_path = REPO_ROOT / record_rel
    expected_version = config.get("data_version", "")

    if not record_path.is_file():
        _add(
            results, section, "Lab data", Status.WARN,
            "No data download record found",
            "Run: acme download_data",
        )
        return

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _add(
            results, section, "Lab data", Status.WARN,
            f"Could not read {record_rel}",
            "Run: acme download_data",
        )
        return

    pulled = record.get("data_version", "")
    detail = f"Version {pulled}, {record.get('file_count', '?')} files"
    if record.get("pulled_at"):
        detail += f" (pulled {record['pulled_at'][:10]})"

    if expected_version and pulled != expected_version:
        _add(
            results, section, "Lab data", Status.WARN,
            f"Downloaded {pulled}, course expects {expected_version}",
            "Run: acme download_data",
        )
    else:
        _add(results, section, "Lab data", Status.PASS, detail)


def _check_vscode(results: list[CheckResult], config: dict) -> None:
    section = "vscode"
    extensions = config.get("vscode_extensions", {})
    if not extensions:
        _add(results, section, "VS Code extensions", Status.SKIP, "No extension checks configured")
        return

    proc = _run(["bash", "-lc", "command -v code >/dev/null && code --list-extensions"])
    if proc.returncode != 0:
        _add(
            results, section, "VS Code CLI", Status.WARN,
            "Could not list extensions (code CLI unavailable)",
            "If notebooks fail to open, reopen in container and install the Jupyter extension manually",
        )
        return

    installed = {line.strip().lower() for line in proc.stdout.splitlines() if line.strip()}
    for ext_id, meta in extensions.items():
        label = meta.get("label", ext_id)
        if ext_id.lower() in installed:
            _add(results, section, f"{label} extension", Status.PASS, ext_id)
        else:
            _add(
                results, section, f"{label} extension", Status.WARN,
                f"{ext_id} not installed",
                f'Install the "{label}" extension in VS Code, then Reopen in Container if needed',
            )


def _check_git_basics(results: list[CheckResult]) -> None:
    section = "github"
    if _run(["bash", "-lc", "command -v git"]).returncode != 0:
        _add(results, section, "Git installed", Status.FAIL, "git not found on PATH")
        return
    _add(results, section, "Git installed", Status.PASS)

    for key, label in (("user.name", "Git user.name"), ("user.email", "Git user.email")):
        proc = _run(["git", "config", "--global", key])
        value = proc.stdout.strip()
        if value:
            _add(results, section, label, Status.PASS, value)
        else:
            _add(
                results, section, label, Status.WARN, "Not configured",
                f'Run: git config --global {key} "Your Name or Email"',
            )


def _check_git_repo(results: list[CheckResult]) -> None:
    section = "github"
    if not (REPO_ROOT / ".git").is_dir():
        _add(
            results, section, "Git repository", Status.FAIL,
            "This directory is not a git repository",
            "Clone your assignment repo from GitHub Classroom instead of downloading a ZIP",
        )
        return
    _add(results, section, "Git repository", Status.PASS)

    proc = _run(["git", "remote", "get-url", "origin"])
    if proc.returncode == 0 and proc.stdout.strip():
        _add(results, section, "Git remote origin", Status.PASS, proc.stdout.strip())
    else:
        _add(
            results, section, "Git remote origin", Status.WARN,
            "No origin remote configured",
            "Your repo should be connected to your GitHub Classroom assignment repository",
        )


def _check_git_worktree(results: list[CheckResult]) -> None:
    section = "github"
    if not (REPO_ROOT / ".git").is_dir():
        return

    status = _run(["git", "status", "--porcelain"])
    if status.stdout.strip():
        count = len(status.stdout.strip().splitlines())
        _add(
            results, section, "Uncommitted changes", Status.WARN,
            f"{count} changed file(s) not committed",
            "Commit when ready: git add -A && git commit -m \"your message\"",
        )
    else:
        _add(results, section, "Uncommitted changes", Status.PASS, "Working tree clean")

    upstream = _run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    if upstream.returncode != 0:
        _add(
            results, section, "Unpushed commits", Status.SKIP,
            "No upstream branch configured",
        )
        _add(results, section, "Unpulled commits", Status.SKIP, "No upstream branch configured")
        return

    unpushed = _run(["git", "rev-list", "--count", "@{upstream}..HEAD"])
    if unpushed.returncode == 0:
        count = int(unpushed.stdout.strip() or "0")
        if count:
            _add(
                results, section, "Unpushed commits", Status.WARN,
                f"{count} local commit(s) not pushed",
                "Run: git push",
            )
        else:
            _add(results, section, "Unpushed commits", Status.PASS, "All commits pushed")


def _check_git_unpulled(results: list[CheckResult], *, online: bool) -> None:
    section = "github"
    if not (REPO_ROOT / ".git").is_dir():
        return

    upstream = _run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    if upstream.returncode != 0:
        return

    if not online:
        _add(
            results, section, "Unpulled commits", Status.SKIP,
            "Skipped (no network connection)",
        )
        return

    fetch = _run(["git", "fetch", "origin"], timeout=45)
    if fetch.returncode != 0:
        _add(
            results, section, "Unpulled commits", Status.SKIP,
            "Skipped (could not reach GitHub)",
            fetch.stderr.strip() or fetch.stdout.strip(),
        )
        return

    behind = _run(["git", "rev-list", "--count", "HEAD..@{upstream}"])
    if behind.returncode != 0:
        _add(results, section, "Unpulled commits", Status.SKIP, "Could not compare with remote")
        return

    count = int(behind.stdout.strip() or "0")
    if count:
        _add(
            results, section, "Unpulled commits", Status.WARN,
            f"{count} commit(s) on GitHub not yet pulled",
            "Run: git pull — see the course Git guide if you see merge conflicts",
        )
    else:
        _add(results, section, "Unpulled commits", Status.PASS, "Up to date with GitHub")


def run_checks(*, online: bool | None = None) -> list[CheckResult]:
    config = _load_config()
    if online is None:
        online = _has_network()

    results: list[CheckResult] = []
    _check_container(results)
    _check_python(results, config)
    _check_kernel(results, config)
    _check_lint(results)
    _check_data_record(results, config)
    _check_vscode(results, config)
    _check_git_basics(results)
    _check_git_repo(results)
    _check_git_worktree(results)
    _check_git_unpulled(results, online=online)
    return results


def _section_status(items: Iterable[CheckResult]) -> Status:
    statuses = [item.status for item in items]
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.WARN in statuses:
        return Status.WARN
    if statuses and all(s == Status.SKIP for s in statuses):
        return Status.SKIP
    return Status.PASS


def _status_label(status: Status) -> str:
    if status == Status.PASS:
        return _c("PASS", "green")
    if status == Status.WARN:
        return _c("WARN", "yellow")
    if status == Status.FAIL:
        return _c("FAIL", "red")
    return _c("SKIP", "cyan")


def _print_student_report(results: list[CheckResult]) -> None:
    print(_c("ACME Environment Check", "bold"))
    print("=" * 24)
    print()

    by_section: dict[str, list[CheckResult]] = {}
    for item in results:
        by_section.setdefault(item.section, []).append(item)

    for section_key in ("docker", "vscode", "github"):
        items = by_section.get(section_key, [])
        if not items:
            continue
        label = SECTION_LABELS[section_key]
        print(f"{label:<20}{_status_label(_section_status(items))}")

    issues = [r for r in results if r.status in (Status.WARN, Status.FAIL)]
    print()
    if not issues:
        print(_c("All checks passed.", "green"))
    else:
        fails = sum(1 for r in issues if r.status == Status.FAIL)
        warns = sum(1 for r in issues if r.status == Status.WARN)
        parts = []
        if fails:
            parts.append(f"{fails} failed")
        if warns:
            parts.append(f"{warns} warning(s)")
        print(_c(" — ".join(parts).capitalize(), "yellow" if warns and not fails else "red"))
        print()

        for section_key in ("docker", "vscode", "github"):
            section_issues = [r for r in issues if r.section == section_key]
            if not section_issues:
                continue
            print(_c(SECTION_LABELS[section_key], "bold"))
            for item in section_issues:
                mark = "✗" if item.status == Status.FAIL else "!"
                print(f"  {mark} {item.name}", end="")
                if item.detail:
                    print(f" — {item.detail}")
                else:
                    print()
                if item.fix:
                    for line in item.fix.splitlines():
                        print(f"      {line}")
            print()

    print("Run  acme check --verbose  for full details (TAs).")


def _print_verbose_report(results: list[CheckResult]) -> None:
    print(_c("ACME Environment Check (verbose)", "bold"))
    print("=" * 32)
    print()

    by_section: dict[str, list[CheckResult]] = {}
    for item in results:
        by_section.setdefault(item.section, []).append(item)

    for section_key in ("docker", "vscode", "github"):
        items = by_section.get(section_key, [])
        if not items:
            continue
        print(_c(SECTION_LABELS[section_key], "bold"))
        print("-" * len(SECTION_LABELS[section_key]))
        for item in items:
            tag = item.status.value.upper()
            if item.status == Status.PASS:
                tag = _c(tag, "green")
            elif item.status == Status.WARN:
                tag = _c(tag, "yellow")
            elif item.status == Status.FAIL:
                tag = _c(tag, "red")
            else:
                tag = _c(tag, "cyan")
            line = f"[{tag}] {item.name}"
            if item.detail:
                line += f" — {item.detail}"
            print(line)
            if item.fix:
                print(f"       Fix: {item.fix.replace(chr(10), chr(10) + '            ')}")
        print()

    counts = {s: 0 for s in Status}
    for item in results:
        counts[item.status] += 1
    print(
        f"Summary: {counts[Status.PASS]} passed, "
        f"{counts[Status.WARN]} warnings, "
        f"{counts[Status.FAIL]} failed, "
        f"{counts[Status.SKIP]} skipped"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACME environment health check")
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show every check (for TAs)",
    )
    parser.add_argument(
        "--online", action="store_true",
        help="Force remote git checks even if network probe fails",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Skip checks that require network",
    )
    args = parser.parse_args(argv)

    online = False if args.offline else (True if args.online else None)
    results = run_checks(online=online)

    if args.verbose:
        _print_verbose_report(results)
    else:
        _print_student_report(results)

    if any(r.status == Status.FAIL for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
