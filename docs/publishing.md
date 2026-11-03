# Signing and F-Droid publishing

## Status and decisions

The persistent app and repository signing keys have been generated, verified, and backed up outside both repositories. Release `1.41.3-hulloanson-2` (`104100302`, keyboard background under system bars, keys clear of them) is signed and published in repository commit `9f5c922`; `1.41.3-hulloanson-1` (`104100301`) was published in `a916b4f`; `1.41.3-hulloanson` (`1041003`, settings export/import) was published in `ed584f7`; the previous release `1.41.2-hulloanson` (`1041002`) was published in `79afb30`. Hosted v1/v2 indexes, APK hash, and both signer fingerprints have passed HTTPS verification. The lead agent cloned and bootstrapped repo_publish; it is ignored by the main repository. Keep it out of main-repository commits.

The repository is a signed binary F-Droid repository. Gradle produces an unsigned APK; scripts/signing.py signs it with the persistent app key; fdroidserver update builds and signs the repository indexes with the separate F-Droid key. Only the repo/ output directory is copied into repo_publish/ for Git publication. The private F-Droid workspace, its config.yml, metadata, keystore, and password files stay outside both Git repositories.

The selected public endpoint is:

    https://raw.githubusercontent.com/hulloanson/fdroid_repo/main/repo/

The public raw.githubusercontent.com host returned HTTP 200 for the bootstrap README before F-Droid indexes existed. The complete F-Droid `/repo/` endpoint passed hosted index and APK verification after publication. GitHub Pages is not required. scripts/github_publish.py has optional Pages setup commands for a future hosting change, plus the HTTPS repository verifier used for the selected endpoint.

F-Droid metadata uses AutoUpdateMode: None and UpdateCheckMode: None because releases are added from locally built APKs. These fields disable F-Droid server-side automatic build/update checks; they do not disable F-Droid client updates. Each release must have a higher version code and must keep the same app signing key.

## Resource limits

Every resource-intensive install, signing, indexing, clone, and push operation runs under scripts/resource_guard.py. It checks before starting and every two seconds while a child process group runs. A breached or unreadable reserve stops the child process group. The current reserves are:

| Resource | Required reserve |
| --- | ---: |
| Host available memory | 2 GiB |
| Effective cgroup available memory, when the controller is exposed | 2 GiB |
| Root filesystem free space and inodes | 1 GiB and 50,000 inodes |
| Each other target filesystem | 2 GiB and 100,000 inodes |

The F-Droid Python environment and Android build tools are kept under the ignored .local/ directory. Signing material and the F-Droid working repository use the external paths below.

## Persistent keys and backups

The reviewed scripts/signing.py init command created two separate RSA 4096-bit PKCS#12 keys, with separate random passwords:

| Purpose | Keystore | Alias |
| --- | --- | --- |
| Android app updates | app-release.p12 | app-release |
| F-Droid repository indexes | fdroid-index.p12 | fdroid-repo |

By default, the keystores and their mode 0600 password files are stored in ~/.local/share/hackerskeyboard/signing/, whose directory mode is 0700. HK_SIGNING_HOME can select another external directory. The script refuses to store key material inside either repository and refuses to replace an existing or partial key set.

The script verifies and copies both keystores and password files to ~/hackerskeyboard-signing-backup/, mode 0700, with files mode 0600. HK_SIGNING_BACKUP_DIR can select another external backup directory. It also writes ~/hackerskeyboard-signing-backup.tar.gz (override with HK_SIGNING_ARCHIVE), mode 0600, containing exactly those four files with mode 0600, and verifies every archived byte against the verified directory copy. On a VM, these paths are still on the VM's storage: copy the archive to separate encrypted or access-controlled storage before the VM can be deleted. Keep this backup available for future updates; losing the app key prevents in-place updates, and losing the repository key changes the repository identity for F-Droid clients.

The first key creation (already completed for this checkout) was:

    source scripts/env.sh
    python3 scripts/signing.py init
    python3 scripts/signing.py fingerprints

The generated public SHA-256 certificate fingerprints are:

| Key | Fingerprint |
| --- | --- |
| App signing | `d320313989dae04c48f1b15bdc0a9780662c04ba3aba3f09e053b852ae8ebefe` |
| F-Droid repository signing | `77c9d776441463e0a9dc693db49caf81fa553f6fdf6f057ee38914a5e4fbd3bb` |

