#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
GUARD="$ROOT/scripts/resource_guard.py"
AVD_NAME="hackerskeyboard-api35"
IMAGE="system-images;android-35;google_apis;x86_64"

fail() {
  echo "setup-emulator: $*" >&2
  exit 1
}

[[ -x "$HK_SDKMANAGER" ]] || fail "pinned Android command-line tools are missing; run scripts/setup-build.sh first"
mkdir -p "$TMPDIR" "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"

python3 "$GUARD" run --label "install API 35 emulator packages" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
  "$HK_SDKMANAGER" --sdk_root="$HK_ANDROID_SDK_ROOT" --channel=0 --install \
    "platform-tools" "emulator" "$IMAGE"

AVDMANAGER="$HK_ANDROID_SDK_ROOT/cmdline-tools/15859902/bin/avdmanager"
AVD_PATH="$ANDROID_AVD_HOME/$AVD_NAME.avd"
[[ -x "$AVDMANAGER" ]] || fail "avdmanager is missing after SDK installation"
if [[ -d "$AVD_PATH" ]]; then
  echo "AVD $AVD_NAME is already present"
else
  python3 "$GUARD" run --label "create API 35 emulator AVD" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    "$AVDMANAGER" create avd --name "$AVD_NAME" --package "$IMAGE" --device pixel_2 --force
fi

[[ -x "$HK_ANDROID_SDK_ROOT/emulator/emulator" ]] || fail "emulator binary is missing after SDK installation"
[[ -x "$HK_ANDROID_SDK_ROOT/platform-tools/adb" ]] || fail "adb is missing after SDK installation"
[[ -f "$AVD_PATH/config.ini" ]] || fail "AVD config is missing after creation"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"
echo "API 35 smoke-test AVD ready: $AVD_NAME (system image $IMAGE)"
