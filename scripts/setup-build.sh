#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
GUARD="$ROOT/scripts/resource_guard.py"
DOWNLOADS="$HK_BUILD_ROOT/downloads"

fail() {
  echo "setup-build: $*" >&2
  exit 1
}

[[ "$(uname -s)" == Linux ]] || fail "this toolchain setup currently supports Linux only"
[[ "$(uname -m)" == x86_64 ]] || fail "this toolchain setup currently supports x86_64 only"
for tool in python3 curl sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || fail "required host utility '$tool' is missing"
done

mkdir -p "$DOWNLOADS" "$HK_BUILD_ROOT/tmp" "$HK_ANDROID_SDK_ROOT" "$HK_BUILD_ROOT/gradle"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"

download_checked() {
  local name="$1" url="$2" expected_sha256="$3"
  local destination="$DOWNLOADS/$name" partial="$DOWNLOADS/$name.part"
  if [[ -f "$destination" ]]; then
    printf '%s  %s\n' "$expected_sha256" "$destination" | sha256sum --check --status \
      || fail "cached archive checksum failed: $destination (remove it and rerun)"
    echo "Using verified cached archive: $name"
    return
  fi
  echo "Downloading $name"
  python3 "$GUARD" run --label "download $name" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    curl --fail --location --retry 3 --retry-delay 2 --retry-all-errors --continue-at - \
      --output "$partial" "$url"
  if ! printf '%s  %s\n' "$expected_sha256" "$partial" | sha256sum --check --status; then
    rm -f -- "$partial"
    fail "download checksum failed for $name"
  fi
  mv -- "$partial" "$destination"
}

install_jdk() {
  local archive="$DOWNLOADS/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20_8.tar.gz"
  local version_file="$HK_JAVA_HOME/release"
  if [[ -x "$HK_JAVA_HOME/bin/java" && -f "$version_file" ]] && grep -q 'JAVA_VERSION="17.0.20"' "$version_file"; then
    echo "Temurin JDK 17.0.20+8 is already installed"
    return
  fi
  [[ ! -e "$HK_JAVA_HOME" ]] || fail "incomplete JDK directory exists: $HK_JAVA_HOME (remove it and rerun)"
  download_checked \
    "OpenJDK17U-jdk_x64_linux_hotspot_17.0.20_8.tar.gz" \
    "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20%2B8/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20_8.tar.gz" \
    "be7668bc030d578b83d6d5ef9221d6d6729bbbca8cf94a7d52e16ac68b5a5a35"
  local stage
  stage="$(mktemp -d "$HK_BUILD_ROOT/tmp/jdk.XXXXXX")"
  python3 "$GUARD" run --label "extract Temurin JDK" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    tar -xzf "$archive" --strip-components=1 -C "$stage"
  grep -q 'JAVA_VERSION="17.0.20"' "$stage/release" || fail "JDK archive has an unexpected version"
  mv -- "$stage" "$HK_JAVA_HOME"
}

install_gradle() {
  local archive="$DOWNLOADS/gradle-8.9-bin.zip"
  if [[ -x "$HK_GRADLE_BIN" ]]; then
    echo "Gradle 8.9 is already installed"
    return
  fi
  [[ ! -e "$HK_GRADLE_HOME" ]] || fail "incomplete Gradle directory exists: $HK_GRADLE_HOME (remove it and rerun)"
  download_checked \
    "gradle-8.9-bin.zip" \
    "https://services.gradle.org/distributions/gradle-8.9-bin.zip" \
    "d725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab"
  local stage
  stage="$(mktemp -d "$HK_BUILD_ROOT/tmp/gradle.XXXXXX")"
  python3 "$GUARD" run --label "extract Gradle 8.9" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    python3 "$ROOT/scripts/unpack_zip.py" "$archive" "$stage"
  [[ -x "$stage/gradle-8.9/bin/gradle" ]] || fail "Gradle archive has an unexpected layout"
  mv -- "$stage/gradle-8.9" "$HK_GRADLE_HOME"
}

install_command_line_tools() {
  local destination="$HK_ANDROID_SDK_ROOT/cmdline-tools/15859902"
  if [[ -x "$destination/bin/sdkmanager" ]]; then
    echo "Android command-line tools revision 15859902 is already installed"
    return
  fi
  [[ ! -e "$destination" ]] || fail "incomplete Android command-line tools directory exists: $destination (remove it and rerun)"
  download_checked \
    "commandlinetools-linux-15859902_latest.zip" \
    "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip" \
    "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"
  local stage
  stage="$(mktemp -d "$HK_BUILD_ROOT/tmp/android-cli.XXXXXX")"
  python3 "$GUARD" run --label "extract Android command-line tools" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    python3 "$ROOT/scripts/unpack_zip.py" "$DOWNLOADS/commandlinetools-linux-15859902_latest.zip" "$stage"
  [[ -x "$stage/cmdline-tools/bin/sdkmanager" ]] || fail "Android command-line tools archive has an unexpected layout"
  mkdir -p "$(dirname -- "$destination")"
  mv -- "$stage/cmdline-tools" "$destination"
}

install_jdk
install_gradle
install_command_line_tools

# Source again now that the pinned JDK and SDK manager have been installed.
source "$ROOT/scripts/env.sh"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"

python3 "$GUARD" run --label "accept Android SDK licenses" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
  bash -c 'set +o pipefail; yes | "$1" --sdk_root="$2" --licenses; status=${PIPESTATUS[1]}; exit "$status"' _ "$HK_SDKMANAGER" "$HK_ANDROID_SDK_ROOT"

python3 "$GUARD" run --label "install pinned Android SDK packages" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
  "$HK_SDKMANAGER" --sdk_root="$HK_ANDROID_SDK_ROOT" --channel=0 --install \
    "platforms;android-35" \
    "build-tools;35.0.0" \
    "ndk;27.2.12479018" \
    "cmake;3.22.1"

[[ -f "$HK_ANDROID_SDK_ROOT/platforms/android-35/android.jar" ]] || fail "Android API 35 platform is missing after installation"
[[ -x "$HK_ANDROID_SDK_ROOT/build-tools/35.0.0/apksigner" ]] || fail "Android build-tools 35.0.0 are missing after installation"
[[ -f "$HK_ANDROID_SDK_ROOT/ndk/27.2.12479018/build/cmake/android.toolchain.cmake" ]] || fail "Android NDK 27.2.12479018 is missing after installation"
[[ -x "$HK_ANDROID_SDK_ROOT/cmake/3.22.1/bin/cmake" ]] || fail "Android CMake 3.22.1 is missing after installation"

python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"
echo "Build toolchain ready. Source scripts/env.sh in bash to use the pinned tools."
