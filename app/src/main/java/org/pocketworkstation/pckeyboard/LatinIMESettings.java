/*
 * Copyright (C) 2008 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not
 * use this file except in compliance with the License. You may obtain a copy of
 * the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
 * License for the specific language governing permissions and limitations under
 * the License.
 */

package org.pocketworkstation.pckeyboard;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

import android.Manifest;
import android.app.Dialog;
import android.app.backup.BackupManager;
import android.content.DialogInterface;
import android.content.Intent;
import android.net.Uri;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.content.res.Resources;
import android.os.Build;
import android.os.Bundle;
import android.preference.CheckBoxPreference;
import android.preference.ListPreference;
import android.preference.Preference;
import android.preference.PreferenceActivity;
import android.preference.PreferenceGroup;
import android.text.AutoText;
import android.text.InputType;
import android.util.Log;
import android.widget.Toast;

public class LatinIMESettings extends PreferenceActivity
        implements SharedPreferences.OnSharedPreferenceChangeListener,
        DialogInterface.OnDismissListener {

    private static final String QUICK_FIXES_KEY = "quick_fixes";
    private static final String PREDICTION_SETTINGS_KEY = "prediction_settings";
    private static final String VOICE_SETTINGS_KEY = "voice_mode";
    /* package */ static final String PREF_SETTINGS_KEY = "settings_key";
    static final String INPUT_CONNECTION_INFO = "input_connection_info";    

    private static final String TAG = "LatinIMESettings";

    // Dialog ids
    private static final int VOICE_INPUT_CONFIRM_DIALOG = 0;
    private static final int REQUEST_POST_NOTIFICATIONS = 1;
    private static final int REQUEST_EXPORT_SETTINGS = 2;
    private static final int REQUEST_IMPORT_SETTINGS = 3;
    private static final String EXPORT_SETTINGS_KEY = "export_settings";
    private static final String IMPORT_SETTINGS_KEY = "import_settings";
    private static final String SETTINGS_MIME_TYPE = "application/json";
    private static final String SETTINGS_FILE_NAME = "hackerskeyboard-settings.json";

    private CheckBoxPreference mQuickFixes;
    private CheckBoxPreference mKeyboardNotificationPreference;
    private ListPreference mVoicePreference;
    private ListPreference mSettingsKeyPreference;
    private ListPreference mKeyboardModePortraitPreference;
    private ListPreference mKeyboardModeLandscapePreference;
    private Preference mInputConnectionInfo;
    private Preference mLabelVersion;

    private boolean mVoiceOn;

    private boolean mOkClicked = false;
    private String mVoiceModeOff;

    @Override
    protected void onCreate(Bundle icicle) {
        super.onCreate(icicle);
        addPreferencesFromResource(R.xml.prefs);
        mQuickFixes = (CheckBoxPreference) findPreference(QUICK_FIXES_KEY);
        mKeyboardNotificationPreference = (CheckBoxPreference) findPreference(
                LatinIME.PREF_KEYBOARD_NOTIFICATION);
        mKeyboardNotificationPreference.setOnPreferenceChangeListener(
                new Preference.OnPreferenceChangeListener() {
                    @Override
                    public boolean onPreferenceChange(Preference preference, Object newValue) {
                        if (Boolean.TRUE.equals(newValue)
                                && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                                        != PackageManager.PERMISSION_GRANTED) {
                            requestPermissions(new String[] {
                                    Manifest.permission.POST_NOTIFICATIONS
                            }, REQUEST_POST_NOTIFICATIONS);
                            return false;
                        }
                        return true;
                    }
                });
        mVoicePreference = (ListPreference) findPreference(VOICE_SETTINGS_KEY);
        mSettingsKeyPreference = (ListPreference) findPreference(PREF_SETTINGS_KEY);
        mInputConnectionInfo = (Preference) findPreference(INPUT_CONNECTION_INFO);
        mLabelVersion = (Preference) findPreference("label_version");


        // TODO(klausw): remove these when no longer needed
        mKeyboardModePortraitPreference = (ListPreference) findPreference("pref_keyboard_mode_portrait");
        mKeyboardModeLandscapePreference = (ListPreference) findPreference("pref_keyboard_mode_landscape");
        
        findPreference(EXPORT_SETTINGS_KEY).setOnPreferenceClickListener(
                new Preference.OnPreferenceClickListener() {
                    @Override
                    public boolean onPreferenceClick(Preference preference) {
                        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                        intent.addCategory(Intent.CATEGORY_OPENABLE);
                        intent.setType(SETTINGS_MIME_TYPE);
                        intent.putExtra(Intent.EXTRA_TITLE, SETTINGS_FILE_NAME);
                        startActivityForResult(intent, REQUEST_EXPORT_SETTINGS);
                        return true;
                    }
                });
        findPreference(IMPORT_SETTINGS_KEY).setOnPreferenceClickListener(
                new Preference.OnPreferenceClickListener() {
                    @Override
                    public boolean onPreferenceClick(Preference preference) {
                        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                        intent.addCategory(Intent.CATEGORY_OPENABLE);
                        intent.setType(SETTINGS_MIME_TYPE);
                        startActivityForResult(intent, REQUEST_IMPORT_SETTINGS);
                        return true;
                    }
                });

        SharedPreferences prefs = getPreferenceManager().getSharedPreferences();
        prefs.registerOnSharedPreferenceChangeListener(this);

        mVoiceModeOff = getString(R.string.voice_mode_off);
        mVoiceOn = !(prefs.getString(VOICE_SETTINGS_KEY, mVoiceModeOff).equals(mVoiceModeOff));
    }

    @Override
    protected void onResume() {
        super.onResume();
        int autoTextSize = AutoText.getSize(getListView());
        if (autoTextSize < 1) {
            ((PreferenceGroup) findPreference(PREDICTION_SETTINGS_KEY))
                    .removePreference(mQuickFixes);
        }
        
        Log.i(TAG, "compactModeEnabled=" + LatinIME.sKeyboardSettings.compactModeEnabled);
        if (!LatinIME.sKeyboardSettings.compactModeEnabled) {
            CharSequence[] oldEntries = mKeyboardModePortraitPreference.getEntries();
            CharSequence[] oldValues = mKeyboardModePortraitPreference.getEntryValues();
            
            if (oldEntries.length > 2) {
                CharSequence[] newEntries = new CharSequence[] { oldEntries[0], oldEntries[2] };
                CharSequence[] newValues = new CharSequence[] { oldValues[0], oldValues[2] };
                mKeyboardModePortraitPreference.setEntries(newEntries);
                mKeyboardModePortraitPreference.setEntryValues(newValues);
                mKeyboardModeLandscapePreference.setEntries(newEntries);
                mKeyboardModeLandscapePreference.setEntryValues(newValues);
            }
        }
        
        updateSummaries();

        String version = "";
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), PackageManager.GET_SIGNATURES);
            version = info.versionName;
            boolean isOfficial = false;
            for (Signature sig : info.signatures) {
                byte[] b = sig.toByteArray();
                int out = 0;
                for (int i = 0; i < b.length; ++i) {
                    int pos = i % 4;
                    out ^= b[i] << (pos * 4);
                }
                if (out == -466825) {
                    isOfficial = true;
                }
                //version += " [" + Integer.toHexString(out) + "]";
            }
            version += isOfficial ? " official" : " custom";
        } catch (PackageManager.NameNotFoundException e) {
            Log.e(TAG, "Could not find version info.");
        }

        mLabelVersion.setSummary(version);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_POST_NOTIFICATIONS && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            mKeyboardNotificationPreference.setChecked(true);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        final Uri uri = data.getData();
        if (requestCode == REQUEST_EXPORT_SETTINGS) {
            runBackupTask(new Runnable() {
                public void run() { exportSettings(uri); }
            });
        } else if (requestCode == REQUEST_IMPORT_SETTINGS) {
            runBackupTask(new Runnable() {
                public void run() { importSettings(uri); }
            });
        }
    }

    // The document provider may be slow (for example a network-backed one), so keep
    // the file I/O off the UI thread.
    private void runBackupTask(Runnable task) {
        new Thread(task, "settings-backup").start();
    }

    private void showToast(final String message) {
        runOnUiThread(new Runnable() {
            public void run() {
                Toast.makeText(getApplicationContext(), message, Toast.LENGTH_LONG).show();
            }
        });
    }

    private void exportSettings(Uri uri) {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            Map<String, String> schema = SettingsBackupSchema.load(this);
            Map<String, ?> all = getPreferenceManager().getSharedPreferences().getAll();
            String json = SettingsBackup.serialize(all, schema, getPackageName(),
                    info.versionName,
                    Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                            ? info.getLongVersionCode() : info.versionCode);
            OutputStream out = getContentResolver().openOutputStream(uri, "wt");
            if (out == null) throw new IOException("Could not open the file");
            try {
                out.write(json.getBytes(StandardCharsets.UTF_8));
            } finally {
                out.close();
            }
            showToast(getString(R.string.export_settings_done,
                    countExportable(all, schema)));
        } catch (Exception e) {
            Log.e(TAG, "Settings export failed", e);
            showToast(getString(R.string.export_settings_failed, e.getMessage()));
        }
    }

    private static int countExportable(Map<String, ?> all, Map<String, String> schema) {
        int count = 0;
        for (Map.Entry<String, ?> e : all.entrySet()) {
            String expected = schema.get(e.getKey());
            if (expected != null && expected.equals(SettingsBackup.typeOf(e.getValue()))) {
                count++;
            }
        }
        return count;
    }

    private void importSettings(Uri uri) {
        try {
            Map<String, String> schema = SettingsBackupSchema.load(this);
            String json;
            InputStream in = getContentResolver().openInputStream(uri);
            if (in == null) throw new IOException("Could not open the file");
            try {
                json = SettingsBackup.readLimited(in);
            } finally {
                in.close();
            }
            // Whole file is validated before the first write; any error throws here.
            SettingsBackup.ImportPlan plan = SettingsBackup.parse(json, schema);
            SharedPreferences.Editor editor = getPreferenceManager().getSharedPreferences().edit();
            for (Map.Entry<String, Object> e : plan.values.entrySet()) {
                putValue(editor, e.getKey(), e.getValue());
            }
            if (!editor.commit()) throw new IOException("Could not write preferences");
            showToast(getString(R.string.import_settings_done, plan.values.size(),
                    plan.skippedUnknown));
            // LatinIME listens to the same preferences and has already been notified of
            // each changed key. Recreate this screen so every widget rereads its value.
            runOnUiThread(new Runnable() {
                public void run() {
                    if (!isFinishing() && !isDestroyed()) recreate();
                }
            });
        } catch (Exception e) {
            Log.e(TAG, "Settings import failed", e);
            showToast(getString(R.string.import_settings_failed, e.getMessage()));
        }
    }

    @SuppressWarnings("unchecked")
    private static void putValue(SharedPreferences.Editor editor, String key, Object value) {
        if (value instanceof Boolean) {
            editor.putBoolean(key, (Boolean) value);
        } else if (value instanceof Integer) {
            editor.putInt(key, (Integer) value);
        } else if (value instanceof Long) {
            editor.putLong(key, (Long) value);
        } else if (value instanceof Float) {
            editor.putFloat(key, (Float) value);
        } else if (value instanceof String) {
            editor.putString(key, (String) value);
        } else {
            editor.putStringSet(key, (Set<String>) value);
        }
    }

    @Override
    protected void onDestroy() {
        getPreferenceManager().getSharedPreferences().unregisterOnSharedPreferenceChangeListener(
                this);
        super.onDestroy();
    }

    public void onSharedPreferenceChanged(SharedPreferences prefs, String key) {
        (new BackupManager(this)).dataChanged();
        // If turning on voice input, show dialog
        if (key.equals(VOICE_SETTINGS_KEY) && !mVoiceOn) {
            if (!prefs.getString(VOICE_SETTINGS_KEY, mVoiceModeOff)
                    .equals(mVoiceModeOff)) {
                showVoiceConfirmation();
            }
        }
        mVoiceOn = !(prefs.getString(VOICE_SETTINGS_KEY, mVoiceModeOff).equals(mVoiceModeOff));
        updateVoiceModeSummary();
        updateSummaries();
    }

    static Map<Integer, String> INPUT_CLASSES = new HashMap<Integer, String>();
    static Map<Integer, String> DATETIME_VARIATIONS = new HashMap<Integer, String>();
    static Map<Integer, String> TEXT_VARIATIONS = new HashMap<Integer, String>();
    static Map<Integer, String> NUMBER_VARIATIONS = new HashMap<Integer, String>();
    static {
        INPUT_CLASSES.put(0x00000004, "DATETIME");
        INPUT_CLASSES.put(0x00000002, "NUMBER");
        INPUT_CLASSES.put(0x00000003, "PHONE");
        INPUT_CLASSES.put(0x00000001, "TEXT"); 
        INPUT_CLASSES.put(0x00000000, "NULL");
        
        DATETIME_VARIATIONS.put(0x00000010, "DATE");
        DATETIME_VARIATIONS.put(0x00000020, "TIME");

        NUMBER_VARIATIONS.put(0x00000010, "PASSWORD");

        TEXT_VARIATIONS.put(0x00000020, "EMAIL_ADDRESS");
        TEXT_VARIATIONS.put(0x00000030, "EMAIL_SUBJECT");
        TEXT_VARIATIONS.put(0x000000b0, "FILTER");
        TEXT_VARIATIONS.put(0x00000050, "LONG_MESSAGE");
        TEXT_VARIATIONS.put(0x00000080, "PASSWORD");
        TEXT_VARIATIONS.put(0x00000060, "PERSON_NAME");
        TEXT_VARIATIONS.put(0x000000c0, "PHONETIC");
        TEXT_VARIATIONS.put(0x00000070, "POSTAL_ADDRESS");
        TEXT_VARIATIONS.put(0x00000040, "SHORT_MESSAGE");
        TEXT_VARIATIONS.put(0x00000010, "URI");
        TEXT_VARIATIONS.put(0x00000090, "VISIBLE_PASSWORD");
        TEXT_VARIATIONS.put(0x000000a0, "WEB_EDIT_TEXT");
        TEXT_VARIATIONS.put(0x000000d0, "WEB_EMAIL_ADDRESS");
        TEXT_VARIATIONS.put(0x000000e0, "WEB_PASSWORD");

    }
    
    private static void addBit(StringBuffer buf, int bit, String str) {
        if (bit != 0) {
            buf.append("|");
            buf.append(str);
        }
    }

    private static String inputTypeDesc(int type) {
        int cls = type & 0x0000000f; // MASK_CLASS
        int flags = type & 0x00fff000; // MASK_FLAGS
        int var = type &  0x00000ff0; // MASK_VARIATION

        StringBuffer out = new StringBuffer();
        String clsName = INPUT_CLASSES.get(cls);
        out.append(clsName != null ? clsName : "?");
        
        if (cls == InputType.TYPE_CLASS_TEXT) {
            String varName = TEXT_VARIATIONS.get(var);
            if (varName != null) {
                out.append(".");
                out.append(varName);
            }
            addBit(out, flags & 0x00010000, "AUTO_COMPLETE");
            addBit(out, flags & 0x00008000, "AUTO_CORRECT");
            addBit(out, flags & 0x00001000, "CAP_CHARACTERS");
            addBit(out, flags & 0x00004000, "CAP_SENTENCES");
            addBit(out, flags & 0x00002000, "CAP_WORDS");
            addBit(out, flags & 0x00040000, "IME_MULTI_LINE");
            addBit(out, flags & 0x00020000, "MULTI_LINE");
            addBit(out, flags & 0x00080000, "NO_SUGGESTIONS");
        } else if (cls == InputType.TYPE_CLASS_NUMBER) {
            String varName = NUMBER_VARIATIONS.get(var);
            if (varName != null) {
                out.append(".");
                out.append(varName);
            }
            addBit(out, flags & 0x00002000, "DECIMAL");
            addBit(out, flags & 0x00001000, "SIGNED");        
        } else if (cls == InputType.TYPE_CLASS_DATETIME) {
            String varName = DATETIME_VARIATIONS.get(var);
            if (varName != null) {
                out.append(".");
                out.append(varName);
            }
        }
        return out.toString();
    }

    private void updateSummaries() {
        Resources res = getResources();
        mSettingsKeyPreference.setSummary(
                res.getStringArray(R.array.settings_key_modes)
                [mSettingsKeyPreference.findIndexOfValue(mSettingsKeyPreference.getValue())]);

        mInputConnectionInfo.setSummary(String.format("%s type=%s",
                LatinIME.sKeyboardSettings.editorPackageName,
                inputTypeDesc(LatinIME.sKeyboardSettings.editorInputType)
                ));
    }

    private void showVoiceConfirmation() {
        mOkClicked = false;
        showDialog(VOICE_INPUT_CONFIRM_DIALOG);
    }

    private void updateVoiceModeSummary() {
        mVoicePreference.setSummary(
                getResources().getStringArray(R.array.voice_input_modes_summary)
                [mVoicePreference.findIndexOfValue(mVoicePreference.getValue())]);
    }

    @Override
    protected Dialog onCreateDialog(int id) {
        switch (id) {
            default:
                Log.e(TAG, "unknown dialog " + id);
                return null;
        }
    }

    public void onDismiss(DialogInterface dialog) {
        if (!mOkClicked) {
            // This assumes that onPreferenceClick gets called first, and this if the user
            // agreed after the warning, we set the mOkClicked value to true.
            mVoicePreference.setValue(mVoiceModeOff);
        }
    }

    private void updateVoicePreference() {
    }
}
