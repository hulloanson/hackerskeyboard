# Guarded Android build environment

## Build recipe

The app build uses Android Gradle Plugin 8.7.3, Gradle 8.9, Eclipse Temurin JDK 17.0.20+8, Android API 35, Build Tools 35.0.0, NDK 27.2.12479018, and CMake 3.22.1. The app targets API 35 and supports API 26 and newer. The SDK package IDs and the archive checksums below are fixed in `scripts/setup-build.sh`.

| Component | Pinned version | Archive SHA-256 |
| --- | --- | --- |
| Temurin JDK | 17.0.20+8 | `be7668bc030d578b83d6d5ef9221d6d6729bbbca8cf94a7d52e16ac68b5a5a35` |
| Gradle binary distribution | 8.9 | `d725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab` |
| Android command-line tools | revision 15859902 | `4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583` |

The Android SDK Manager installs the pinned package IDs `platforms;android-35`, `build-tools;35.0.0`, `ndk;27.2.12479018`, and `cmake;3.22.1` from the stable channel. Android SDK archives are checked by the SDK Manager against its repository metadata. The command-line tools archive and SDK packages are provided under Google's [Android Studio download and SDK tools pages](https://developer.android.com/studio); the JDK and Gradle archives come from [Adoptium](https://github.com/adoptium/temurin17-binaries/releases) and [Gradle](https://services.gradle.org/distributions/).

The repository's legacy `gradlew` launcher refers to wrapper files that are absent from this checkout. The scripts use the verified Gradle 8.9 distribution installed under `.local/build-tools` instead.

## Setup and build

The scripts currently support Linux x86_64. Host prerequisites are Bash, Python 3, `curl`, `sha256sum`, and `tar`; they fail with a direct message if one is missing. Tool downloads, SDK components, Gradle caches, Android user state, and temporary files live in `.local/build-tools` on the workspace filesystem. The scripts do not install tools globally or use the user's default Gradle cache.

Run setup once, then source the pinned environment in Bash or use the build wrapper:

```sh
./scripts/setup-build.sh
source scripts/env.sh
./scripts/build.sh assembleRelease
```

`setup-build.sh` verifies cached archives before reuse and stores verified archives to avoid downloading them again. It installs only components that are missing. SDK license prompts are accepted by the setup command through `sdkmanager --licenses`; setup must be run in an environment where the operator is authorized to accept Google's Android SDK terms. It then installs the fixed SDK package IDs and verifies the expected files exist.

`build.sh` accepts `assembleRelease` (default), `assembleDebug`, `assembleDebugAndroidTest`, `testDebugUnitTest` (JVM unit tests under `app/src/test`), or `bundleRelease`. The release APK is unsigned and is produced at `app/build/outputs/apk/release/app-release-unsigned.apk`; signing is handled by `scripts/signing.py` with the persistent app key. Each build's complete output is saved in `.local/build-tools/logs/build-<task>.log` as well as printed to the terminal.

Emulator tooling is optional because it is not needed to produce the APK and adds a large system image. To use the API 35 Google APIs image, run `scripts/setup-emulator.sh`; it installs `platform-tools`, the stable `emulator` package, and `system-images;android-35;google_apis;x86_64`, then creates the `hackerskeyboard-api35` AVD under `.local/build-tools/android-user-home/avd` with a requested 1536 MiB RAM and 128 MiB VM heap. In this environment, emulator 37.2.13 raises the effective guest RAM to 2560 MiB even when both the AVD setting and `-memory 1536` request 1536 MiB; its generated `hardware-qemu.ini` also reports a 576 MiB VM heap despite the 128 MiB AVD setting. Software-emulated QEMU reached about 3 GiB RSS, and two API 35 starts were correctly stopped when host availability reached the 2 GiB reserve before boot completed. Keep that reserve unchanged.

