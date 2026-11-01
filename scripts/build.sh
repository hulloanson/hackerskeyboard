#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
GUARD="$ROOT/scripts/resource_guard.py"

fail() {
  echo "build: $*" >&2
  exit 1
}

[[ $# -le 1 ]] || fail "usage: scripts/build.sh [assembleRelease|assembleDebug|assembleDebugAndroidTest|bundleRelease]"
task="${1:-assembleRelease}"
case "$task" in
  assembleRelease|assembleDebug|assembleDebugAndroidTest|bundleRelease) ;;
  *) fail "unsupported build task: $task" ;;
esac

[[ -x "$JAVA_HOME/bin/java" ]] || fail "pinned JDK is missing; run scripts/setup-build.sh first"
[[ -x "$HK_GRADLE_BIN" ]] || fail "pinned Gradle is missing; run scripts/setup-build.sh first"
[[ -f "$HK_ANDROID_SDK_ROOT/platforms/android-35/android.jar" ]] || fail "Android API 35 is missing; run scripts/setup-build.sh first"

mkdir -p "$TMPDIR" "$GRADLE_USER_HOME" "$ANDROID_USER_HOME" "$HK_BUILD_ROOT/logs"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"
cd "$ROOT"

export LC_ALL=C
export TZ=UTC
export GRADLE_OPTS="-Xmx256m -XX:MaxMetaspaceSize=256m -XX:ActiveProcessorCount=2"
LOG_FILE="$HK_BUILD_ROOT/logs/build-$task.log"

python3 "$GUARD" run --label "Gradle $task" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
  "$HK_GRADLE_BIN" --no-daemon --max-workers=2 --no-parallel --console=plain --stacktrace \
    "-Dorg.gradle.jvmargs=-Xmx1024m -XX:MaxMetaspaceSize=384m -XX:ActiveProcessorCount=2" \
    "$task" 2>&1 | tee "$LOG_FILE"

echo "Build complete: $task (log: $LOG_FILE)"
