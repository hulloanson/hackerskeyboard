package org.pocketworkstation.pckeyboard;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Serializes, parses and validates settings backup files. This class has no
 * Android framework dependencies other than org.json so it can be unit tested
 * on the JVM. See docs/app-design.md for the file format.
 */
public final class SettingsBackup {
    public static final String FORMAT_NAME = "hackerskeyboard-settings";
    public static final int FORMAT_VERSION = 1;
    public static final int MAX_BYTES = 1024 * 1024;

    public static final String TYPE_BOOLEAN = "boolean";
    public static final String TYPE_INT = "int";
    public static final String TYPE_LONG = "long";
    public static final String TYPE_FLOAT = "float";
    public static final String TYPE_STRING = "string";
    public static final String TYPE_STRING_SET = "string_set";

    private static final List<String> TYPES = java.util.Arrays.asList(
            TYPE_BOOLEAN, TYPE_INT, TYPE_LONG, TYPE_FLOAT, TYPE_STRING, TYPE_STRING_SET);

    /** Thrown for any unreadable, oversized or invalid file. */
    public static final class BackupException extends Exception {
        private static final long serialVersionUID = 1L;

        public BackupException(String message) {
            super(message);
        }

        public BackupException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** A validated import: values to write (exact Java types) and skipped-key count. */
    public static final class ImportPlan {
        /** Values typed Boolean, Integer, Long, Float, String or Set&lt;String&gt;. */
        public final Map<String, Object> values;
        public final int skippedUnknown;

        ImportPlan(Map<String, Object> values, int skippedUnknown) {
            this.values = Collections.unmodifiableMap(values);
            this.skippedUnknown = skippedUnknown;
        }
    }

    private SettingsBackup() {
    }

    /** Returns the backup type name of a SharedPreferences value, or null if unsupported. */
    public static String typeOf(Object value) {
        if (value instanceof Boolean) return TYPE_BOOLEAN;
        if (value instanceof Integer) return TYPE_INT;
        if (value instanceof Long) return TYPE_LONG;
        if (value instanceof Float) return TYPE_FLOAT;
        if (value instanceof String) return TYPE_STRING;
        if (value instanceof Set) {
            for (Object o : (Set<?>) value) {
                if (!(o instanceof String)) return null;
            }
            return TYPE_STRING_SET;
        }
        return null;
    }

    /**
     * Serializes the entries of {@code all} whose key is in {@code schema} and whose
     * value type matches the schema type. Other entries are omitted.
     *
     * @param schema allowed key to expected type name
     */
    public static String serialize(Map<String, ?> all, Map<String, String> schema,
            String appPackage, String versionName, long versionCode) throws BackupException {
        try {
            JSONObject prefs = new JSONObject();
            for (Map.Entry<String, ?> e : new TreeMap<String, Object>(all).entrySet()) {
                String expected = schema.get(e.getKey());
                Object value = e.getValue();
                String type = typeOf(value);
                if (expected == null || type == null || !expected.equals(type)) continue;
                JSONObject entry = new JSONObject();
                entry.put("type", type);
                if (TYPE_STRING_SET.equals(type)) {
                    List<String> sorted = new ArrayList<String>();
                    for (Object o : (Set<?>) value) sorted.add((String) o);
                    Collections.sort(sorted);
                    entry.put("value", new JSONArray(sorted));
                } else if (TYPE_FLOAT.equals(type)) {
                    float f = (Float) value;
                    if (Float.isNaN(f) || Float.isInfinite(f)) continue;
                    // Float.toString round-trips exactly through parsing back to float.
                    entry.put("value", Double.parseDouble(Float.toString(f)));
                } else {
                    entry.put("value", value);
                }
                prefs.put(e.getKey(), entry);
            }
            JSONObject root = new JSONObject();
            root.put("format", FORMAT_NAME);
            root.put("format_version", FORMAT_VERSION);
            root.put("app_package", appPackage);
            root.put("app_version_name", versionName);
            root.put("app_version_code", versionCode);
            root.put("preferences", prefs);
            return root.toString(2);
        } catch (JSONException e) {
            throw new BackupException("Could not serialize settings", e);
        }
    }