Do not generate replacement keys for a later APK or index update. scripts/signing.py backup verifies and refreshes the external directory and access-controlled archive from the existing primary keys.

## Tool setup and private F-Droid workspace

Use the Android SDK installed by the build setup. Signing and APK inspection use Android build-tools 35.0.0. Set ANDROID_HOME or ANDROID_SDK_ROOT to that SDK.

After build setup, source `scripts/env.sh` in each release shell to select the pinned JDK and Android SDK tools.

Install the pinned F-Droid server tools in an external Python virtual environment:

    python3 scripts/fdroid_repo.py setup

This installs fdroidserver 2.4.5 under .local/fdroidserver-2.4.5/ by default. Override the path with HK_FDROID_VENV. The current VM has Python's venv and ensurepip modules. On another host, install the distribution's python3-venv package first if either module is missing; the guarded setup script stops with an error rather than proceeding without a usable virtual environment.

Initialize the private workspace after keys exist:

    python3 scripts/fdroid_repo.py init

The workspace defaults to ~/.local/share/hackerskeyboard/signing/fdroid-work/; HK_FDROID_WORK_HOME can override it. Its config.yml has mode 0600 and reads the F-Droid key password from the process environment. It is never copied to repo_publish/. The URL defaults to the raw HTTPS endpoint above; FDROID_REPO_URL can override it with another HTTPS URL ending in /repo. `make_current_version_link` is disabled so F-Droid does not create current-version APK symlinks.

Initialization copies the app's MDPI Esc key icon to the private workspace and writes a category definition for `System`; F-Droid indexing then includes a real repository icon and a recognized category instead of a generated placeholder.

Validate the installed fdroidserver, metadata, environment-based secret loading, persistent key, icon, and PKCS#12 signing path without creating repository indexes or printing passwords:

    source scripts/env.sh
    python3 scripts/fdroid_repo.py doctor

This runs F-Droid's metadata linter and a temporary JAR-signing check through the installed F-Droid signing tools. It also creates a legacy-signed fixture and confirms that the modern F-Droid apksigner path replaces that signature with a SHA-256 JAR signature, verified by the default strict JDK verifier and the repository fingerprint. It does not change JVM security policy.

The generated app metadata pins AllowedAPKSigningKeys to the app certificate fingerprint, so F-Droid indexing fails closed if a release was signed with another app key. It records the version name and version code from each APK. fdroidserver 2.4.5 initially signs index-v1.jar (and optional index.jar) with a legacy algorithm for older clients. After `fdroid update`, the guarded release script re-signs those JARs with fdroidserver's modern apksigner path (`use_old_algs=False`), producing SHA-256 signatures compatible with the supported Android 26 and newer clients. It leaves the already-modern entry.jar signature intact. The script then uses the default strict JDK verifier and checks that every signed index has exactly one signer matching the persistent repository key; it verifies the v2 index digest from the signed entry as well. No JVM security-policy overrides are used.

## Upstream tracking and versioning

