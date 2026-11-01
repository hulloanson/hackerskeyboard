#!/usr/bin/env bash
# Source this file from the repository root to select the pinned local toolchain.

if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "scripts/env.sh must be sourced by bash" >&2
  return 2
fi

HK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
HK_BUILD_ROOT="${HK_BUILD_ROOT:-$HK_ROOT/.local/build-tools}"
HK_ANDROID_SDK_ROOT="$HK_BUILD_ROOT/android-sdk"
HK_JAVA_HOME="$HK_BUILD_ROOT/jdk-17.0.20+8"
HK_GRADLE_HOME="$HK_BUILD_ROOT/gradle/gradle-8.9"
HK_GRADLE_BIN="$HK_GRADLE_HOME/bin/gradle"
HK_SDKMANAGER="$HK_ANDROID_SDK_ROOT/cmdline-tools/15859902/bin/sdkmanager"

export HK_ROOT HK_BUILD_ROOT HK_ANDROID_SDK_ROOT HK_JAVA_HOME HK_GRADLE_HOME HK_GRADLE_BIN HK_SDKMANAGER
export JAVA_HOME="$HK_JAVA_HOME"
export ANDROID_HOME="$HK_ANDROID_SDK_ROOT"
export ANDROID_SDK_ROOT="$HK_ANDROID_SDK_ROOT"
export ANDROID_USER_HOME="$HK_BUILD_ROOT/android-user-home"
export ANDROID_SDK_HOME="$HK_BUILD_ROOT/android-user-home"
export GRADLE_USER_HOME="$HK_BUILD_ROOT/gradle-user-home"
export TMPDIR="$HK_BUILD_ROOT/tmp"
export PATH="$JAVA_HOME/bin:$HK_ANDROID_SDK_ROOT/cmdline-tools/15859902/bin:$HK_ANDROID_SDK_ROOT/platform-tools:$PATH"

