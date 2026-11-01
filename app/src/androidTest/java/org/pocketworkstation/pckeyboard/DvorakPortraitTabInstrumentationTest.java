package org.pocketworkstation.pckeyboard;

import android.content.Context;
import android.content.res.Configuration;
import android.test.InstrumentationTestCase;

import java.util.Locale;

/** Exercises the real keyboard XML parser on portrait and landscape resource contexts. */
public class DvorakPortraitTabInstrumentationTest extends InstrumentationTestCase {
    private Locale mOriginalInputLocale;

    @Override
    protected void setUp() throws Exception {
        super.setUp();
        mOriginalInputLocale = LatinIME.sKeyboardSettings.inputLocale;
    }

    @Override
    protected void tearDown() throws Exception {
        LatinIME.sKeyboardSettings.inputLocale = mOriginalInputLocale;
        super.tearDown();
    }

    public void testDvorakTabVisibilityForAllAlphabetModes() {
        int[] modes = {
                KeyboardSwitcher.KEYBOARDMODE_NORMAL,
                KeyboardSwitcher.KEYBOARDMODE_URL,
                KeyboardSwitcher.KEYBOARDMODE_EMAIL,
                KeyboardSwitcher.KEYBOARDMODE_IM,
                KeyboardSwitcher.KEYBOARDMODE_WEB,
                KeyboardSwitcher.KEYBOARDMODE_NORMAL_WITH_SETTINGS_KEY,
                KeyboardSwitcher.KEYBOARDMODE_URL_WITH_SETTINGS_KEY,
                KeyboardSwitcher.KEYBOARDMODE_EMAIL_WITH_SETTINGS_KEY,
                KeyboardSwitcher.KEYBOARDMODE_IM_WITH_SETTINGS_KEY,
                KeyboardSwitcher.KEYBOARDMODE_WEB_WITH_SETTINGS_KEY
        };
        int tabCode = getInstrumentation().getTargetContext().getResources()
                .getInteger(R.integer.key_tab);

        for (int mode : modes) {
            boolean isWebEntry = mode == KeyboardSwitcher.KEYBOARDMODE_WEB
                    || mode == KeyboardSwitcher.KEYBOARDMODE_WEB_WITH_SETTINGS_KEY;
            LatinKeyboard portrait = createKeyboard("DV",
                    Configuration.ORIENTATION_PORTRAIT, mode);
            assertEquals("Dvorak portrait mode " + mode, 0,
                    countKeys(portrait, tabCode));

            LatinKeyboard landscape = createKeyboard("DV",
                    Configuration.ORIENTATION_LANDSCAPE, mode);
            assertEquals("Dvorak landscape mode " + mode,
                    isWebEntry ? 1 : 0, countKeys(landscape, tabCode));
        }
    }

    public void testPortraitDvorakSpaceAbsorbsTabWidthInBothWebModes() {
        int[] webModes = {
                KeyboardSwitcher.KEYBOARDMODE_WEB,
                KeyboardSwitcher.KEYBOARDMODE_WEB_WITH_SETTINGS_KEY
        };
        int tabCode = getInstrumentation().getTargetContext().getResources()
                .getInteger(R.integer.key_tab);
        int spaceCode = getInstrumentation().getTargetContext().getResources()
                .getInteger(R.integer.key_space);

        for (int mode : webModes) {
            LatinKeyboard portrait = createKeyboard("DV",
                    Configuration.ORIENTATION_PORTRAIT, mode);
            LatinKeyboard landscape = createKeyboard("DV",
                    Configuration.ORIENTATION_LANDSCAPE, mode);
            Keyboard.Key portraitSpace = findKey(portrait, spaceCode);
            Keyboard.Key landscapeSpace = findKey(landscape, spaceCode);
            Keyboard.Key landscapeTab = findKey(landscape, tabCode);

            assertNotNull("portrait Space", portraitSpace);
            assertNotNull("landscape Space", landscapeSpace);
            assertNotNull("landscape Tab", landscapeTab);
            assertNull("portrait Tab", findKey(portrait, tabCode));
            assertEquals("hidden Tab width is added to Space", landscapeSpace.width
                    + landscapeTab.width, portraitSpace.width);
        }
    }

    public void testQwertyPortraitKeepsTabInBothWebModes() {
        int[] webModes = {
                KeyboardSwitcher.KEYBOARDMODE_WEB,
                KeyboardSwitcher.KEYBOARDMODE_WEB_WITH_SETTINGS_KEY
        };
        int tabCode = getInstrumentation().getTargetContext().getResources()
                .getInteger(R.integer.key_tab);

        for (int mode : webModes) {
            LatinKeyboard qwerty = createKeyboard("US",
                    Configuration.ORIENTATION_PORTRAIT, mode);
            assertEquals("QWERTY portrait mode " + mode, 1,
                    countKeys(qwerty, tabCode));
        }
    }

    private LatinKeyboard createKeyboard(String region, int orientation, int mode) {
        Locale locale = new Locale("en", region);
        LatinIME.sKeyboardSettings.inputLocale = locale;

        Context target = getInstrumentation().getTargetContext();
        Configuration configuration = new Configuration(
                target.getResources().getConfiguration());
        configuration.setLocale(locale);
        configuration.orientation = orientation;
        Context configured = target.createConfigurationContext(configuration);

        return new LatinKeyboard(configured, R.xml.kbd_qwerty, mode, 0.0f);
    }

    private static int countKeys(LatinKeyboard keyboard, int code) {
        int count = 0;
        for (Keyboard.Key key : keyboard.getKeys()) {
            if (key.codes != null && key.codes.length > 0 && key.codes[0] == code) {
                count++;
            }
        }
        return count;
    }

    private static Keyboard.Key findKey(LatinKeyboard keyboard, int code) {
        for (Keyboard.Key key : keyboard.getKeys()) {
            if (key.codes != null && key.codes.length > 0 && key.codes[0] == code) {
                return key;
            }
        }
        return null;
    }
}