The fork tracks `upstream` (https://github.com/klausw/hackerskeyboard, branch `master`). Before each release, run `git fetch upstream` and rebase the fork's commits onto `upstream/master`, then force-push `master` with `--force-with-lease`. As of 2026-11-03, `upstream/master` is `9202d9d` (v1.41.1, versionCode 1041001).

The fork keeps its own base version, currently 1.41.3, and appends `-hulloanson-N`. Each fork release bumps only N; the base changes only when a rebase brings in an upstream version greater than the current base (for example upstream 1.42 replaces base 1.41.3), and N then restarts at 1. The versionCode base is then upstream's versionCode. The versionCode is the base versionCode times 100 plus N (1.41.3 gives base 1041003, so `1.41.3-hulloanson-1` is 104100301). This stays above the earlier releases 1041002 (`1.41.2-hulloanson`) and 1041003 (`1.41.3-hulloanson`). N must stay below 100.

## Release and publishing sequence

1. Build an unsigned release APK with the guarded build workflow.

2. Sign and verify it:

       python3 scripts/signing.py sign-apk path/to/app-release-unsigned.apk

   The signed file is written outside the repositories to ~/hackerskeyboard-releases/com.hulloanson_<versionCode>.apk by default. HK_RELEASE_DIR can override the output directory. The script checks the package ID, refuses to overwrite an existing release, signs with apksigner, verifies the signature, and compares its certificate fingerprint with the persistent app key.

3. Generate and verify the F-Droid indexes, then stage only public repo/ output into the cloned repository:

       python3 scripts/fdroid_repo.py stage "$HOME/hackerskeyboard-releases/com.hulloanson_<versionCode>.apk"

   This runs fdroid update and modern index re-signing under the resource guard, checks the strict signatures and v2 digest, and copies the APKs and generated indexes into repo_publish/repo/. The source work directory remains private and outside Git.

4. Review the staged files and publish them through the configured Git credential helper or askpass. The push token is kept as `GH_TOKEN` in the main repository's ignored, mode 0600 `.env`; supply it through a credential helper that reads the file, never in a remote URL:

       python3 scripts/fdroid_git.py publish

   The script updates the public repository URL and signing fingerprint, then commits and pushes only the allowlisted repo/, README.md, and index.html files on main; it does not embed a token in the Git remote. The initial repository landing-page commit can be created with python3 scripts/fdroid_git.py bootstrap.

5. Print the public fingerprints and verify the HTTPS release:

       python3 scripts/signing.py fingerprints
       python3 scripts/github_publish.py verify \
         --repo-url https://raw.githubusercontent.com/hulloanson/fdroid_repo/main/repo/ \
         --fingerprint REPOSITORY_SHA256 \
         --app-fingerprint APP_SHA256 \
         --apk-sha256 RELEASE_APK_SHA256

   Get RELEASE_APK_SHA256 from sha256sum on the signed APK. The verifier checks HTTPS transport, the v1 index and signed v2 entry, the v2 digest, the latest package's APK size and SHA-256, and its apksigner certificate. It adds a cache-busting query to its checks and polls for up to five minutes until the latest indexed APK hash matches the expected release; do not announce the release until this succeeds.

For a later release, rebuild and sign using the same app key, run fdroid_repo.py stage to regenerate the index with the same repository key, then publish the updated repo/. Keep previous APKs in the F-Droid workspace and clone so existing index references continue to work.

## F-Droid client update behavior

The F-Droid project's client 1.19 release notes describe session-installer background updates on Android 12 or later after the first install or first update. This depends on the installed client, Android's installer/session rules, and the user's settings; increasing this app's minSdkVersion alone does not guarantee unattended updates. See [F-Droid's 1.19 update notes](https://f-droid.org/2024/02/01/twif.html) and Android's [PackageInstaller.SessionParams#setRequireUserAction](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int)).

This fork uses the distinct Android application ID `com.hulloanson`, so it can be installed alongside the official Hacker's Keyboard app. Android treats them as separate apps with separate data and settings.

## Optional GitHub Pages API helper

The selected raw endpoint does not need Pages configuration. If hosting later moves to GitHub Pages, scripts/github_publish.py pages-enable configures branch main at /, and pages-wait reports the HTTPS site URL. API writes need a token from HK_GITHUB_TOKEN_FILE (a mode 0600 file outside both repositories) or HK_GITHUB_TOKEN; the script never accepts tokens in arguments or logs them. GitHub documents Pages: write and Administration: write for configuring a site with a fine-grained token. See [GitHub's Pages REST API permissions](https://docs.github.com/en/rest/pages/pages).

The APK signer uses the PKCS#12 store password for its private key automatically. Passing the same one-line password file for both password options makes apksigner consume that file twice and reach EOF; the script supplies the store password once. V4 sidecar generation is disabled because F-Droid distributes the APK itself.

Published APK: `com.hulloanson_104100302.apk`, 2,488,871 bytes, SHA-256 `862746f1d5984126c38d5d75254dd533ea06da9ccc74ff5866a3568635c4af84`. Previous APK: `com.hulloanson_104100301.apk`, 2,488,871 bytes, SHA-256 `b9765222338745ea1b34288b1a0bb25f6153046902db05ba5b3f9d0e86b3735a`. Previous APK: `com.hulloanson_1041003.apk`, 2,488,871 bytes, SHA-256 `3bf36f813af2cd3596ec01b993b118eabcfe0b2301d1fded7fbe8931bebeb939`. Previous APK: `com.hulloanson_1041002.apk`, 2,468,391 bytes, SHA-256 `0463876bfc5dab86f4d33fec0cfa5d0012106d50112097a846c4cfb63adb95bf`.
