import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res-auto}"


def keyboard_rows(path):
    root = ET.parse(path).getroot()
    return [row for row in root.findall("Row")]


def key_code(key):
    return key.get(ANDROID + "codes")


def width_percent(value):
    match = re.fullmatch(r"([0-9.]+)%p", value)
    if not match:
        raise AssertionError("expected a percentage key width, got {!r}".format(value))
    return float(match.group(1))


class DvorakPortraitTabLayoutTest(unittest.TestCase):
    def setUp(self):
        self.dvorak = ROOT / "app/src/main/res/xml-en-rDV/kbd_qwerty.xml"
        self.qwerty = ROOT / "app/src/main/res/xml-en/kbd_qwerty.xml"

    def test_tab_is_limited_to_web_rows_in_both_layouts(self):
        for path in (self.dvorak, self.qwerty):
            rows = keyboard_rows(path)
            tab_modes = [
                row.get(ANDROID + "keyboardMode")
                for row in rows
                if any(key_code(key) == "@integer/key_tab" for key in row.findall("Key"))
            ]
            self.assertEqual(
                ["@+id/mode_webentry", "@+id/mode_webentry_with_settings_key"],
                tab_modes,
                path,
            )

    def test_dvorak_web_rows_keep_full_width_when_tab_merges_into_space(self):
        rows = keyboard_rows(self.dvorak)
        web_rows = [
            row
            for row in rows
            if row.get(ANDROID + "keyboardMode")
            in ("@+id/mode_webentry", "@+id/mode_webentry_with_settings_key")
        ]
        self.assertEqual(2, len(web_rows))

        for row in web_rows:
            default_width = row.get(ANDROID + "keyWidth")
            keys = row.findall("Key")
            widths = [
                width_percent(key.get(ANDROID + "keyWidth", default_width))
                for key in keys
            ]
            self.assertAlmostEqual(100.0, sum(widths), places=4)

            tab_index = next(
                index for index, key in enumerate(keys)
                if key_code(key) == "@integer/key_tab"
            )
            self.assertEqual("@integer/key_space", key_code(keys[tab_index - 1]))

            merged_widths = widths[:]
            merged_widths[tab_index - 1] += merged_widths.pop(tab_index)
            self.assertAlmostEqual(100.0, sum(merged_widths), places=4)
            self.assertAlmostEqual(40.0, merged_widths[tab_index - 1], places=4)

if __name__ == "__main__":
    unittest.main()
