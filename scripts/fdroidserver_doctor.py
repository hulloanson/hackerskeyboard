#!/usr/bin/env python3
"""Safely validate the installed F-Droid server setup and PKCS#12 signing flow."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


APP_ID = "com.hulloanson"


def check(condition: bool, stage: str) -> None:
    if not condition:
        raise RuntimeError(stage)


def main() -> int:
    if len(sys.argv) != 4:
        print("F-Droid doctor: invalid arguments", file=sys.stderr)
        return 2
    work = pathlib.Path(sys.argv[1]).resolve(strict=True)
    expected_app = sys.argv[2].replace(":", "").lower()
    expected_repo = sys.argv[3].replace(":", "").lower()
    stage = "environment"
    try:
        check(re.fullmatch(r"[0-9a-f]{64}", expected_app) is not None, stage)
        check(re.fullmatch(r"[0-9a-f]{64}", expected_repo) is not None, stage)
        configured_password = os.environ.get("HK_FDROID_REPO_PASSWORD")
        check(bool(configured_password), stage)
        os.chdir(work)

        stage = "fdroidserver imports"
        from fdroidserver import index, metadata, common

        stage = "config and environment secret resolution"
        config = common.read_config()
        common.options = argparse.Namespace(verbose=False)
        store_password = config.get("keystorepass")
        key_password = config.get("keypass")
        check(isinstance(store_password, str) and store_password == configured_password, stage)
        check(isinstance(key_password, str) and key_password == configured_password, stage)
        check(config.get("repo_keyalias") == "fdroid-repo", stage)
        check(pathlib.Path(config.get("keystore", "")).is_file(), stage)
        check(pathlib.Path(config["keystore"]).suffix.lower() == ".p12", stage)
        check(config.get("repo_url", "").endswith("/repo"), stage)
        icon_name = config.get("repo_icon")
        check(isinstance(icon_name, str) and pathlib.Path(icon_name).is_file(), "repository icon")

        stage = "repository key export through fdroidserver"
        _public_key_hex, fdroid_repo_fingerprint = index.extract_pubkey()
        normalized_repo = re.sub(r"[^0-9a-f]", "", fdroid_repo_fingerprint.lower())
        check(normalized_repo == expected_repo, stage)

        stage = "metadata parsing and signing-key allowlist"
        apps = metadata.read_metadata()
        app = apps.get(APP_ID)
        check(app is not None, stage)
        allowed = app.get("AllowedAPKSigningKeys", [])
        check(allowed == [expected_app], stage)

        stage = "F-Droid apksigner PKCS#12 compatibility"
        apksigner = config.get("apksigner")
        jarsigner = config.get("jarsigner")
        keytool = config.get("keytool")
        check(bool(apksigner and pathlib.Path(apksigner).is_file()), stage)
        check(bool(jarsigner and pathlib.Path(jarsigner).is_file()), stage)
        check(bool(keytool and pathlib.Path(keytool).is_file()), stage)
        child_env = os.environ.copy()
        child_env["FDROID_KEY_STORE_PASS"] = store_password
        child_env["FDROID_KEY_PASS"] = key_password
        with tempfile.TemporaryDirectory(prefix=".fdroid-doctor-", dir=work / "tmp") as temporary:
            jar = pathlib.Path(temporary) / "fdroid-apksigner-doctor.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("doctor.txt", "temporary F-Droid apksigner check\n")
            sign_command = [
                apksigner, "sign", "--min-sdk-version", "23", "--max-sdk-version", "24",
                "--v1-signing-enabled", "true", "--v2-signing-enabled", "false",
                "--v3-signing-enabled", "false", "--v4-signing-enabled", "false",
                "--ks", config["keystore"], "--ks-pass", "env:FDROID_KEY_STORE_PASS",
                "--ks-key-alias", config["repo_keyalias"], "--key-pass", "env:FDROID_KEY_PASS",
                str(jar),
            ]
            result = subprocess.run(sign_command, env=child_env, check=False,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            check(result.returncode == 0, stage)
            verify = subprocess.run(
                [jarsigner, "-verify", "-strict", "-verbose", "-certs", str(jar)],
                env=child_env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = verify.stdout
            check(verify.returncode in (0, 4), stage)
            check("jar verified" in output.lower(), stage)
            check(re.search(r"\bunsigned entries?\b", output, re.IGNORECASE) is None, stage)
            certificate = subprocess.run(
                [keytool, "-printcert", "-jarfile", str(jar)],
                env=child_env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            check(certificate.returncode == 0, stage)
            signer_fingerprints = re.findall(r"SHA256:\s*([0-9A-Fa-f:]+)", certificate.stdout)
            check(len(signer_fingerprints) == 1, stage)
            signer = signer_fingerprints[0].replace(":", "").lower()
            check(signer == expected_repo, stage)

        print("Validated fdroidserver config/env mapping, metadata key pin, repo icon, and PKCS#12 index signing")
        return 0
    except Exception:
        # In particular, never include subprocess diagnostics: they may contain
        # configuration values or local paths that should remain private.
        print(f"F-Droid doctor failed during {stage}; diagnostic output suppressed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
