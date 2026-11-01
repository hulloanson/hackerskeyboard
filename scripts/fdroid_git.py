#!/usr/bin/env python3
"""Clone and publish the public GitHub repository that serves F-Droid files.

Git uses the caller's configured credential helper, askpass program, or SSH
agent. This script never accepts or writes credentials and never embeds them in
the remote URL.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "resource_guard.py"
REMOTE = "https://github.com/hulloanson/fdroid_repo.git"
CLONE = ROOT / "repo_publish"
PAGE_PATHS = ("repo", "README.md", ".nojekyll", "index.html")
PUBLIC_REPO_URL = "https://raw.githubusercontent.com/hulloanson/fdroid_repo/main/repo/"
README_DETAILS_START = "<!-- fdroid-public-details:start -->"
README_DETAILS_END = "<!-- fdroid-public-details:end -->"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"F-Droid Git transport: {message}")


def guarded(command: Sequence[str], label: str) -> None:
    argv = [sys.executable, str(GUARD), "run", "--label", label,
            "--path", str(ROOT), "--", *command]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(argv, check=True, env=env)


def clone_repo() -> None:
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "repo_publish/.probe"]).returncode
    if ignored != 0:
        fail("repo_publish must be ignored by the main repository before cloning")
    if CLONE.exists():
        if not CLONE.is_dir() or any(CLONE.iterdir()):
            fail(f"clone destination exists and is not empty: {CLONE}")
    else:
        CLONE.parent.mkdir(parents=True, exist_ok=True)
    guarded(["git", "clone", REMOTE, str(CLONE)], "clone F-Droid publishing repository")
    check_clone()
    print(f"Cloned {REMOTE} into {CLONE}")


def check_clone() -> None:
    if not CLONE.is_dir() or not (CLONE / ".git").exists():
        fail(f"repository clone is missing: {CLONE}; run clone")
    result = subprocess.run(["git", "-C", str(CLONE), "remote", "get-url", "origin"],
                            check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if result not in (REMOTE, REMOTE.removesuffix(".git")):
        fail(f"unexpected origin URL: {result}")
    branch = subprocess.run(["git", "-C", str(CLONE), "symbolic-ref", "--short", "HEAD"],
                            check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if branch != "main":
        fail(f"expected GitHub Pages source branch 'main', found {branch!r}")


def publish_paths(paths: Sequence[str], title: str, purpose: str) -> None:
    check_clone()
    names = subprocess.run(["git", "-C", str(CLONE), "diff", "--cached", "--name-only"],
                           check=True, stdout=subprocess.PIPE, text=True).stdout.splitlines()
    if any(not any(name == allowed or name.startswith(allowed.rstrip("/") + "/") for allowed in PAGE_PATHS)
           for name in names):
        fail("there are already staged changes outside the publishing allowlist; inspect them first")
    for path_name in paths:
        candidate = CLONE / path_name
        if not candidate.exists():
            fail(f"publishing path is missing: {path_name}")
        if path_name == "repo":
            forbidden = re.compile(r"(?i)(\.p12$|\.jks$|\.keystore$|\.pass$|^config\.ya?ml$)")
            for path in candidate.rglob("*"):
                if path.is_symlink():
                    fail(f"repo/ contains a symlink: {path.relative_to(CLONE)}")
                if path.is_file() and forbidden.search(path.name):
                    fail(f"repo/ contains a private key or config file: {path.relative_to(CLONE)}")
    subprocess.run(["git", "-C", str(CLONE), "add", "--", *paths], check=True)
    staged = subprocess.run(["git", "-C", str(CLONE), "diff", "--cached", "--name-only"],
                            check=True, stdout=subprocess.PIPE, text=True).stdout.splitlines()
    if not staged:
        print("F-Droid repository is already up to date")
        return
    if any(not any(name == allowed or name.startswith(allowed.rstrip("/") + "/") for allowed in PAGE_PATHS)
           for name in staged):
        fail("staged changes include paths outside the publishing allowlist; refusing to commit")
    subprocess.run(["git", "-C", str(CLONE), "-c", "user.name=hulloanson",
                    "-c", "user.email=9977536+hulloanson@users.noreply.github.com",
                    "commit", "-m", title, "-m", f"- {purpose}"], check=True)
    guarded(["git", "-C", str(CLONE), "push", "origin", "main"], "publish F-Droid repository to GitHub")
    print("Published the staged public F-Droid files to GitHub main branch")


def bootstrap_pages() -> None:
    check_clone()
    marker = CLONE / ".nojekyll"
    marker.touch(exist_ok=True)
    landing = CLONE / "README.md"
    if not landing.exists():
        landing.write_text(
            "# Hacker's Keyboard F-Droid Repository\n\n"
            "This repository serves signed Hacker's Keyboard releases for the F-Droid client.\n\n"
            "The signed repository index and APK files are published under `repo/`.\n"
            f"The public HTTPS repository URL and signing fingerprint are listed below.\n\n"
            f"{README_DETAILS_START}\n"
            f"Repository URL: {PUBLIC_REPO_URL}\n"
            "Repository signing fingerprint (SHA-256): pending first release\n"
            f"{README_DETAILS_END}\n",
            encoding="utf-8",
        )
    landing_page = CLONE / "index.html"
    if not landing_page.exists():
        landing_page.write_text(
            "<!doctype html>\n"
            "<html lang=\"en\"><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Hacker's Keyboard F-Droid Repository</title>\n"
            "<main><h1>Hacker's Keyboard F-Droid Repository</h1>\n"
            "<p>Signed releases are served from the <a href=\"repo/\">F-Droid repository</a>.</p>\n"
            "<p>The HTTPS repository URL and signing fingerprint will be published with the first release.</p>\n"
            "</main></html>\n",
            encoding="utf-8",
        )
    publish_paths((".nojekyll", "README.md", "index.html"), "Initialize F-Droid Pages hosting",
                  "Add a landing page and disable Jekyll processing for repository files.")


def publish_repo(message: str) -> None:
    check_clone()
    if not (CLONE / "repo").is_dir():
        fail("repo_publish/repo is missing; run scripts/fdroid_repo.py stage after indexing an APK")
    update_public_details()
    publish_paths(("repo", "README.md"), message,
                  "Publish the signed index, APK files, and repository signing fingerprint.")


def update_public_details() -> None:
    readme = CLONE / "README.md"
    if not readme.is_file() or readme.is_symlink():
        fail("repo_publish/README.md is missing or not a regular file")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/signing.py"), "fingerprints"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    ).stdout
    match = re.search(r"^repo: ([0-9a-f]{64})$", result, re.MULTILINE)
    if not match:
        fail("could not read the verified repository signing fingerprint")
    block = (
        f"{README_DETAILS_START}\n"
        f"Repository URL: {PUBLIC_REPO_URL}\n"
        f"Repository signing fingerprint (SHA-256): {match.group(1)}\n"
        f"{README_DETAILS_END}"
    )
    contents = readme.read_text(encoding="utf-8")
    managed = re.compile(
        re.escape(README_DETAILS_START) + r".*?" + re.escape(README_DETAILS_END),
        re.DOTALL,
    )
    if managed.search(contents):
        updated, count = managed.subn(lambda _match: block, contents)
        if count != 1:
            fail("README.md must contain exactly one managed repository details block")
    else:
        placeholder = "The HTTPS repository URL and signing fingerprint will be published with the first release."
        if contents.count(placeholder) != 1:
            fail("README.md has no managed repository details block; review and add one before publishing")
        updated = contents.replace(placeholder, "The public HTTPS repository URL and signing fingerprint are listed below.\n\n" + block)
    if updated != contents:
        readme.write_text(updated, encoding="utf-8")
        os.chmod(readme, 0o644)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clone", help="clone hulloanson/fdroid_repo using inherited git credentials")
    sub.add_parser("bootstrap", help="publish the repository landing page")
    publish = sub.add_parser("publish", help="commit and push only repo/ contents")
    publish.add_argument("--message", default="Publish F-Droid repository index and APKs")
    args = parser.parse_args(argv)
    try:
        if args.command == "clone":
            clone_repo()
        elif args.command == "bootstrap":
            bootstrap_pages()
        else:
            publish_repo(args.message)
    except subprocess.CalledProcessError as exc:
        fail(f"git operation failed with exit status {exc.returncode}; credentials were not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
