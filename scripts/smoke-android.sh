#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
GUARD="$ROOT/scripts/resource_guard.py"
SMOKE_API_LEVEL="${HK_SMOKE_API_LEVEL:-35}"
AVD_NAME="hackerskeyboard-api${SMOKE_API_LEVEL}"
EMULATOR_PORT="${HK_EMULATOR_PORT:-5558}"
ADB_PORT="${HK_ADB_SERVER_PORT:-5038}"
SERIAL="emulator-${EMULATOR_PORT}"
SDK_ROOT="$HK_ANDROID_SDK_ROOT"
EMULATOR="$SDK_ROOT/emulator/emulator"
ADB_BIN="$SDK_ROOT/platform-tools/adb"

fail() {
  echo "smoke-android: $*" >&2
  exit 1
}

if [[ "${1:-}" != "--guarded" ]]; then
  "$ROOT/scripts/build.sh" assembleDebug
  "$ROOT/scripts/build.sh" assembleDebugAndroidTest
  exec python3 "$GUARD" run --label "API $SMOKE_API_LEVEL instrumentation" \
    --path "$ROOT" --path "$HK_BUILD_ROOT" -- bash "$0" --guarded
fi
shift

[[ -x "$JAVA_HOME/bin/java" ]] || fail "pinned JDK is missing; run scripts/setup-build.sh first"
[[ -x "$HK_GRADLE_BIN" ]] || fail "pinned Gradle is missing; run scripts/setup-build.sh first"
[[ -x "$EMULATOR" ]] || fail "optional emulator is missing; run scripts/setup-emulator.sh first"
[[ -x "$ADB_BIN" ]] || fail "Android platform-tools are missing; run scripts/setup-emulator.sh first"
[[ -d "$ANDROID_USER_HOME/avd/$AVD_NAME.avd" ]] \
  || fail "AVD $AVD_NAME is missing; run HK_SMOKE_API_LEVEL=$SMOKE_API_LEVEL scripts/setup-emulator.sh first"
mkdir -p "$TMPDIR"
export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
export ANDROID_ADB_SERVER_PORT="$ADB_PORT"
ADB=("$ADB_BIN" -P "$ADB_PORT")
export GRADLE_OPTS="-Xmx256m -XX:MaxMetaspaceSize=256m -XX:ActiveProcessorCount=2"

EMULATOR_PID=""
ADB_SERVER_PID=""

