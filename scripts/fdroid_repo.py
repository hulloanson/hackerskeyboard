#!/usr/bin/env python3
"""Set up and stage a signed binary F-Droid repository outside the app checkout."""

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
import urllib.parse
import zipfile
from collections.abc import Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "resource_guard.py"
PINNED_FDROIDSERVER = "2.4.5"
APP_ID = "com.hulloanson"
REPO_KEY_ALIAS = "fdroid-repo"
BUILD_TOOLS_VERSION = "35.0.0"
DEFAULT_REPO_URL = "https://raw.githubusercontent.com/hulloanson/fdroid_repo/main/repo/"
APP_REPO_ICON = ROOT / "app/src/main/res/drawable-mdpi/icon.png"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"fdroid repo: {message}")


def signing_home() -> pathlib.Path:
    value = pathlib.Path(os.environ.get("HK_SIGNING_HOME", pathlib.Path.home() / ".local/share/hackerskeyboard/signing"))
    path = value.expanduser().resolve()
    for protected in (ROOT.resolve(), (ROOT / "repo_publish").resolve()):
        if path == protected or protected in path.parents:
            fail(f"HK_SIGNING_HOME must be outside both repositories: {path}")
    return path


def work_home() -> pathlib.Path:
    value = pathlib.Path(os.environ.get("HK_FDROID_WORK_HOME", signing_home() / "fdroid-work"))
    path = value.expanduser().resolve()
    for protected in (ROOT.resolve(), (ROOT / "repo_publish").resolve()):
        if path == protected or protected in path.parents:
            fail(f"HK_FDROID_WORK_HOME must be outside both repositories: {path}")
    if path == signing_home():
        fail("HK_FDROID_WORK_HOME must differ from HK_SIGNING_HOME")
    return path


def backup_home() -> pathlib.Path:
    value = pathlib.Path(os.environ.get("HK_SIGNING_BACKUP_DIR", pathlib.Path.home() / "hackerskeyboard-signing-backup"))
    path = value.expanduser().resolve()
    for protected in (ROOT.resolve(), (ROOT / "repo_publish").resolve()):
        if path == protected or protected in path.parents:
            fail(f"HK_SIGNING_BACKUP_DIR must be outside both repositories: {path}")
    return path


