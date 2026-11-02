#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
GUARD="$ROOT/scripts/resource_guard.py"
SMOKE_API_LEVEL="${HK_SMOKE_API_LEVEL:-35}"
case "$SMOKE_API_LEVEL" in
  35)
    AVD_NAME="hackerskeyboard-api35"
    IMAGE="system-images;android-35;google_apis;x86_64"
    RAM_MIB=1536
    ;;
  34)
    AVD_NAME="hackerskeyboard-api34"
    IMAGE="system-images;android-34;default;x86_64"
    RAM_MIB=2048
    ;;
  *)
    echo "setup-emulator: HK_SMOKE_API_LEVEL must be 34 or 35 (got $SMOKE_API_LEVEL)" >&2
    exit 2
    ;;
esac
VM_HEAP="128M"

fail() {
  echo "setup-emulator: $*" >&2
  exit 1
}

[[ -x "$HK_SDKMANAGER" ]] || fail "pinned Android command-line tools are missing; run scripts/setup-build.sh first"
mkdir -p "$TMPDIR" "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"

python3 "$GUARD" run --label "install API $SMOKE_API_LEVEL emulator packages" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
  "$HK_SDKMANAGER" --sdk_root="$HK_ANDROID_SDK_ROOT" --channel=0 --install \
    "platform-tools" "emulator" "$IMAGE"

AVDMANAGER="$HK_ANDROID_SDK_ROOT/cmdline-tools/15859902/bin/avdmanager"
AVD_PATH="$ANDROID_AVD_HOME/$AVD_NAME.avd"
[[ -x "$AVDMANAGER" ]] || fail "avdmanager is missing after SDK installation"
if [[ -d "$AVD_PATH" ]]; then
  echo "AVD $AVD_NAME is already present"
else
  python3 "$GUARD" run --label "create API $SMOKE_API_LEVEL emulator AVD" --path "$ROOT" --path "$HK_BUILD_ROOT" -- \
    "$AVDMANAGER" create avd --name "$AVD_NAME" --package "$IMAGE" --device pixel_2 --force
fi

python3 - "$AVD_PATH/config.ini" "$RAM_MIB" "$VM_HEAP" <<'PY'
from pathlib import Path
import sys

config = Path(sys.argv[1])
settings = {"hw.ramSize": sys.argv[2], "vm.heapSize": sys.argv[3]}
lines = config.read_text(encoding="utf-8").splitlines()
seen = set()
updated = []
for line in lines:
    key, separator, _value = line.partition("=")
    if separator and key in settings:
        updated.append(f"{key}={settings[key]}")
        seen.add(key)
    else:
        updated.append(line)
for key, value in settings.items():
    if key not in seen:
        updated.append(f"{key}={value}")
config.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

[[ -x "$HK_ANDROID_SDK_ROOT/emulator/emulator" ]] || fail "emulator binary is missing after SDK installation"
[[ -x "$HK_ANDROID_SDK_ROOT/platform-tools/adb" ]] || fail "adb is missing after SDK installation"
[[ -f "$AVD_PATH/config.ini" ]] || fail "AVD config is missing after creation"
python3 "$GUARD" check --path "$ROOT" --path "$HK_BUILD_ROOT"
echo "API $SMOKE_API_LEVEL smoke-test AVD ready: $AVD_NAME (system image $IMAGE, requested RAM ${RAM_MIB} MiB)"