cleanup() {
  set +e
  if [[ -n "$ADB_SERVER_PID" && -n "$EMULATOR_PID" ]] \
      && kill -0 "$ADB_SERVER_PID" 2>/dev/null; then
  "${ADB[@]}" -s "$SERIAL" emu kill >/dev/null 2>&1
    for _ in $(seq 1 15); do
      kill -0 "$EMULATOR_PID" 2>/dev/null || break
      sleep 1
    done
  fi
  if [[ -n "$EMULATOR_PID" ]] && kill -0 "$EMULATOR_PID" 2>/dev/null; then
    kill "$EMULATOR_PID" 2>/dev/null
  fi
  if [[ -n "$ADB_SERVER_PID" ]] && kill -0 "$ADB_SERVER_PID" 2>/dev/null; then
    kill "$ADB_SERVER_PID" 2>/dev/null
  fi
  [[ -z "$EMULATOR_PID" ]] || wait "$EMULATOR_PID" 2>/dev/null
  [[ -z "$ADB_SERVER_PID" ]] || wait "$ADB_SERVER_PID" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

"${ADB[@]}" nodaemon server >"$TMPDIR/adb-server-api${SMOKE_API_LEVEL}.log" 2>&1 &
ADB_SERVER_PID=$!

server_ready=false
for _ in $(seq 1 30); do
  if ! kill -0 "$ADB_SERVER_PID" 2>/dev/null; then
    cat "$TMPDIR/adb-server-api${SMOKE_API_LEVEL}.log" >&2
    fail "foreground ADB server exited during startup"
  fi
  if ( : >"/dev/tcp/127.0.0.1/$ADB_PORT" ) 2>/dev/null; then
    server_ready=true
    break
  fi
  sleep 1
done
[[ "$server_ready" == true ]] || fail "foreground ADB server did not become ready"

if [[ -r /dev/kvm && -w /dev/kvm ]]; then
  ACCELERATION=on
else
  ACCELERATION=off
  echo "KVM is not accessible; using slow software emulation for this smoke run." >&2
fi

"$EMULATOR" -avd "$AVD_NAME" -port "$EMULATOR_PORT" -lowram -memory 2048 -cores 4 \
  -no-window -no-audio -no-boot-anim -no-snapshot -accel "$ACCELERATION" \
  -gpu swiftshader_indirect >"$TMPDIR/emulator-api${SMOKE_API_LEVEL}.log" 2>&1 &
EMULATOR_PID=$!

device_ready=false
for _ in $(seq 1 120); do
  if ! kill -0 "$EMULATOR_PID" 2>/dev/null; then
    cat "$TMPDIR/emulator-api${SMOKE_API_LEVEL}.log" >&2
    fail "emulator exited before becoming ready"
  fi
  if "${ADB[@]}" devices | awk -v serial="$SERIAL" '$1 == serial && $2 == "device" { found = 1 } END { exit !found }'; then
    device_ready=true
    break
  fi
  sleep 2
done
[[ "$device_ready" == true ]] || fail "emulator did not connect to ADB"

booted=false
for _ in $(seq 1 180); do
  if [[ "$("${ADB[@]}" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then
    booted=true
    break
  fi
  sleep 2
done
[[ "$booted" == true ]] || fail "emulator did not finish booting"
[[ "$("${ADB[@]}" -s "$SERIAL" shell getprop ro.build.version.sdk | tr -d '\r')" == "$SMOKE_API_LEVEL" ]] \
  || fail "emulator is not running Android API $SMOKE_API_LEVEL"
"${ADB[@]}" -s "$SERIAL" shell settings put secure show_ime_with_hard_keyboard 1

APP_APK="$ROOT/app/build/outputs/apk/debug/app-debug.apk"
TEST_APK="$ROOT/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
[[ -f "$APP_APK" ]] || fail "debug APK was not produced by the guarded build phase"
[[ -f "$TEST_APK" ]] || fail "instrumentation APK was not produced by the guarded build phase"

"${ADB[@]}" -s "$SERIAL" install -r "$APP_APK"
"${ADB[@]}" -s "$SERIAL" install -r "$TEST_APK"
"${ADB[@]}" -s "$SERIAL" shell am instrument -w -r \
  com.hulloanson.test/android.test.InstrumentationTestRunner \
  | tee "$TMPDIR/instrumentation-api${SMOKE_API_LEVEL}.log"
if grep -Eq 'FAILURES!!!|INSTRUMENTATION_FAILED|INSTRUMENTATION_ABORTED' \
    "$TMPDIR/instrumentation-api${SMOKE_API_LEVEL}.log"; then
  fail "instrumentation reported a test failure; see $TMPDIR/instrumentation-api${SMOKE_API_LEVEL}.log"
fi
grep -Eq 'OK \([0-9]+ tests?\)' "$TMPDIR/instrumentation-api${SMOKE_API_LEVEL}.log" \
  || fail "instrumentation did not report a passing test summary; see $TMPDIR/instrumentation-api${SMOKE_API_LEVEL}.log"

IME_ID="com.hulloanson/org.pocketworkstation.pckeyboard.LatinIME"
"${ADB[@]}" -s "$SERIAL" shell am force-stop com.hulloanson
"${ADB[@]}" -s "$SERIAL" shell \
  "run-as com.hulloanson sh -c 'mkdir -p shared_prefs && cat > shared_prefs/com.hulloanson_preferences.xml'" <<'EOF'
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="selected_languages">en_DV</string>
    <string name="input_language">en_DV</string>
</map>
EOF
"${ADB[@]}" -s "$SERIAL" shell \
  "run-as com.hulloanson cat shared_prefs/com.hulloanson_preferences.xml" \
  >"$TMPDIR/dvorak-preferences-api${SMOKE_API_LEVEL}.xml"
grep -F '<string name="selected_languages">en_DV</string>' \
  "$TMPDIR/dvorak-preferences-api${SMOKE_API_LEVEL}.xml" >/dev/null \
  || fail "the en_DV language preference was not stored"
"${ADB[@]}" -s "$SERIAL" logcat -c
"${ADB[@]}" -s "$SERIAL" shell ime enable "$IME_ID"
"${ADB[@]}" -s "$SERIAL" shell ime set "$IME_ID"
"${ADB[@]}" -s "$SERIAL" shell am start -n \
  com.hulloanson/org.pocketworkstation.pckeyboard.Main >/dev/null
sleep 2

tap_main_edit_text() {
  local hierarchy="$TMPDIR/main-window-api${SMOKE_API_LEVEL}.xml"
  local bounds
  "${ADB[@]}" -s "$SERIAL" shell uiautomator dump /sdcard/main-window.xml >/dev/null
  "${ADB[@]}" -s "$SERIAL" shell cat /sdcard/main-window.xml \
    | tr -d '\r' >"$hierarchy"
  bounds="$(python3 - "$hierarchy" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET

tree = ET.parse(sys.argv[1])
for node in tree.getroot().iter("node"):
    if node.get("resource-id") != "com.hulloanson:id/main_setup_edit_test":
        continue
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
else:
    raise SystemExit("main test EditText was not found in the UI hierarchy")
PY
  )" || fail "could not find Main's test input field"
  read -r local_x local_y <<<"$bounds"
  "${ADB[@]}" -s "$SERIAL" shell input tap "$local_x" "$local_y"
  sleep 3
}

tap_main_edit_text
"${ADB[@]}" -s "$SERIAL" shell dumpsys input_method >"$TMPDIR/input-method-portrait-api${SMOKE_API_LEVEL}.txt"

assert_ime_active_and_shown() {
  local dump="$1"
  python3 - "$dump" "$IME_ID" "$SMOKE_API_LEVEL" <<'PY'
import re
import sys

dump = open(sys.argv[1], encoding="utf-8", errors="replace").read()
ime_id = sys.argv[2]
active_fields = re.findall(r"(?m)^\s*(?:mCurId|mCurMethodId|mCurMethod)=([^\r\n]+)", dump)
if not any(ime_id in value for value in active_fields):
    raise SystemExit(f"IME id not found in current-method fields: {active_fields}")

shown_fields = dict(re.findall(
    r"(?im)^\s*(mInputShown|mShowRequested|mIsInputViewShown|mImeWindowVis)=([^\r\n]+)",
    dump,
))
visibility_field = next(
    (name for name in ("mInputShown", "mIsInputViewShown", "mImeWindowVis", "mShowRequested")
     if name in shown_fields),
    None,
)
if visibility_field:
    value = shown_fields[visibility_field].strip().lower()
    is_shown = bool(re.search(r"(?:0x)?[1-9a-f][0-9a-f]*", value, re.I)) \
        if visibility_field == "mImeWindowVis" else value in {"true", "1"}
    if not is_shown:
        raise SystemExit(f"IME is current but input window is not shown: {shown_fields}")
else:
    print("API%d dump exposes no input-shown flag; visual screenshot review is required." % int(sys.argv[3]))
PY
}

assert_ime_active_and_shown "$TMPDIR/input-method-portrait-api${SMOKE_API_LEVEL}.txt" \
  || fail "fork IME is not current and visible in portrait; see $TMPDIR/input-method-portrait-api${SMOKE_API_LEVEL}.txt"
"${ADB[@]}" -s "$SERIAL" exec-out screencap -p >"$TMPDIR/hk-ime-portrait-api${SMOKE_API_LEVEL}.png"

"${ADB[@]}" -s "$SERIAL" shell settings put system accelerometer_rotation 0
"${ADB[@]}" -s "$SERIAL" shell settings put system user_rotation 1
sleep 5
tap_main_edit_text
"${ADB[@]}" -s "$SERIAL" shell dumpsys input_method >"$TMPDIR/input-method-landscape-api${SMOKE_API_LEVEL}.txt"
assert_ime_active_and_shown "$TMPDIR/input-method-landscape-api${SMOKE_API_LEVEL}.txt" \
  || fail "fork IME is not current and visible in landscape; see $TMPDIR/input-method-landscape-api${SMOKE_API_LEVEL}.txt"
"${ADB[@]}" -s "$SERIAL" exec-out screencap -p >"$TMPDIR/hk-ime-landscape-api${SMOKE_API_LEVEL}.png"

"${ADB[@]}" -s "$SERIAL" logcat -d -s PCKeyboard:I AndroidRuntime:E \
  >"$TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log"
grep -F 'onCreate(), os.version=' "$TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log" >/dev/null \
  || fail "LatinIME service did not reach onCreate; see $TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log"
if grep -F 'FATAL EXCEPTION' "$TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log" >/dev/null; then
  cat "$TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log" >&2
  fail "the IME service crashed; see $TMPDIR/ime-runtime-api${SMOKE_API_LEVEL}.log"
fi

echo "API $SMOKE_API_LEVEL instrumentation and IME service smoke passed. Screenshots and logs: $TMPDIR"