For an API 34 AOSP layout smoke run, set `HK_SMOKE_API_LEVEL=34` for both setup and smoke commands. Setup installs `system-images;android-34;default;x86_64` and creates `hackerskeyboard-api34` with a requested 2048 MiB RAM and 128 MiB VM heap. By default, emulator 37.2.13 raises API 34+ guests to 2560 MiB RAM and a 576 MiB VM heap; the supported `-lowram` option removes that RAM minimum, allowing the smoke command's `-memory 2048` cap to take effect. The emulator source documents this behavior in [main-common.c](https://android.googlesource.com/platform/external/qemu/+/emu-master-dev/android/android-emu/android/main-common.c#1241). The corrected API 34 launch produced `hw.ramSize=2048` and `vm.heapSize=512` in `hardware-qemu.ini`. Verify the effective values immediately after launch. The app remains compiled and targeted for API 35; the API 34 run checks the UI and keyboard service on an earlier runtime, not API 35-only behavior. Keep the 2 GiB reserve unchanged and let the guard stop the run if host availability falls below it.

Android's SDK Manager does not offer a fixed revision selector for these package IDs, so they are latest stable at setup time; they do not affect the pinned release build toolchain. The emulator run itself must use `resource_guard.py run` with the same memory and filesystem reserves. Hardware acceleration needs host KVM support; check that `/dev/kvm` is available before starting.

Gradle runs without a persistent daemon, with at most two workers and parallel project execution disabled. The bootstrap JVM is bounded to a 256 MiB heap and 256 MiB metaspace; the Gradle build JVM is bounded to a 1 GiB heap, 384 MiB metaspace, and two reported processors. The app contains native C++ code, so limiting Gradle workers also limits concurrent native build work.

## Resource reserves and enforcement

The setup environment was inspected before choosing reserves. It reported about 5.8 GiB total host memory and 5.0 GiB `MemAvailable`. The system filesystem had about 4.2 GiB free, while `/workspace` had about 44 GiB free and 26.9 million free inodes. Build tools and all large build state therefore go under `/workspace/.local/build-tools`; temporary files are directed there as well. On 2026-11-02 host RAM was raised to 8 GiB (about 7.6 GiB `MemTotal`); the reserves below are unchanged. The same day, the user lowered the reserve for non-system filesystems from 10 GiB to 2 GiB because another project on the shared `/workspace` host volume left about 3.8 GiB free.

Every setup, download, extraction, and build phase starts through `scripts/resource_guard.py`. Signing and repository scripts use the same interface. The fixed reserves are:

| Resource | Safety reserve |
| --- | ---: |
| Host `MemAvailable` | 2 GiB |
| Effective cgroup memory available | 2 GiB |
| System filesystem free bytes | 1 GiB |
| System filesystem free inodes | 50,000 |
| Each other monitored filesystem free bytes | 2 GiB |
| Each other monitored filesystem free inodes | 100,000 |

The guard checks `/` and every requested output/cache path, deduplicates paths on the same filesystem, and samples memory, bytes, and inodes before a phase and every two seconds while it runs. It reads host `MemAvailable` plus cgroup v1 or v2 limits and current use, including ancestor limits. Finite cgroup `memory.max`/`memory.high` or v1 limits reduce effective availability; a readable `max` limit means the cgroup has no configured finite ceiling, so host `MemAvailable` bounds it. A VM with a cgroup filesystem but no memory controller reports that host availability is the only available memory metric. If a mounted controller exposes resource files but required limit or usage data is missing or malformed, the guard fails closed.

The process supervisor starts each command as a new process group and marks itself as a Linux child subreaper. It tracks descendant PIDs together with process start times through `/proc`, including processes that create a new session or survive their original parent. If a reserve is breached, or the supervisor receives `SIGINT`, `SIGTERM`, or `SIGHUP`, it sends `SIGTERM` to the child group and tracked descendants, waits up to ten seconds, then sends `SIGKILL`. If the main command exits while a detached helper remains alive, the guard gives it five seconds to exit before cleaning it up and preserving the main command's exit status. It reaps adopted descendants before returning. If `/proc` or child-subreaper support is unavailable, it refuses to start the command. Resource-intensive phases should still use one guard wrapper; avoid nested guard sessions. Run checks or wrap one phase with:

```sh
python3 scripts/resource_guard.py check --path /workspace --path /workspace/.local/build-tools
python3 scripts/resource_guard.py run --label 'one build phase' --path /workspace --path /workspace/.local/build-tools -- command argument...
```

The unit tests cover cgroup v1/v2 parsing, finite and unlimited limits, fail-closed behavior for unreadable values, reserve thresholds, child-tree termination after a breach (including a double-forked `setsid` daemon), and wrapper signal forwarding:

```sh
python3 -m unittest -v tests/test_resource_guard.py
```