    /** Reads at most {@link #MAX_BYTES} bytes as UTF-8; throws if the stream is larger. */
    public static String readLimited(InputStream in) throws IOException, BackupException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) {
            if (out.size() + n > MAX_BYTES) {
                throw new BackupException("File is larger than " + MAX_BYTES + " bytes");
            }
            out.write(buf, 0, n);
        }
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }

    /**
     * Validates the complete file. Nothing is returned unless every entry (known or
     * unknown) is well formed and every known key has its schema type.
     */
    public static ImportPlan parse(String json, Map<String, String> schema)
            throws BackupException {
        if (json == null || json.length() > MAX_BYTES) {
            throw new BackupException("File is empty or larger than " + MAX_BYTES + " bytes");
        }
        JSONObject root;
        try {
            root = new JSONObject(json);
        } catch (JSONException e) {
            throw new BackupException("Not a valid JSON object", e);
        }
        if (!FORMAT_NAME.equals(root.optString("format", null))) {
            throw new BackupException("Not a Hacker's Keyboard settings file");
        }
        Object version = root.opt("format_version");
        if (!(version instanceof Integer) || (Integer) version != FORMAT_VERSION) {
            throw new BackupException("Unsupported format version: " + version);
        }
        JSONObject prefs = root.optJSONObject("preferences");
        if (prefs == null) {
            throw new BackupException("Missing preferences object");
        }

        Map<String, Object> values = new LinkedHashMap<String, Object>();
        int skipped = 0;
        java.util.Iterator<String> keys = prefs.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            JSONObject entry = prefs.optJSONObject(key);
            if (entry == null) {
                throw new BackupException("Entry is not an object: " + key);
            }
            String type = entry.optString("type", null);
            if (type == null || !TYPES.contains(type)) {
                throw new BackupException("Unknown type for " + key + ": " + type);
            }
            Object value = parseValue(key, type, entry.opt("value"));
            String expected = schema.get(key);
            if (expected == null) {
                skipped++;
            } else if (!expected.equals(type)) {
                throw new BackupException("Wrong type for " + key + ": expected " + expected
                        + ", found " + type);
            } else {
                values.put(key, value);
            }
        }
        return new ImportPlan(values, skipped);
    }

    private static Object parseValue(String key, String type, Object raw)
            throws BackupException {
        if (raw == null || raw == JSONObject.NULL) {
            throw new BackupException("Missing value for " + key);
        }
        switch (type) {
            case TYPE_BOOLEAN:
                if (raw instanceof Boolean) return raw;
                break;
            case TYPE_INT:
                if (raw instanceof Integer) return raw;
                break;
            case TYPE_LONG:
                if (raw instanceof Integer) return ((Integer) raw).longValue();
                if (raw instanceof Long) return raw;
                break;
            case TYPE_FLOAT:
                if (raw instanceof Number) {
                    double d = ((Number) raw).doubleValue();
                    float f = (float) d;
                    if (!Float.isNaN(f) && !Float.isInfinite(f)) return f;
                }
                break;
            case TYPE_STRING:
                if (raw instanceof String) return raw;
                break;
            case TYPE_STRING_SET:
                if (raw instanceof JSONArray) {
                    JSONArray array = (JSONArray) raw;
                    Set<String> set = new LinkedHashSet<String>();
                    for (int i = 0; i < array.length(); i++) {
                        Object item = array.opt(i);
                        if (!(item instanceof String)) {
                            throw new BackupException("Non-string set member for " + key);
                        }
                        set.add((String) item);
                    }
                    return set;
                }
                break;
            default:
                break;
        }
        throw new BackupException("Value for " + key + " is not a valid " + type);
    }
}
