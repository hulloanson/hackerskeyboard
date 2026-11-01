#!/usr/bin/env python3
"""Configure GitHub Pages and verify a published F-Droid repository over HTTPS.

For API writes, provide a fine-scoped token in HK_GITHUB_TOKEN_FILE (mode 0600)
or HK_GITHUB_TOKEN. Credentials are never accepted as command-line arguments,
written to disk by this script, or included in its output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "resource_guard.py"
API_ROOT = "https://api.github.com"
REPO = "hulloanson/fdroid_repo"
APP_ID = "com.hulloanson"


class StalePublication(Exception):
    """The host still serves a complete, signed repository from an older release."""


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"GitHub repository: {message}")


def token() -> str:
    token_file = os.environ.get("HK_GITHUB_TOKEN_FILE")
    if token_file:
        path = pathlib.Path(token_file).expanduser().resolve(strict=True)
        for protected in (ROOT.resolve(), (ROOT / "repo_publish").resolve()):
            if path == protected or protected in path.parents:
                fail("HK_GITHUB_TOKEN_FILE must be outside both repositories")
        if path.is_symlink() or not path.is_file():
            fail("HK_GITHUB_TOKEN_FILE must be a regular non-symlink file")
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            fail("HK_GITHUB_TOKEN_FILE must be owned by this user and have mode 0600")
        value = path.read_text(encoding="ascii").strip()
    else:
        value = os.environ.get("HK_GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    if not value or "\n" in value or "\r" in value:
        fail("set HK_GITHUB_TOKEN_FILE or HK_GITHUB_TOKEN in the environment")
    return value


def api_request(method: str, path: str, *, body: dict[str, object] | None = None, authenticated: bool = True):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "hackerskeyboard-fdroid-publisher",
    }
    if authenticated:
        headers["Authorization"] = f"Bearer {token()}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(API_ROOT + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # Do not print server response bodies in case they echo sensitive request data.
        return exc.code, {}
    except urllib.error.URLError:
        fail("GitHub API request failed; check network and credential configuration")


def pages_enable(replace_existing: bool) -> None:
    path = f"/repos/{REPO}/pages"
    source = {"branch": "main", "path": "/"}
    status, payload = api_request("GET", path)
    if status == 404:
        status, payload = api_request("POST", path, body={"source": source})
    elif status == 200:
        existing = payload.get("source") if isinstance(payload, dict) else None
        if existing == source:
            print("GitHub Pages is already configured for branch main at the repository root")
            return
        if not replace_existing:
            fail("Pages is already configured differently; use --replace only after reviewing the current source")
        status, payload = api_request("PUT", path, body={"source": source})
    if status not in (200, 201, 202):
        fail(f"GitHub Pages API returned HTTP {status}; verify repo admin permissions and Pages availability")
    html_url = payload.get("html_url") if isinstance(payload, dict) else None
    if html_url:
        print(f"GitHub Pages configuration accepted: {html_url}")
    else:
        print("GitHub Pages configuration accepted for branch main at the repository root")


def pages_status(timeout: int, interval: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        status, payload = api_request("GET", f"/repos/{REPO}/pages", authenticated=False)
        if status == 200 and isinstance(payload, dict) and payload.get("html_url"):
            source = payload.get("source") or {}
            if source.get("branch") != "main" or source.get("path") != "/":
                fail("GitHub Pages is not serving the configured main branch root")
            url = str(payload["html_url"])
            if not url.startswith("https://"):
                fail("GitHub Pages did not report an HTTPS URL")
            print(f"GitHub Pages HTTPS URL: {url}")
            return
        if status not in (404, 200):
            fail(f"GitHub Pages status API returned HTTP {status}")
        if time.monotonic() >= deadline:
            fail("timed out waiting for GitHub Pages to become active")
        time.sleep(interval)


def fetch_https(url: str, destination: pathlib.Path, *, cache_bust: bool = False) -> pathlib.Path:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        fail("repository URL and all redirects must use HTTPS")
    if cache_bust:
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("fdroid_verify", str(time.time_ns())))
        url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    request = urllib.request.Request(url, headers={"User-Agent": "hackerskeyboard-fdroid-verifier"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https":
                fail("repository download redirected away from HTTPS")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 512 * 1024 * 1024:
                fail(f"repository file exceeds the 512 MiB verification limit: {destination.name}")
            total = 0
            with destination.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        fail(f"repository file exceeds the 512 MiB verification limit: {destination.name}")
                    output.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError):
        fail(f"could not download expected repository file: {url.rsplit('/', 1)[-1]}")
    return destination


def verify_jar(path: pathlib.Path, fingerprint: str) -> None:
    jarsigner = shutil.which("jarsigner")
    keytool = shutil.which("keytool")
    if not jarsigner or not keytool:
        fail("jarsigner and keytool are required to verify hosted index signatures")
    result = subprocess.run([jarsigner, "-verify", "-strict", "-verbose", "-certs", str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "LC_ALL": "C"})
    if (result.returncode not in (0, 4)
            or "jar verified" not in result.stdout.lower()
            or re.search(r"\bunsigned entries?\b", result.stdout, re.IGNORECASE)):
        fail(f"signature verification failed for hosted {path.name}")
    certificate = subprocess.run([keytool, "-printcert", "-jarfile", str(path)], check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
    matches = re.findall(r"SHA256:\s*([0-9A-Fa-f:]+)", certificate)
    if len(matches) != 1 or matches[0].replace(":", "").lower() != fingerprint:
        fail(f"hosted index signer does not match the expected repository fingerprint ({path.name})")


def verify_repository(
    repo_url: str,
    fingerprint: str,
    app_fingerprint: str | None,
    expected_apk_sha256: str | None,
    wait_seconds: int,
    interval_seconds: int,
) -> None:
    parsed = urllib.parse.urlsplit(repo_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        fail("repository URL must be a credential-free HTTPS URL")
    if parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/repo"):
        fail("repository URL must end in /repo with no query or fragment")
    fingerprint = fingerprint.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        fail("expected fingerprint must be a SHA-256 certificate fingerprint")
    target = pathlib.Path(os.environ.get("HK_VERIFY_TMP", ROOT / ".local/fdroid-verify")).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected_apk_sha256 = expected_apk_sha256.replace(":", "").lower() if expected_apk_sha256 else None
    if expected_apk_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_apk_sha256):
        fail("expected APK SHA-256 must be 64 hexadecimal characters")
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        guard_command = [
            sys.executable, str(GUARD), "run", "--label", "verify hosted F-Droid repository",
            "--path", str(ROOT), "--path", str(target), "--",
            sys.executable, str(pathlib.Path(__file__).resolve()), "_verify",
            repo_url.rstrip("/"), fingerprint, app_fingerprint or "-",
            expected_apk_sha256 or "-", str(target),
        ]
        result = subprocess.run(guard_command, check=False)
        if result.returncode == 0:
            return
        if result.returncode != 75 or expected_apk_sha256 is None:
            fail(f"HTTPS repository verification failed with exit status {result.returncode}")
        if time.monotonic() >= deadline:
            fail("timed out waiting for the raw host to serve the expected APK SHA-256")
        time.sleep(max(1, interval_seconds))


def verify_repository_inner(
    repo_url: str,
    fingerprint: str,
    app_fingerprint: str | None,
    expected_apk_sha256: str | None,
    target: pathlib.Path,
) -> None:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    index_v1 = target / "index-v1.jar"
    entry_jar = target / "entry.jar"
    fetch_https(repo_url.rstrip("/") + "/index-v1.jar", index_v1, cache_bust=expected_apk_sha256 is not None)
    fetch_https(repo_url.rstrip("/") + "/entry.jar", entry_jar, cache_bust=expected_apk_sha256 is not None)
    verify_jar(index_v1, fingerprint)
    verify_jar(entry_jar, fingerprint)
    with zipfile.ZipFile(index_v1) as archive:
        index_v1_bytes = archive.read("index-v1.json")
    if APP_ID.encode("utf-8") not in index_v1_bytes:
        fail(f"hosted index-v1 does not list {APP_ID}")
    with zipfile.ZipFile(entry_jar) as archive:
        entry_bytes = archive.read("entry.json")
    external_entry_path = target / "entry.json"
    fetch_https(repo_url.rstrip("/") + "/entry.json", external_entry_path, cache_bust=expected_apk_sha256 is not None)
    if entry_bytes != external_entry_path.read_bytes():
        fail("hosted entry.json does not match the JAR-signed entry")
    entry = json.loads(entry_bytes)
    index = entry.get("index", {})
    name = index.get("name")
    if not isinstance(name, str) or pathlib.PurePosixPath(name).name != "index-v2.json":
        fail("signed entry metadata does not identify index-v2.json")
    index_path = fetch_https(
        repo_url.rstrip("/") + "/" + name.lstrip("/"),
        target / "index-v2.json",
        cache_bust=expected_apk_sha256 is not None,
    )
    if index_path.stat().st_size != int(index.get("size", -1)):
        fail("hosted index-v2 size does not match the signed entry")
    digest = hashlib.sha256()
    with index_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != index.get("sha256"):
        fail("hosted index-v2 digest does not match the signed entry")
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    package = index_data.get("packages", {}).get(APP_ID)
    if not isinstance(package, dict):
        fail(f"hosted index-v2 does not list {APP_ID}")
    versions = package.get("versions")
    if not isinstance(versions, dict) or not versions:
        fail(f"hosted index-v2 has no releases for {APP_ID}")
    latest = max(
        versions.values(),
        key=lambda version: int(version.get("manifest", {}).get("versionCode", -1)),
    )
    file_info = latest.get("file", {})
    manifest = latest.get("manifest", {})
    apk_name = file_info.get("name")
    if not isinstance(apk_name, str) or pathlib.PurePosixPath(apk_name).name != apk_name.lstrip("/"):
        fail("signed index contains an unsafe APK filename")
    apk_path = fetch_https(
        repo_url.rstrip("/") + "/" + apk_name.lstrip("/"),
        target / apk_name.lstrip("/"),
        cache_bust=expected_apk_sha256 is not None,
    )
    if apk_path.stat().st_size != int(file_info.get("size", -1)):
        fail("hosted APK size does not match the signed index")
    apk_digest = hashlib.sha256()
    with apk_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            apk_digest.update(chunk)
    actual_apk_sha256 = apk_digest.hexdigest()
    if actual_apk_sha256 != file_info.get("sha256"):
        fail("hosted APK digest does not match the signed index")
    if expected_apk_sha256 and actual_apk_sha256 != expected_apk_sha256:
        raise StalePublication(f"host still serves APK SHA-256 {actual_apk_sha256}")
    signer_info = manifest.get("signer", {}).get("sha256", [])
    if not isinstance(signer_info, list) or len(signer_info) != 1:
        fail("signed index does not identify exactly one APK signer")
    index_signer = str(signer_info[0]).replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", index_signer):
        fail("signed index contains an invalid APK signer fingerprint")
    expected_app = app_fingerprint.replace(":", "").lower() if app_fingerprint else index_signer
    if expected_app != index_signer:
        fail("APK signer in the index does not match the supplied app fingerprint")
    apksigner = shutil.which("apksigner")
    if not apksigner:
        fail("apksigner is required to verify the hosted APK")
    verification = subprocess.run(
        [apksigner, "verify", "--verbose", "--print-certs", str(apk_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout
    actual_signers = re.findall(r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", verification)
    if (len(actual_signers) != 1
            or actual_signers[0].replace(":", "").lower() != expected_app):
        fail("hosted APK signature does not match its signed-index signer fingerprint")
    print(
        f"Verified HTTPS repository, v1/v2 indexes, APK SHA-256, and signer fingerprints "
        f"(repo {fingerprint}; app {expected_app})"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enable = sub.add_parser("pages-enable", help="configure Pages from branch main root")
    enable.add_argument("--replace", action="store_true", help="replace a different existing Pages source")
    status = sub.add_parser("pages-wait", help="wait for GitHub Pages to serve HTTPS")
    status.add_argument("--timeout", type=int, default=300)
    status.add_argument("--interval", type=int, default=5)
    verify = sub.add_parser("verify", help="verify public HTTPS index files and signer fingerprint")
    verify.add_argument("--repo-url", required=True)
    verify.add_argument("--fingerprint", required=True)
    verify.add_argument("--app-fingerprint", help="expected APK signer SHA-256 certificate fingerprint")
    verify.add_argument("--apk-sha256", help="expected current APK SHA-256; wait for raw-host propagation")
    verify.add_argument("--wait-seconds", type=int, default=300)
    verify.add_argument("--interval-seconds", type=int, default=5)
    internal = sub.add_parser("_verify", help=argparse.SUPPRESS)
    internal.add_argument("repo_url")
    internal.add_argument("fingerprint")
    internal.add_argument("app_fingerprint")
    internal.add_argument("apk_sha256")
    internal.add_argument("directory", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.command == "pages-enable":
        pages_enable(args.replace)
    elif args.command == "pages-wait":
        pages_status(args.timeout, args.interval)
    elif args.command == "verify":
        verify_repository(args.repo_url, args.fingerprint, args.app_fingerprint,
                          args.apk_sha256, args.wait_seconds, args.interval_seconds)
    else:
        try:
            verify_repository_inner(
                args.repo_url,
                args.fingerprint,
                None if args.app_fingerprint == "-" else args.app_fingerprint,
                None if args.apk_sha256 == "-" else args.apk_sha256,
                args.directory,
            )
        except StalePublication as exc:
            print(f"GitHub hosting: {exc}", file=sys.stderr)
            return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
