#!/usr/bin/env python3
"""Verify the release APK identity, package contents, and 16 KB alignment."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APK = ROOT / "app/build/outputs/apk/release/app-release-unsigned.apk"
EXPECTED_PACKAGE = "com.hulloanson"


def gradle_version(field: str) -> str:
    """Read versionCode or versionName from app/build.gradle so releases cannot drift."""
    text = (ROOT / "app/build.gradle").read_text(encoding="utf-8")
    match = re.search(rf"^\s*{field}\s+'?([^'\s]+)'?\s*$", text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"verify-apk: {field} not found in app/build.gradle")
    return match.group(1)


EXPECTED_VERSION_CODE = gradle_version("versionCode")
EXPECTED_VERSION_NAME = gradle_version("versionName")
EXPECTED_MIN_SDK = "26"
EXPECTED_TARGET_SDK = "35"
EXPECTED_ABIS = {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}
EXPECTED_COMPONENTS = {
    "org.pocketworkstation.pckeyboard.LatinIME",
    "org.pocketworkstation.pckeyboard.Main",
    "org.pocketworkstation.pckeyboard.LatinIMESettings",
    "org.pocketworkstation.pckeyboard.InputLanguageSelection",
    "org.pocketworkstation.pckeyboard.PrefScreenActions",
    "org.pocketworkstation.pckeyboard.PrefScreenView",
    "org.pocketworkstation.pckeyboard.PrefScreenFeedback",
}


def fail(message: str) -> "NoReturn":
    print(f"verify-apk: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(*command: str) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        fail(f"required tool not found: {exc.filename}")
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        fail(f"command failed ({exc.returncode}): {' '.join(command)}\n{output.strip()}")
    return result.stdout


def parse_components(manifest_tree: str) -> set[str]:
    lines = manifest_tree.splitlines()
    components: set[str] = set()
    component_tags = {"activity", "activity-alias", "receiver", "service", "provider"}
    for index, line in enumerate(lines):
        match = re.match(r"\s*E: ([\w-]+) \(", line)
        if not match or match.group(1) not in component_tags:
            continue
        for attribute in lines[index + 1 :]:
            if re.match(r"\s*E: ", attribute):
                break
            name = re.search(r'android:name[^=]*="([^"]+)"', attribute)
            if name:
                components.add(name.group(1))
                break
    return components


def verify_elf_page_size(readelf: Path, apk: Path, abi: str, entry: str) -> None:
    with zipfile.ZipFile(apk) as archive, tempfile.NamedTemporaryFile() as extracted:
        extracted.write(archive.read(entry))
        extracted.flush()
        output = run(str(readelf), "-lW", extracted.name)

    alignments = []
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] == "LOAD":
            try:
                alignments.append(int(fields[-1], 16))
            except (IndexError, ValueError):
                fail(f"cannot parse ELF LOAD alignment for {entry}: {line}")
    if not alignments:
        fail(f"no ELF LOAD segments found in {entry}")
    if min(alignments) < 0x4000:
        fail(f"{entry} has a LOAD segment below 16 KB alignment: {alignments}")
    print(f"  {abi}: ELF LOAD alignment >= 0x4000")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, default=DEFAULT_APK)
    parser.add_argument(
        "--sdk-root",
        type=Path,
        default=Path(__import__("os").environ.get("ANDROID_HOME", ROOT / ".local/build-tools/android-sdk")),
        help="Android SDK root (defaults to ANDROID_HOME or the pinned local SDK)",
    )
    args = parser.parse_args()
    apk = args.apk.resolve()
    sdk_root = args.sdk_root.resolve()
    build_tools = sdk_root / "build-tools/35.0.0"
    ndk = sdk_root / "ndk/27.2.12479018/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
    aapt = build_tools / "aapt"
    zipalign = build_tools / "zipalign"

    if not apk.is_file():
        fail(f"APK does not exist: {apk}")
    for tool in (aapt, zipalign, ndk):
        if not tool.is_file():
            fail(f"required tool is missing: {tool}; run scripts/setup-build.sh first")

    badging = run(str(aapt), "dump", "badging", str(apk))
    expected_badging = {
        "package": rf"package: name='{re.escape(EXPECTED_PACKAGE)}'",
        "versionCode": rf"versionCode='{EXPECTED_VERSION_CODE}'",
        "versionName": rf"versionName='{re.escape(EXPECTED_VERSION_NAME)}'",
        "minSdkVersion": rf"sdkVersion:'{EXPECTED_MIN_SDK}'",
        "targetSdkVersion": rf"targetSdkVersion:'{EXPECTED_TARGET_SDK}'",
    }
    for label, pattern in expected_badging.items():
        if not re.search(pattern, badging):
            fail(f"APK {label} does not match expected value; expected {pattern}")
    print("APK identity: package/version/min/target match the release configuration")

    manifest = run(str(aapt), "dump", "xmltree", str(apk), "AndroidManifest.xml")
    if re.search(r"android\.test\.(?:base|runner|mock)", manifest):
        fail("production manifest contains an android.test optional library")
    components = parse_components(manifest)
    if not EXPECTED_COMPONENTS.issubset(components):
        missing = sorted(EXPECTED_COMPONENTS - components)
        fail(f"manifest is missing explicit legacy-namespace components: {missing}")
    if any(not name.startswith("org.pocketworkstation.pckeyboard.") for name in components):
        fail(f"manifest has a component outside the retained Java namespace: {sorted(components)}")
    print(f"Manifest components: {len(components)} use the retained Java namespace")

    alignment = run(str(zipalign), "-c", "-P", "16", "-v", "4", str(apk))
    if "Verification succesful" not in alignment:
        fail("zipalign did not confirm 16 KB native library alignment")
    print("ZIP alignment: uncompressed native libraries are 16 KB aligned")

    try:
        with zipfile.ZipFile(apk) as archive:
            libraries = {
                name.split("/")[1]: name
                for name in archive.namelist()
                if name.startswith("lib/") and name.endswith(".so") and name.count("/") == 2
            }
    except zipfile.BadZipFile as exc:
        fail(f"APK is not a valid ZIP archive: {exc}")
    missing_abis = EXPECTED_ABIS - set(libraries)
    if missing_abis:
        fail(f"APK is missing native libraries for ABIs: {sorted(missing_abis)}")
    print(f"Native ABIs: {', '.join(sorted(libraries))}")
    for abi in ("arm64-v8a", "x86_64"):
        verify_elf_page_size(ndk, apk, abi, libraries[abi])
    print("Artifact verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
