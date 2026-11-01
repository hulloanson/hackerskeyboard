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

`build.sh` accepts `assembleRelease` (default), `assembleDebug`, or `bundleRelease`. The release APK is unsigned and is produced at `app/build/outputs/apk/release/app-release-unsigned.apk`; signing is handled by `scripts/signing.py` with the persistent app key.

Gradle runs without a persistent daemon, with at most two workers and parallel project execution disabled. The bootstrap JVM is bounded to a 256 MiB heap and 256 MiB metaspace; the Gradle build JVM is bounded to a 1 GiB heap, 384 MiB metaspace, and two reported processors. The app contains native C++ code, so limiting Gradle workers also limits concurrent native build work.

## Resource reserves and enforcement

The setup environment was inspected before choosing reserves. It reported about 5.8 GiB total host memory and 5.0 GiB `MemAvailable`. The system filesystem had about 4.2 GiB free, while `/workspace` had about 44 GiB free and 26.9 million free inodes. Build tools and all large build state therefore go under `/workspace/.local/build-tools`; temporary files are directed there as well.

Every setup, download, extraction, and build phase starts through `scripts/resource_guard.py`. Signing and repository scripts use the same interface. The fixed reserves are:

| Resource | Safety reserve |
| --- | ---: |
| Host `MemAvailable` | 2 GiB |
| Effective cgroup memory available | 2 GiB |
| System filesystem free bytes | 1 GiB |
| System filesystem free inodes | 50,000 |
| Each other monitored filesystem free bytes | 10 GiB |
| Each other monitored filesystem free inodes | 100,000 |

The guard checks `/` and every requested output/cache path, deduplicates paths on the same filesystem, and samples memory, bytes, and inodes before a phase and every two seconds while it runs. It reads host `MemAvailable` plus cgroup v1 or v2 limits and current use, including ancestor limits. Finite cgroup `memory.max`/`memory.high` or v1 limits reduce effective availability; a readable `max` limit means the cgroup has no configured finite ceiling, so host `MemAvailable` bounds it. A VM with a cgroup filesystem but no memory controller reports that host availability is the only available memory metric. If a mounted controller exposes resource files but required limit or usage data is missing or malformed, the guard fails closed.

The process supervisor starts each command as a new process group. If a reserve is breached, or the supervisor receives `SIGINT`, `SIGTERM`, or `SIGHUP`, it sends `SIGTERM` to the full group, waits up to ten seconds, then sends `SIGKILL`. It also keeps watching descendants if the command's leader exits before them. Resource-intensive commands should stay in that process group; avoid nested guard sessions or tools that daemonize outside it. Run checks or wrap one phase with:

```sh
python3 scripts/resource_guard.py check --path /workspace --path /workspace/.local/build-tools
python3 scripts/resource_guard.py run --label 'one build phase' --path /workspace --path /workspace/.local/build-tools -- command argument...
```

The unit tests cover cgroup v1/v2 parsing, finite and unlimited limits, fail-closed behavior for unreadable values, reserve thresholds, child-tree termination after a breach, and wrapper signal forwarding:

```sh
python3 -m unittest -v tests/test_resource_guard.py
```

