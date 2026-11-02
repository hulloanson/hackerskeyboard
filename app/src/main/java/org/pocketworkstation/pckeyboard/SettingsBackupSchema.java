package org.pocketworkstation.pckeyboard;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

import org.xmlpull.v1.XmlPullParser;
import org.xmlpull.v1.XmlPullParserException;

import android.content.Context;
import android.content.res.XmlResourceParser;

/**
 * Builds the map of exportable preference keys to backup types from the
 * preference XML definitions, so it cannot drift from the settings screens.
 */
final class SettingsBackupSchema {
    private static final String ANDROID_NS = "http://schemas.android.com/apk/res/android";

    private static final int[] PREFERENCE_XML = {
        R.xml.prefs, R.xml.prefs_view, R.xml.prefs_feedback, R.xml.prefs_actions,
        R.xml.prefs_for_debug,
    };

    private SettingsBackupSchema() {
    }

    static Map<String, String> load(Context context) throws IOException, XmlPullParserException {
        Map<String, String> schema = new LinkedHashMap<String, String>();
        for (int resId : PREFERENCE_XML) {
            XmlResourceParser parser = context.getResources().getXml(resId);
            try {
                int event;
                while ((event = parser.next()) != XmlPullParser.END_DOCUMENT) {
                    if (event != XmlPullParser.START_TAG) continue;
                    String key = parser.getAttributeValue(ANDROID_NS, "key");
                    if (key == null) continue;
                    String tag = parser.getName();
                    // Containers and the display-only "Preference" rows hold no value.
                    if (tag.equals("PreferenceScreen") || tag.equals("PreferenceCategory")
                            || tag.equals("Preference")) {
                        continue;
                    }
                    schema.put(key, tag.equals("CheckBoxPreference")
                            ? SettingsBackup.TYPE_BOOLEAN : SettingsBackup.TYPE_STRING);
                }
            } finally {
                parser.close();
            }
        }
        // Enabled input languages: a user setting that has no preference widget.
        schema.put(LatinIME.PREF_SELECTED_LANGUAGES, SettingsBackup.TYPE_STRING);
        return schema;
    }
}
