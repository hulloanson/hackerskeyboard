#!/usr/bin/env python3
"""Create, back up, inspect, and use the persistent Hacker's Keyboard keys."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "resource_guard.py"
KEYS = {
    "app": ("app-release.p12", "app-release.pass", "app-release"),
    "repo": ("fdroid-index.p12", "fdroid-index.pass", "fdroid-repo"),
}
BUILD_TOOLS_VERSION = "35.0.0"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"signing: {message}")


def signing_home() -> pathlib.Path:
    configured = os.environ.get("HK_SIGNING_HOME")
    path = pathlib.Path(configured or pathlib.Path.home() / ".local/share/hackerskeyboard/signing")
    path = path.expanduser().resolve()
    reject_repository_path(path, "HK_SIGNING_HOME")
    return path


def backup_home() -> pathlib.Path:
    configured = os.environ.get("HK_SIGNING_BACKUP_DIR")
    path = pathlib.Path(configured or pathlib.Path.home() / "hackerskeyboard-signing-backup")
    path = path.expanduser().resolve()
    reject_repository_path(path, "HK_SIGNING_BACKUP_DIR")
    if path == signing_home():
        fail("backup directory must differ from HK_SIGNING_HOME")
    return path


def backup_archive() -> pathlib.Path:
    configured = os.environ.get("HK_SIGNING_ARCHIVE")
    requested = pathlib.Path(configured or pathlib.Path.home() / "hackerskeyboard-signing-backup.tar.gz").expanduser()
    if requested.is_symlink():
        fail(f"backup archive target must not be a symlink: {requested}")
    path = requested.resolve()
    reject_repository_path(path, "HK_SIGNING_ARCHIVE")
    if path in (signing_home(), backup_home()):
        fail("backup archive must be a separate file outside the key directories")
    return path


def reject_repository_path(path: pathlib.Path, name: str) -> None:
    for repository in (ROOT.resolve(), (ROOT / "repo_publish").resolve()):
        if path == repository or repository in path.parents:
            fail(f"{name} resolves inside a tracked repository: {path}")


def secure_directory(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        fail(f"expected a real directory: {path}")
    os.chmod(path, 0o700)


def guarded(command: Sequence[str], label: str, paths: Sequence[pathlib.Path]) -> None:
    argv = [sys.executable, str(GUARD), "run", "--label", label, "--path", str(ROOT)]
    for path in paths:
        argv.extend(("--path", str(path)))
    argv.extend(("--", *command))
    subprocess.run(argv, check=True)


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        fail(f"required executable not found on PATH: {name}")
    return path


def public_fingerprint(keystore: pathlib.Path, password: pathlib.Path, alias: str) -> str:
    keytool = ensure_tool("keytool")
    exported = subprocess.run(
        [keytool, "-exportcert", "-alias", alias, "-keystore", str(keystore),
         "-storetype", "PKCS12", "-storepass:file", str(password)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return hashlib.sha256(exported).hexdigest()


def verify_keystore(keystore: pathlib.Path, password: pathlib.Path, alias: str) -> str:
    keytool = ensure_tool("keytool")
    if not keystore.is_file() or not password.is_file():
        fail(f"key material is incomplete for alias {alias}; refusing to generate a replacement")
    if stat.S_IMODE(password.stat().st_mode) != 0o600:
        fail(f"password file must have mode 0600: {password}")
    subprocess.run(
        [keytool, "-list", "-keystore", str(keystore), "-storetype", "PKCS12",
         "-alias", alias, "-storepass:file", str(password)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return public_fingerprint(keystore, password, alias)


def key_paths(home: pathlib.Path, kind: str) -> tuple[pathlib.Path, pathlib.Path, str]:
    keystore_name, password_name, alias = KEYS[kind]
    return home / keystore_name, home / password_name, alias


def create_key(home: pathlib.Path, kind: str) -> str:
    keystore, password, alias = key_paths(home, kind)
    keytool = ensure_tool("keytool")
    if keystore.exists() or password.exists():
        fail(f"{kind} key files already exist or are incomplete; refusing to replace them")
    password.write_text(secrets.token_hex(32) + "\n", encoding="ascii")
    os.chmod(password, 0o600)
    distinguished_name = (
        "CN=com.hulloanson, OU=APK Signing, O=Hulloanson"
        if kind == "app"
        else "CN=Hacker's Keyboard F-Droid Repository, OU=Index Signing, O=Hulloanson"
    )
    try:
        guarded(
            [keytool, "-genkeypair", "-noprompt", "-keystore", str(keystore),
             "-storetype", "PKCS12", "-alias", alias, "-keyalg", "RSA",
             "-keysize", "4096", "-sigalg", "SHA256withRSA", "-validity", "10000",
             "-dname", distinguished_name, "-storepass:file", str(password),
             "-keypass:file", str(password)],
            f"generate {kind} signing key",
            (home,),
        )
        os.chmod(keystore, 0o600)
        return verify_keystore(keystore, password, alias)
    except Exception:
        # Keep partial artifacts so a retry cannot silently generate a new key.
        raise


def atomic_copy(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
            fail(f"backup already has different key material at {destination}; refusing to overwrite")
        os.chmod(destination, 0o600)
        return
    fd, temporary_name = tempfile.mkstemp(prefix=".signing-backup-", dir=destination.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def create_verified_archive(backup: pathlib.Path) -> pathlib.Path:
    archive = backup_archive()
    archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if archive.is_symlink() or (archive.exists() and not archive.is_file()):
        fail(f"backup archive target must be a regular non-symlink file: {archive}")
    entries = [backup / name for names in KEYS.values() for name in names[:2]]
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".signing-archive-", dir=archive.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz") as tar:
                for source in entries:
                    if source.is_symlink() or not source.is_file():
                        fail(f"backup archive source is not a regular file: {source}")
                    info = tarfile.TarInfo(source.name)
                    info.size = source.stat().st_size
                    info.mode = 0o600
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with source.open("rb") as content:
                        tar.addfile(info, content)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary, 0o600)
        verify_backup_archive(temporary, backup)
        os.replace(temporary, archive)
        directory_fd = os.open(archive.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if stat.S_IMODE(archive.stat().st_mode) != 0o600:
            fail(f"backup archive permissions are not mode 0600: {archive}")
        verify_backup_archive(archive, backup)
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def verify_backup_archive(archive: pathlib.Path, backup: pathlib.Path) -> None:
    expected = {name: backup / name for names in KEYS.values() for name in names[:2]}
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) != len(expected) or {member.name for member in members} != set(expected):
                fail("backup archive must contain exactly the four signing key and password files")
            for member in members:
                if not member.isfile() or stat.S_IMODE(member.mode) != 0o600:
                    fail(f"backup archive entry is not a mode 0600 regular file: {member.name}")
                extracted = tar.extractfile(member)
                if extracted is None:
                    fail(f"could not read backup archive entry: {member.name}")
                expected_file = expected[member.name]
                if hashlib.sha256(extracted.read()).digest() != hashlib.sha256(expected_file.read_bytes()).digest():
                    fail(f"backup archive entry differs from verified backup file: {member.name}")
    except (tarfile.TarError, OSError) as exc:
        fail(f"could not verify backup archive ({type(exc).__name__})")


def backup_keys() -> None:
    home = signing_home()
    backup = backup_home()
    secure_directory(home)
    secure_directory(backup)
    for kind in KEYS:
        keystore, password, alias = key_paths(home, kind)
        fingerprint = verify_keystore(keystore, password, alias)
        atomic_copy(keystore, backup / keystore.name)
        atomic_copy(password, backup / password.name)
        if verify_keystore(backup / keystore.name, backup / password.name, alias) != fingerprint:
            fail(f"backup fingerprint check failed for {kind} signing key")
        print(f"{kind} signing key fingerprint: {fingerprint}")
    archive = create_verified_archive(backup)
    print(f"verified access-controlled signing-key archive (mode 0600): {archive}")


def create_keys() -> None:
    home = signing_home()
    secure_directory(home)
    # Refuse a partial first run so an interruption cannot cause a key to be
    # regenerated on a later run.
    all_paths = [path for kind in KEYS for path in key_paths(home, kind)[:2]]
    if any(path.exists() for path in all_paths):
        if all(path.is_file() for path in all_paths):
            for kind in KEYS:
                key, password, alias = key_paths(home, kind)
                verify_keystore(key, password, alias)
            backup_keys()
            return
        fail("partial key state exists; inspect HK_SIGNING_HOME and restore or remove only by explicit operator action")
    for kind in KEYS:
        fingerprint = create_key(home, kind)
        print(f"{kind} signing key fingerprint: {fingerprint}")
    backup_keys()


def print_fingerprints() -> None:
    home = signing_home()
    for kind in KEYS:
        keystore, password, alias = key_paths(home, kind)
        print(f"{kind}: {verify_keystore(keystore, password, alias)}")


def sign_apk(unsigned_apk: pathlib.Path, output: pathlib.Path | None) -> None:
    home = signing_home()
    keystore, password, alias = key_paths(home, "app")
    expected_fingerprint = verify_keystore(keystore, password, alias)
    unsigned_apk = unsigned_apk.resolve(strict=True)
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not android_home:
        fail("set ANDROID_HOME or ANDROID_SDK_ROOT to the installed Android SDK")
    sdk = pathlib.Path(android_home).expanduser().resolve(strict=True)
    build_tools = sdk / "build-tools" / BUILD_TOOLS_VERSION
    apksigner = build_tools / "apksigner"
    aapt = build_tools / "aapt"
    if not apksigner.is_file() or not aapt.is_file():
        fail(f"Android SDK build-tools {BUILD_TOOLS_VERSION} with apksigner and aapt were not found")
    badging = subprocess.run([str(aapt), "dump", "badging", str(unsigned_apk)], check=True,
                             text=True, stdout=subprocess.PIPE).stdout
    match = re.search(r"^package: name='([^']+)' versionCode='([^']+)'", badging, re.MULTILINE)
    if not match:
        fail("aapt could not read the APK package name and version code")
    package_id, version_code = match.groups()
    if package_id != "com.hulloanson":
        fail(f"APK package ID is {package_id!r}; expected com.hulloanson")
    if output is None:
        release_dir = pathlib.Path(
            os.environ.get("HK_RELEASE_DIR", pathlib.Path.home() / "hackerskeyboard-releases")
        ).expanduser().resolve()
        output = release_dir / f"{package_id}_{version_code}.apk"
    else:
        output = output.expanduser().resolve()
    reject_repository_path(output, "APK output")
    if output.exists():
        fail(f"refusing to overwrite existing signed APK: {output}")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_directory(output.parent)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        guarded(
            [str(apksigner), "sign", "--ks", str(keystore), "--ks-type", "PKCS12",
             "--ks-key-alias", alias, "--ks-pass", f"file:{password}",
             "--key-pass", f"file:{password}", "--out", str(temporary), str(unsigned_apk)],
            "sign release APK",
            (sdk, output.parent, home),
        )
        verification = subprocess.run(
            [str(apksigner), "verify", "--verbose", "--print-certs", str(temporary)],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ).stdout
        signer = re.search(r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", verification)
        if not signer:
            fail("apksigner did not report the signed APK certificate fingerprint")
        actual = signer.group(1).replace(":", "").lower()
        if actual != expected_fingerprint:
            fail("signed APK fingerprint does not match the persistent app signing key")
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
        print(f"signed APK: {output}")
        print(f"package: {package_id}; version code: {version_code}")
        print(f"APK signer SHA-256: {actual}")
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create both persistent keys once and back them up")
    sub.add_parser("backup", help="verify and copy existing keys to the backup directory")
    sub.add_parser("fingerprints", help="print the public certificate fingerprints")
    sign = sub.add_parser("sign-apk", help="sign and verify one unsigned APK")
    sign.add_argument("unsigned_apk", type=pathlib.Path)
    sign.add_argument("output", type=pathlib.Path, nargs="?", help="external output path; default ~/hackerskeyboard-releases/<package>_<versionCode>.apk")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            create_keys()
        elif args.command == "backup":
            backup_keys()
        elif args.command == "fingerprints":
            print_fingerprints()
        else:
            sign_apk(args.unsigned_apk, args.output)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"operation failed ({type(exc).__name__}); inspect tool output without exposing password files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
