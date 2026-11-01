# App identity, SDK, and keyboard behavior

## Package identity and install behavior

The fork's Android application ID is exactly `com.hulloanson`. Its Android/Java
namespace remains `org.pocketworkstation.pckeyboard`, which keeps the existing
Java source tree, custom view names, resource references, and JNI class lookup
stable. Manifest component class names use that full namespace explicitly;
`app/src/main/res/xml/method.xml` likewise names the settings activity by its
Java class name.

The fork is therefore a separate installed package from the original
`org.pocketworkstation.pckeyboard` app. Both can be installed at once, with
separate app data and app signing identity. Updating this fork still requires
keeping its own app signing key stable. The custom action names used by the
manifest, preferences, and notification now use the `com.hulloanson` prefix.
The established third-party dictionary plugin action names remain unchanged;
the manifest declares those query intents so package visibility on Android 11+
does not hide compatible dictionary packs. There are no content providers in
the manifest, so no provider authority requires a new value.

## SDK and build toolchain

The app uses `minSdkVersion 26`, `compileSdkVersion 35`, and
`targetSdkVersion 35`. API 26 is the selected compatibility floor for a
maintainable modern build. It does not enable unattended updates: that behavior
is governed by Android's installer API, the installed Android version, and
which installer owns the update.

The pinned build versions are Android Gradle Plugin 8.7.3, Gradle 8.9, JDK 17,
Android SDK platform/build tools 35/35.0.0, NDK 27.2.12479018, and CMake 3.22.1.
[AGP 8.7's compatibility table](https://developer.android.com/build/releases/agp-8-7-0-release-notes)
requires Gradle 8.9 and JDK 17 and supports API 35. The project retains the
last Android Support Library release (`support-compat` 28.0.0) for its legacy
notification helper instead of combining the SDK/toolchain update with a
whole-app AndroidX migration. Java source and bytecode target 1.8. Gradle is
limited to a 1536 MB heap and two workers; the guarded build scripts remain the
only authorized way to run resource-intensive build operations.

The native library is built with NDK flexible page-size support enabled for
16 KB page-size devices. The standard Android NDK ABI set is left available to
Gradle. This configuration still needs confirmation in a guarded build and
artifact inspection.

`targetSdkVersion 35` also means Android 15 enforces edge-to-edge behavior for
the app's activity windows. This narrow compatibility pass does not retrofit
all legacy activity layouts for system-bar insets; review the Main, settings,
and input-method windows on current Android devices before release. See
[Android's edge-to-edge guidance](https://developer.android.com/develop/ui/views/layout/edge-to-edge).

## Unattended F-Droid updates

F-Droid announced unattended updates in client 1.19 for Android 12 and later,
subject to Android's package installer rules. Android's current
[`SessionParams#setRequireUserAction`](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int))
documentation lists a target SDK threshold of 34 for Android 16 (API 36), so
target 35 clears that threshold. The documented conditions also require the
installer to be the update owner or installer of record, as applicable, and
to hold `UPDATE_PACKAGES_WITHOUT_USER_ACTION`. Android can still return a
pending-user-action result. These rules can change in later Android releases.

The `minSdkVersion` is not part of those unattended-update conditions. Target
35 improves compatibility with newer F-Droid update flows, but neither this
target level nor this app build can guarantee an unattended update on every
device or install history. See [F-Droid's announcement](https://f-droid.org/2024/02/01/twif.html)
and the [Android package installer API reference](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int)).

## Runtime compatibility for the raised target

Manifest components with intent filters declare `android:exported`. The IME
service and app/settings entry points are available to the system; preference
subscreens remain internal. Dynamic receiver registration uses the API 33
export flags on Android 13+; package-change and ringer-mode system broadcasts
are exported, while the app's notification receiver is not. Notification
pending intents are immutable and scoped to this app. The optional permanent
keyboard notification declares `POST_NOTIFICATIONS` and requests it when the
user enables that preference on Android 13+.

## English Dvorak portrait Tab behavior

The selected input locale controls the qualified `kbd_qwerty.xml` resource;
English Dvorak is `en-DV` and is defined in
`app/src/main/res/xml-en-rDV/kbd_qwerty.xml`. Tab is present only in the two
web-entry bottom rows: the regular web-entry row and its settings-key variant.
Normal, URL, email, and IM bottom rows have no Tab key in this layout. QWERTY
and other locale layouts are not changed.

`LatinKeyboard` enables the filter only for the main `kbd_qwerty` resource,
locale `en-DV`, and portrait orientation. `Keyboard` then treats that Tab key
as a code-less key. Its existing parser merges a code-less key's width into
the previous key, which is Space in both affected rows. This keeps each row's
declared total width at 100 percent and makes Space wider where Tab was. In
landscape the original Tab key remains. Orientation changes reload the
keyboard cache, so a portrait/landscape transition rebuilds the keys using the
new orientation.

The runnable static regression check is
`python3 -m unittest discover -s tests -p 'test_dvorak_portrait_tab_layout.py'`. It checks that Tab
occurs only in both web-entry modes for QWERTY and Dvorak, that Dvorak row
widths total 100 percent before and after merging Tab into Space, and that the
source layout contains both affected web-entry rows. The Android
instrumentation matrix is
`app/src/androidTest/java/org/pocketworkstation/pckeyboard/DvorakPortraitTabInstrumentationTest.java`;
it constructs the real `LatinKeyboard` for English Dvorak in both orientations
and for QWERTY in both orientations, then checks Tab visibility across all
alphabet modes plus Space width in both web-entry modes.
Run `scripts/setup-emulator.sh` once and then `scripts/smoke-android.sh` for the
guarded API 35 run. It builds the test APKs first, then starts the emulator and
foreground ADB server under a separate guard, runs the matrix, enables the
debug IME with `en-DV`, and captures portrait/landscape screenshots under
`.local/build-tools/tmp`. When KVM is unavailable to the current user, the
script falls back to software emulation without changing device permissions.

## Validation still required

- Run the guarded Gradle build and resolve compiler/resource errors before
  signing or publication.
- Run the guarded API 35 emulator instrumentation matrix once the toolchain
  has provisioned an emulator and system image (`scripts/setup-emulator.sh`
  and `scripts/smoke-android.sh`).
- Inspect the merged manifest and APK package ID; verify native libraries for
  expected ABIs and 16 KB page-size compatibility.
- Exercise Dvorak portrait and landscape, both web-entry modes with and
  without the settings key, and QWERTY on an emulator/device. Also rotate while
  the keyboard is open to verify cache recreation.
- Check activity and IME presentation with Android 15 edge-to-edge behavior,
  and the notification permission flow on Android 13+.
- Verify the installed fork and original app coexist, then verify F-Droid
  updates from a fork-signed APK using the persistent fork key and confirmed
  repository configuration.