def env_path(name: str, default: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(os.environ.get(name, str(default))).expanduser().resolve()
    return path


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private_file(path: pathlib.Path) -> None:
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        fail(f"required private file is missing or not mode 0600: {path}")


def run_guarded(command: Sequence[str], label: str, paths: Sequence[pathlib.Path], *, env: dict[str, str] | None = None) -> None:
    argv = [sys.executable, str(GUARD), "run", "--label", label, "--path", str(ROOT)]
    for path in paths:
        argv.extend(("--path", str(path)))
    argv.extend(("--", *command))
    subprocess.run(argv, check=True, env=env)


def shell_in(directory: pathlib.Path, command: Sequence[str]) -> list[str]:
    # The fixed shell fragment carries no data; paths and command parts remain
    # separate argv entries, and secret values are only passed in the environment.
    return ["/bin/sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(directory), *command]


def fdroid_executable() -> pathlib.Path:
    location = env_path(
        "HK_FDROID_VENV",
        ROOT / ".local/fdroidserver-2.4.5",
    )
    executable = location / "bin" / "fdroid"
    if not executable.is_file():
        fail(f"F-Droid tools are not installed; run {ROOT / 'scripts/fdroid_repo.py'} setup")
    return executable


def android_sdk() -> pathlib.Path:
    value = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not value:
        fail("set ANDROID_HOME or ANDROID_SDK_ROOT to the Android SDK path")
    path = pathlib.Path(value).expanduser().resolve()
    if not path.is_dir():
        fail(f"Android SDK directory does not exist: {path}")
    return path


def repo_url() -> str:
    value = os.environ.get("FDROID_REPO_URL", DEFAULT_REPO_URL).strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        fail("FDROID_REPO_URL must be a credential-free HTTPS URL")
    if parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/repo"):
        fail("FDROID_REPO_URL must end in /repo and contain no query or fragment")
    return value.rstrip("/")


def key_material() -> tuple[pathlib.Path, pathlib.Path, str]:
    home = signing_home()
    keystore = home / "fdroid-index.p12"
    password = home / "fdroid-index.pass"
    if not keystore.is_file():
        fail(f"repository signing key is missing: {keystore}; run scripts/signing.py init after review")
    require_private_file(password)
    if stat.S_IMODE(keystore.stat().st_mode) != 0o600:
        fail(f"repository keystore must have mode 0600: {keystore}")
    return keystore, password, REPO_KEY_ALIAS


def write_config(work: pathlib.Path) -> None:
    url = repo_url()
    keystore, _password, alias = key_material()
    config = work / "config.yml"
    existing = config.exists()
    if existing:
        if config.is_symlink() or not config.is_file():
            fail(f"refusing to replace non-regular F-Droid config: {config}")
        if "# managed by scripts/fdroid_repo.py" not in config.read_text(encoding="utf-8"):
            fail(f"existing config is not managed by this script: {config}")

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    contents = "\n".join((
        "# managed by scripts/fdroid_repo.py",
        f"repo_url: {quote(url)}",
        "repo_name: " + quote("Hacker's Keyboard F-Droid Repository"),
        "repo_description: >-",
        "  Signed Hacker's Keyboard releases for the F-Droid client.",
        "repo_maxage: 0",
        f"repo_keyalias: {quote(alias)}",
        f"keystore: {quote(str(keystore))}",
        "keystorepass: {env: HK_FDROID_REPO_PASSWORD}",
        "keypass: {env: HK_FDROID_REPO_PASSWORD}",
        "repo_icon: 'icon.png'",
        "archive_older: 0",
        "sdk_path: " + quote(str(android_sdk())),
        "make_current_version_link: true",
        "",
    ))
    temporary = config.with_suffix(".yml.tmp")
    temporary.write_text(contents, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, config)


def app_fingerprint() -> str:
    home = signing_home()
    keystore = home / "app-release.p12"
    password = home / "app-release.pass"
    if not keystore.is_file():
        fail(f"app signing key is missing: {keystore}; run scripts/signing.py init after review")
    require_private_file(password)
    if stat.S_IMODE(keystore.stat().st_mode) != 0o600:
        fail(f"app keystore must have mode 0600: {keystore}")
    result = subprocess.run(
        ["keytool", "-exportcert", "-alias", "app-release", "-keystore", str(keystore),
         "-storetype", "PKCS12", "-storepass:file", str(password)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def write_metadata(
    work: pathlib.Path,
    version_code: str | None = None,
    version_name: str | None = None,
) -> None:
    metadata = work / "metadata" / f"{APP_ID}.yml"
    metadata.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if metadata.is_symlink():
        fail(f"refusing symlinked F-Droid metadata path: {metadata}")
    fingerprint = app_fingerprint()
    current_version = ""
    if version_code is not None and version_name is not None:
        current_version = (
            f"CurrentVersion: {json.dumps(version_name, ensure_ascii=False)}\n"
            f"CurrentVersionCode: {version_code}\n"
        )
    content = f"""# managed by scripts/fdroid_repo.py
Categories:
  - System
License: Apache-2.0
AuthorName: Hulloanson
WebSite: https://github.com/hulloanson/hackerskeyboard
SourceCode: https://github.com/hulloanson/hackerskeyboard
IssueTracker: https://github.com/hulloanson/hackerskeyboard/issues
AutoUpdateMode: None
UpdateCheckMode: None
AllowedAPKSigningKeys: {fingerprint}
Name: Hacker's Keyboard
Summary: A full keyboard layout with Tab, Ctrl, Esc, and arrow keys
Description: |-
  Hacker's Keyboard is a software keyboard based on the Android Open Source
  Project keyboard. This fork includes a portrait Dvorak layout adjustment.
{current_version}"""
    if metadata.exists() and metadata.read_text(encoding="utf-8") != content:
        previous = metadata.read_text(encoding="utf-8")
        if "# managed by scripts/fdroid_repo.py" not in previous:
            fail(f"metadata is not managed by this script; review before updating: {metadata}")
        metadata.write_text(content, encoding="utf-8")
        os.chmod(metadata, 0o600)
    elif not metadata.exists():
        metadata.write_text(content, encoding="utf-8")
        os.chmod(metadata, 0o600)


def configure_init() -> None:
    work = work_home()
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(work, 0o700)
    android_sdk()
    key_material()
    write_config(work)
    for directory in ("repo", "metadata", "unsigned", "tmp", "config"):
        (work / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    if not APP_REPO_ICON.is_file():
        fail(f"app repository icon is missing: {APP_REPO_ICON}")
    icon = work / "icon.png"
    if icon.is_symlink():
        fail(f"refusing symlinked F-Droid repository icon path: {icon}")
    temporary_icon = work / ".icon.png.tmp"
    shutil.copyfile(APP_REPO_ICON, temporary_icon)
    os.chmod(temporary_icon, 0o644)
    os.replace(temporary_icon, icon)
    category_config = work / "config" / "categories.yml"
    category_contents = "# managed by scripts/fdroid_repo.py\nSystem:\n  name: System\n"
    if category_config.is_symlink():
        fail(f"refusing symlinked category config: {category_config}")
    if category_config.exists():
        if not category_config.is_file():
            fail(f"refusing to replace non-regular category config: {category_config}")
        if "# managed by scripts/fdroid_repo.py" not in category_config.read_text(encoding="utf-8"):
            fail(f"category config is not managed by this script: {category_config}")
    category_config.write_text(category_contents, encoding="utf-8")
    os.chmod(category_config, 0o600)
    metadata = work / "metadata" / f"{APP_ID}.yml"
    if not metadata.exists():
        write_metadata(work)
    print(f"F-Droid workspace initialized outside both repositories: {work}")


def repo_fingerprint() -> str:
    keystore, password, alias = key_material()
    exported = subprocess.run(
        ["keytool", "-exportcert", "-alias", alias, "-keystore", str(keystore),
         "-storepass:file", str(password)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout
    return hashlib.sha256(exported).hexdigest()


def doctor() -> None:
    work = work_home()
    configure_init()
    sdk = android_sdk()
    tools = env_path("HK_FDROID_VENV", ROOT / ".local/fdroidserver-2.4.5")
    python = tools / "bin" / "python"
    fdroid = fdroid_executable()
    if not python.is_file():
        fail(f"F-Droid virtual environment is missing; run {ROOT / 'scripts/fdroid_repo.py'} setup")
    env = os.environ.copy()
    env.update(signing_password_env())
    paths = (work, signing_home(), sdk, tools)
    for command, label in (
        ([str(fdroid), "lint", APP_ID, "config/categories.yml"], "validate F-Droid app metadata and categories"),
        ([str(python), str(ROOT / "scripts/fdroidserver_doctor.py"), work.as_posix(),
          app_fingerprint(), repo_fingerprint()], "validate F-Droid config and signing compatibility"),
    ):
        argv = [sys.executable, str(GUARD), "run", "--label", label, "--path", str(ROOT)]
        for path in paths:
            argv.extend(("--path", str(path)))
        argv.extend(("--", *shell_in(work, command)))
        result = subprocess.run(argv, check=False, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode != 0:
            fail(f"{label} failed with exit status {result.returncode}; output suppressed")
    print("F-Droid 2.4.5 config, metadata, app signing-key pin, repository key, icon, and PKCS#12 checks passed")


def get_apk_version(apk: pathlib.Path, sdk: pathlib.Path) -> tuple[str, str]:
    build_tools = sdk / "build-tools"
    aapt = build_tools / BUILD_TOOLS_VERSION / "aapt"
    if not aapt.is_file():
        fail(f"aapt is missing from Android SDK build-tools {BUILD_TOOLS_VERSION}")
    output = subprocess.run([str(aapt), "dump", "badging", str(apk)], check=True,
                            stdout=subprocess.PIPE, text=True).stdout
    match = re.search(
        r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']*)'",
        output,
        re.MULTILINE,
    )
    if not match:
        fail("aapt could not read the APK package name and version code")
    if match.group(1) != APP_ID:
        fail(f"APK package ID is {match.group(1)!r}; expected {APP_ID}")
    if not match.group(2).isdecimal():
        fail("APK version code must be a decimal integer")
    return match.group(2), match.group(3)


def signing_password_env() -> dict[str, str]:
    _keystore, password, _alias = key_material()
    value = password.read_text(encoding="ascii").strip()
    if not value or "\n" in value or "\r" in value:
        fail("repository key password file has invalid contents")
    return {"HK_FDROID_REPO_PASSWORD": value}


def copy_public_repo(source: pathlib.Path, destination: pathlib.Path) -> None:
    if not source.is_dir():
        fail(f"generated F-Droid repo directory is missing: {source}")
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        fail(f"publish target must be a real directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for current, dirs, files in os.walk(source):
        current_path = pathlib.Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        if any((current_path / name).is_symlink() for name in dirs):
            fail(f"generated F-Droid repo contains a symlinked directory: {current_path}")
        expected = set(dirs) | set(files)
        for child in target_dir.iterdir():
            if child.name not in expected:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        for name in files:
            source_file = current_path / name
            if source_file.is_symlink():
                fail(f"generated F-Droid repo contains a symlink: {source_file}")
            if re.search(r"(?i)(\.p12|\.jks|\.keystore|\.pass|config\.ya?ml)$", name):
                fail(f"refusing to stage a private or configuration file in repo/: {name}")
            shutil.copy2(source_file, target_dir / name)


def copy_apk_file(source: pathlib.Path, destination: pathlib.Path) -> None:
    if source.is_symlink() or not source.is_file():
        fail(f"signed APK source must be a regular non-symlink file: {source}")
    if destination.exists() or destination.is_symlink():
        fail(f"refusing to overwrite APK in F-Droid workspace: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(source, destination)
    os.chmod(destination, 0o644)


def verify_index_signatures(repo: pathlib.Path) -> None:
    keytool = shutil.which("keytool")
    jarsigner = shutil.which("jarsigner")
    if not keytool or not jarsigner:
        fail("keytool and jarsigner are required to verify generated index signatures")
    home = signing_home()
    keystore, password, alias = home / "fdroid-index.p12", home / "fdroid-index.pass", REPO_KEY_ALIAS
    expected = hashlib.sha256(subprocess.run(
        [keytool, "-exportcert", "-alias", alias, "-keystore", str(keystore), "-storetype", "PKCS12",
         "-storepass:file", str(password)], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout).hexdigest()
    required = ("index-v1.jar", "entry.jar", "index-v1.json", "index-v2.json", "entry.json")
    for name in required:
        if not (repo / name).is_file():
            fail(f"F-Droid update did not produce required index output: repo/{name}")
    for name in ("index-v1.jar", "entry.jar"):
        jar = repo / name
        result = subprocess.run([jarsigner, "-verify", "-strict", "-certs", str(jar)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "LC_ALL": "C"})
        if (result.returncode not in (0, 4)
                or "jar verified" not in result.stdout.lower()
                or re.search(r"\bunsigned entries?\b", result.stdout, re.IGNORECASE)):
            fail(f"signature verification failed for repo/{name}")
        cert = subprocess.run([keytool, "-printcert", "-jarfile", str(jar)], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        matches = re.findall(r"SHA256:\s*([0-9A-Fa-f:]+)", cert)
        if len(matches) != 1 or matches[0].replace(":", "").lower() != expected:
            fail(f"repository index signer does not match the persistent key: repo/{name}")
    with zipfile.ZipFile(repo / "entry.jar") as entry_archive:
        embedded = json.loads(entry_archive.read("entry.json"))
    external = json.loads((repo / "entry.json").read_text(encoding="utf-8"))
    if embedded != external:
        fail("repo/entry.json does not match the signed copy inside entry.jar")
    index = embedded.get("index", {})
    index_name = index.get("name")
    if not isinstance(index_name, str) or index_name.lstrip("/") != "index-v2.json":
        fail("signed entry metadata does not point to the v2 index")
    index_path = repo / index_name.lstrip("/")
    index_bytes = index_path.read_bytes()
    if len(index_bytes) != int(index.get("size", -1)):
        fail("index-v2 size does not match signed entry metadata")
    if hashlib.sha256(index_bytes).hexdigest() != index.get("sha256"):
        fail("index-v2 digest does not match signed entry metadata")


def stage_apk(apk_argument: pathlib.Path) -> pathlib.Path:
    apk = apk_argument.resolve(strict=True)
    if apk.suffix.lower() != ".apk":
        fail("stage expects a signed .apk file")
    work = work_home()
    configure_init()
    sdk = android_sdk()
    version_code, version_name = get_apk_version(apk, sdk)
    write_metadata(work, version_code, version_name)
    destination = work / "repo" / f"{APP_ID}_{version_code}.apk"
    if destination.exists():
        if sha256_file(destination) != sha256_file(apk):
            fail(f"an APK with this package/version code already exists and differs: {destination}")
    else:
        run_guarded(
            [sys.executable, str(pathlib.Path(__file__).resolve()),
             "_copy_apk", str(apk), str(destination)],
            "copy signed APK into F-Droid workspace",
            (apk.parent, work),
        )
    env = os.environ.copy()
    env.update(signing_password_env())
    run_guarded(
        shell_in(work, [str(fdroid_executable()), "update"]),
        "generate and sign F-Droid repository indexes",
        (work, signing_home(), sdk),
        env=env,
    )
    run_guarded(
        [sys.executable, str(pathlib.Path(__file__).resolve()),
         "_verify_indexes", str(work / "repo")],
        "verify generated F-Droid indexes",
        (work, signing_home(), sdk),
    )
    print(f"F-Droid indexes verified; publishable repo tree: {work / 'repo'}")
    return work / "repo"


def check_publish_clone(clone: pathlib.Path) -> None:
    if not clone.is_dir() or not (clone / ".git").exists():
        fail(f"F-Droid repository clone is missing; run scripts/fdroid_git.py clone first: {clone}")
    if clone.resolve() == ROOT.resolve() or clone.resolve() == signing_home():
        fail("publish clone path is unsafe")
    result = subprocess.run(["git", "-C", str(clone), "remote", "get-url", "origin"],
                            check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if result not in ("https://github.com/hulloanson/fdroid_repo", "https://github.com/hulloanson/fdroid_repo.git"):
        fail(f"unexpected origin URL in repo_publish: {result}")
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "repo_publish/.probe"]).returncode
    if ignored != 0:
        fail("repo_publish is not ignored by the main repository")


def stage_to_clone(repo: pathlib.Path) -> None:
    clone = (ROOT / "repo_publish").resolve()
    check_publish_clone(clone)
    run_guarded(
        [sys.executable, str(pathlib.Path(__file__).resolve()), "_copy", str(repo), str(clone / "repo")],
        "stage public F-Droid files into repo_publish",
        (repo, clone),
    )
    print(f"F-Droid public files staged in {clone / 'repo'}")


def setup_tools() -> None:
    tools_home = env_path("HK_FDROID_VENV", ROOT / ".local/fdroidserver-2.4.5")
    python = shutil.which("python3")
    if not python:
        fail("python3 is required")
    tools_home.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (tools_home / "bin" / "python").exists():
        run_guarded([python, "-m", "venv", str(tools_home)], "create F-Droid Python environment", (tools_home.parent,))
    pip = tools_home / "bin" / "pip"
    if not pip.is_file():
        fail(f"Python venv has no pip executable: {tools_home}; install python3-venv and retry")
    run_guarded([str(pip), "install", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
                 f"fdroidserver=={PINNED_FDROIDSERVER}"],
                f"install fdroidserver {PINNED_FDROIDSERVER}", (tools_home.parent,))
    run_guarded([str(tools_home / "bin" / "fdroid"), "--version"],
                "verify F-Droid server tool installation", (tools_home.parent,))
    print(f"F-Droid server tools ready: {tools_home}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="install pinned fdroidserver in an external Python venv")
    sub.add_parser("init", help="initialize private F-Droid workspace and app metadata")
    stage = sub.add_parser("stage", help="index a signed APK and stage public repo files")
    stage.add_argument("apk", type=pathlib.Path)
    sub.add_parser("doctor", help="validate config, metadata, icon, and repository signing compatibility")
    copy = sub.add_parser("_copy", help=argparse.SUPPRESS)
    copy.add_argument("source", type=pathlib.Path)
    copy.add_argument("destination", type=pathlib.Path)
    copy_apk = sub.add_parser("_copy_apk", help=argparse.SUPPRESS)
    copy_apk.add_argument("source", type=pathlib.Path)
    copy_apk.add_argument("destination", type=pathlib.Path)
    verify = sub.add_parser("_verify_indexes", help=argparse.SUPPRESS)
    verify.add_argument("repo", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup_tools()
        elif args.command == "init":
            configure_init()
        elif args.command == "doctor":
            doctor()
        elif args.command == "_copy":
            copy_public_repo(args.source.resolve(strict=True), args.destination.absolute())
        elif args.command == "_copy_apk":
            copy_apk_file(args.source.resolve(strict=True), args.destination.absolute())
        elif args.command == "_verify_indexes":
            verify_index_signatures(args.repo.resolve(strict=True))
        else:
            public_repo = stage_apk(args.apk)
            stage_to_clone(public_repo)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"operation failed ({type(exc).__name__}); inspect tool output and preserve all existing signing keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
