# Hacker's Keyboard — hulloanson fork

This fork uses application ID `com.hulloanson`, supports Android 8.0 and newer, and targets Android 15. It removes the Tab key beside Space for English Dvorak in portrait orientation and gives that space to the Space key. Landscape and other layouts retain Tab. Release `1.41.2-hulloanson` is built, signed, and published. Hosted indexes, APK integrity, and signing fingerprints are verified; device validation is in progress. Settings can be exported to and imported from a JSON file (Settings > Backup); see the app design doc.

See [build scripts and resource guards](docs/build.md), [app design](docs/app-design.md), and the [development status](docs/development.md).

## Install this fork

In F-Droid, open **Settings > Repositories**, add the following URL, enable the repository, and refresh:

```text
https://raw.githubusercontent.com/hulloanson/fdroid_repo/main/repo/?fingerprint=77c9d776441463e0a9dc693db49caf81fa553f6fdf6f057ee38914a5e4fbd3bb
```

Install **Hacker's Keyboard** with package ID `com.hulloanson`, enable the keyboard in Android settings, and select English Dvorak in the keyboard's language settings. This fork can coexist with the original package. F-Droid background updates depend on a compatible client, Android 12 or newer, installer ownership, and the user's update settings.

See [the public repository](https://github.com/hulloanson/fdroid_repo#readme) and [signing and publishing instructions](docs/publishing.md).

## Upstream overview (historical) ##

**WARNING:** *This is a rather ancient project that was originally developed back in 2011 based on the Android 2.3 (Gingerbread) AOSP keyboard. While it still works as-is for many users, it would need some major rewrites to work with newer APIs, and some features such as language switching or popup keys don't work right on modern Android systems. I'm not currently planning on significant updates, and it's possible that it will stop working on modern devices or will no longer be updateable via the Google Play store due to minimum API level requirements. Play Store requires targeting API level 29 (Android 10), while the code was written for API level 9 (Android 2.3) from 2011.*

Are you missing the key layout you're used to from your computer when using an Android device? This software keyboard has separate number keys, punctuation in the usual places, and arrow keys. It is based on the AOSP Gingerbread soft keyboard, so it supports multitouch for the modifier keys.

This keyboard is especially useful if you use ConnectBot for SSH access. It provides working Tab/Ctrl/Esc keys, and the arrow keys are essential for devices such as the Xoom tablet or Nexus S that don't have a trackball or D-Pad.

The supported keyboard layouts include Armenian (Հայերեն), Arabic (العربية),
British (en\_GB), Bulgarian (български език), Czech (Čeština), Danish (dansk),
Carpalx English (language "en-CX"), Dvorak English (language "en-DV"), English
(QWERTY), Finnish (Suomi), French (Français, AZERTY), German (Deutsch, QWERTZ),
German Neo2 (Deutsch, language "de-NE"),
Greek (ελληνικά), Hebrew (עברית), Hungarian (Magyar), Italian (Italiano), Lao
(ພາສາລາວ), Norwegian (Norsk bokmål), Persian (فارسی), Portuguese (Português),
Romanian (Română), Russian (Русский), Russian phonetic (Русский, ru-rPH),
Serbian (Српски), Slovak (Slovenčina), Slovenian
(Slovenščina)/Bosnian/Croatian/Latin Serbian, Spanish (Español, Español
Latinoamérica), Swedish (Svenska), Tamil (தமிழ்), Thai (ไทย), Turkish (Türkçe),
and Ukrainian (українська мова).

To install, get **[Hacker's
Keyboard](https://play.google.com/store/apps/details?id=org.pocketworkstation.pckeyboard)**
from the Play Store, plus optional [dictionary
packs](https://play.google.com/store/apps/developer?id=Klaus+Weidner).

## Additional resources ##

See the **[Release Notes](https://github.com/klausw/hackerskeyboard/wiki/ReleaseNotes)** for changes in the Play Store released versions.

Having problems? See the **[User's Guide](https://github.com/klausw/hackerskeyboard/wiki/UsersGuide)** and **[FAQ](https://github.com/klausw/hackerskeyboard/wiki/FrequentlyAskedQuestions)**, and check the [issue tracker](https://github.com/klausw/hackerskeyboard/issues) for known bugs or filing new ones.

Comments, requests, or contributions? Join the [discussion group](http://groups.google.com/group/hackerskeyboard/).

Application developers: see [the page about keyboard support in applications](https://github.com/klausw/hackerskeyboard/wiki/KeyboardSupportInApplications) if you want to enable the additional keys in your Android application, the same method also works for hardware USB or Bluetooth keyboards.

![hk-5row-en-s.png](hk-5row-en-s.png)

## Development

Source changes, guarded build scripts, signing, and F-Droid publication are implemented. See the development guide for validation coverage. See the [development guide](docs/development.md) for repository context, decisions, security requirements, and the outstanding checklist. Contributors should also read [AGENTS.md](AGENTS.md).
