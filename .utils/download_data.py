#!/usr/bin/env python3
"""Download lab data files into the student repository working tree."""

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parent

# Injected at publish time when using public data repositories.
# DATA_VERSION matches byu.yml docker_image_tag (a branch name on the data repo).
DATA_REPO = 'https://github.com/BYU-ACME/vol4b-data.git'
DATA_VERSION = 'jun2026'

# Publish-only stamp in public data repos (see repo_urls.DATA_VERSION_FILENAME).
_DATA_VERSION_STAMP = "ACME_DATA_VERSION"
_ACME_DIR = UTILS_DIR / "acme-data"
_DATA_RECORD = _ACME_DIR / "data_record.json"


def run(command, *, cwd=None):
    """Run a shell command and stop if it fails."""
    subprocess.run(command, check=True, cwd=cwd or REPO_ROOT)


def capture(command, *, cwd=None):
    """Run a shell command and return its stdout."""
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        cwd=cwd or REPO_ROOT,
    )
    return result.stdout


def load_gitignore(gitignore_path: Path) -> tuple[list[str], set[str]]:
    if not gitignore_path.exists():
        return [], set()
    lines = gitignore_path.read_text().splitlines()
    return lines, {line.strip() for line in lines if line.strip()}


def is_lab_data_path(path: str) -> bool:
    """Ignore git metadata and publish-only files."""
    if path == _DATA_VERSION_STAMP:
        return False
    return path != ".git" and not path.startswith(".git/")


def update_gitignore(gitignore_path: Path, data_files: list[str]) -> None:
    lines, existing = load_gitignore(gitignore_path)
    new_entries = [
        path for path in data_files if path not in existing and is_lab_data_path(path)
    ]
    if not new_entries and gitignore_path.exists():
        return
    with gitignore_path.open("a" if gitignore_path.exists() else "w", newline="\n") as f:
        if not gitignore_path.exists() or not lines:
            pass
        for entry in new_entries:
            f.write(entry + "\n")
            print(f"Adding to .gitignore: {entry}")


def list_files_from_git_ref(ref: str) -> list[str]:
    output = capture(["git", "ls-tree", "-r", "--name-only", ref])
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and is_lab_data_path(line.strip())
    ]


def list_tracked_files(repo_dir: Path) -> list[str]:
    """List tracked files in a clone (excludes .git/ metadata)."""
    output = capture(["git", "ls-files"], cwd=repo_dir)
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and is_lab_data_path(line.strip())
    ]


def write_data_record(data_files: list[str], *, checkout_dir: Path | None = None) -> None:
    """Record which data version was pulled and copy the upstream version stamp."""
    _ACME_DIR.mkdir(parents=True, exist_ok=True)

    if checkout_dir is not None:
        stamp_src = checkout_dir / _DATA_VERSION_STAMP
        if stamp_src.is_file():
            shutil.copy2(stamp_src, _ACME_DIR / _DATA_VERSION_STAMP)

    record = {
        "data_version": DATA_VERSION,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "source": DATA_REPO or "origin/data",
        "file_count": len(data_files),
    }
    _DATA_RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def pull_from_private_data_branch() -> list[str]:
    print("Fetching latest data branch from origin (your repo)...")
    run(["git", "fetch", "origin", "data"])

    data_files = list_files_from_git_ref("origin/data")
    print("Refreshing data files from origin/data (overwriting local copies)...")
    run(["git", "checkout", "origin/data", "--", "."])
    run(["git", "restore", "--staged", "."])
    write_data_record(data_files)
    return data_files


def pull_from_public_data_repo() -> list[str]:
    print(f"Downloading data from {DATA_REPO} (version {DATA_VERSION})...")

    with tempfile.TemporaryDirectory(prefix="acme-data-") as tmp:
        checkout_dir = Path(tmp) / "data"
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                DATA_VERSION,
                DATA_REPO,
                str(checkout_dir),
            ]
        )

        data_files = list_tracked_files(checkout_dir)

        print(f"Refreshing {len(data_files)} data files (overwriting local copies)...")
        for rel_path in data_files:
            if not is_lab_data_path(rel_path):
                continue
            src = checkout_dir / rel_path
            dest = REPO_ROOT / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        write_data_record(data_files, checkout_dir=checkout_dir)

    return data_files


def main():
    os.chdir(REPO_ROOT)
    gitignore_path = REPO_ROOT / ".gitignore"

    if DATA_REPO:
        data_files = pull_from_public_data_repo()
    else:
        data_files = pull_from_private_data_branch()

    print("Updating .gitignore for data files...")
    update_gitignore(gitignore_path, data_files)

    print("\033[92m\nData successfully pulled!\n\033[0m")


if __name__ == "__main__":
    main()
