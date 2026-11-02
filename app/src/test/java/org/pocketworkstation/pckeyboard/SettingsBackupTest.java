package org.pocketworkstation.pckeyboard;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import org.junit.Test;

public class SettingsBackupTest {
    private static Map<String, String> schema() {
        Map<String, String> s = new HashMap<String, String>();
        s.put("flag", "boolean");
        s.put("count", "int");
        s.put("big", "long");
        s.put("ratio", "float");
        s.put("text", "string");
        s.put("items", "string_set");
        return s;
    }

    private static String file(String prefsJson) {
        return "{\"format\":\"hackerskeyboard-settings\",\"format_version\":1,"
                + "\"preferences\":" + prefsJson + "}";
    }

    private static void assertRejected(String json) {
        try {
            SettingsBackup.parse(json, schema());
            fail("expected rejection of " + json);
        } catch (SettingsBackup.BackupException expected) {
            // ok
        }
    }

    @Test
    public void roundTripPreservesExactTypes() throws Exception {
        Map<String, Object> all = new LinkedHashMap<String, Object>();
        all.put("flag", true);
        all.put("count", 42);
        all.put("big", 9007199254740993L);
        all.put("ratio", 0.1f);
        all.put("text", "he said \"hi\"\né");
        all.put("items", new HashSet<String>(Arrays.asList("b", "a")));
        all.put("not_in_schema", "x");
        all.put("input_language", "en");

        String json = SettingsBackup.serialize(all, schema(), "com.hulloanson", "1.0", 7L);
        assertFalse(json.contains("not_in_schema"));
        SettingsBackup.ImportPlan plan = SettingsBackup.parse(json, schema());

        assertEquals(0, plan.skippedUnknown);
        assertEquals(6, plan.values.size());
        assertEquals(Boolean.TRUE, plan.values.get("flag"));
        assertEquals(Integer.valueOf(42), plan.values.get("count"));
        assertEquals(Long.valueOf(9007199254740993L), plan.values.get("big"));
        assertEquals(Float.valueOf(0.1f), plan.values.get("ratio"));
        assertEquals("he said \"hi\"\né", plan.values.get("text"));
        assertEquals(new HashSet<String>(Arrays.asList("a", "b")), plan.values.get("items"));
    }

    @Test
    public void serializeSkipsEntriesWithTypeMismatch() throws Exception {
        Map<String, Object> all = new HashMap<String, Object>();
        all.put("flag", "true"); // schema says boolean
        all.put("ratio", Float.NaN);
        String json = SettingsBackup.serialize(all, schema(), "p", "v", 1L);
        assertEquals(0, SettingsBackup.parse(json, schema()).values.size());
    }

    @Test
    public void unknownKeysAreSkippedAndCounted() throws Exception {
        SettingsBackup.ImportPlan plan = SettingsBackup.parse(file(
                "{\"flag\":{\"type\":\"boolean\",\"value\":false},"
                + "\"mystery\":{\"type\":\"int\",\"value\":3},"
                + "\"other\":{\"type\":\"string\",\"value\":\"x\"}}"), schema());
        assertEquals(1, plan.values.size());
        assertEquals(2, plan.skippedUnknown);
    }

    @Test
    public void rejectsBadContainer() {
        assertRejected("not json");
        assertRejected("[]");
        assertRejected("{}");
        assertRejected("{\"format\":\"other\",\"format_version\":1,\"preferences\":{}}");
        assertRejected("{\"format\":\"hackerskeyboard-settings\",\"preferences\":{}}");
        assertRejected("{\"format\":\"hackerskeyboard-settings\",\"format_version\":2,"
                + "\"preferences\":{}}");
        assertRejected("{\"format\":\"hackerskeyboard-settings\",\"format_version\":\"1\","
                + "\"preferences\":{}}");
        assertRejected("{\"format\":\"hackerskeyboard-settings\",\"format_version\":1}");
        assertRejected("{\"format\":\"hackerskeyboard-settings\",\"format_version\":1,"
                + "\"preferences\":[]}");
    }

    @Test
    public void rejectsBadEntries() {
        assertRejected(file("{\"flag\":true}"));
        assertRejected(file("{\"flag\":{\"value\":true}}"));
        assertRejected(file("{\"flag\":{\"type\":\"boolean\"}}"));
        assertRejected(file("{\"flag\":{\"type\":\"boolean\",\"value\":null}}"));
        assertRejected(file("{\"flag\":{\"type\":\"bogus\",\"value\":true}}"));
        assertRejected(file("{\"flag\":{\"type\":\"boolean\",\"value\":\"true\"}}"));
        assertRejected(file("{\"count\":{\"type\":\"int\",\"value\":1.5}}"));
        assertRejected(file("{\"count\":{\"type\":\"int\",\"value\":3000000000}}"));
        assertRejected(file("{\"big\":{\"type\":\"long\",\"value\":\"1\"}}"));
        assertRejected(file("{\"ratio\":{\"type\":\"float\",\"value\":\"1\"}}"));
        assertRejected(file("{\"ratio\":{\"type\":\"float\",\"value\":1e999}}"));
        assertRejected(file("{\"text\":{\"type\":\"string\",\"value\":5}}"));
        assertRejected(file("{\"items\":{\"type\":\"string_set\",\"value\":\"a\"}}"));
        assertRejected(file("{\"items\":{\"type\":\"string_set\",\"value\":[\"a\",1]}}"));
    }

    @Test
    public void rejectsTypeThatDiffersFromSchema() {
        assertRejected(file("{\"flag\":{\"type\":\"string\",\"value\":\"true\"}}"));
        assertRejected(file("{\"text\":{\"type\":\"int\",\"value\":1}}"));
    }

    @Test
    public void oneMalformedUnknownEntryRejectsWholeFile() {
        assertRejected(file("{\"flag\":{\"type\":\"boolean\",\"value\":true},"
                + "\"mystery\":{\"type\":\"int\",\"value\":\"x\"}}"));
    }

    @Test
    public void longAcceptsSmallIntegerLiteral() throws Exception {
        SettingsBackup.ImportPlan plan = SettingsBackup.parse(
                file("{\"big\":{\"type\":\"long\",\"value\":5}}"), schema());
        assertEquals(Long.valueOf(5L), plan.values.get("big"));
    }

    @Test
    public void readLimitedEnforcesSizeLimit() throws Exception {
        byte[] ok = new byte[SettingsBackup.MAX_BYTES];
        Arrays.fill(ok, (byte) 'a');
        assertEquals(SettingsBackup.MAX_BYTES,
                SettingsBackup.readLimited(new ByteArrayInputStream(ok)).length());
        try {
            SettingsBackup.readLimited(new ByteArrayInputStream(new byte[SettingsBackup.MAX_BYTES + 1]));
            fail("expected size rejection");
        } catch (SettingsBackup.BackupException expected) {
            assertTrue(expected.getMessage().contains("larger"));
        } catch (IOException e) {
            fail(e.toString());
        }
    }

    @Test
    public void parseRejectsOversizedText() {
        char[] big = new char[SettingsBackup.MAX_BYTES + 1];
        Arrays.fill(big, ' ');
        assertRejected(new String(big));
    }

    @Test
    public void typeOfClassifiesValues() {
        assertEquals("boolean", SettingsBackup.typeOf(true));
        assertEquals("int", SettingsBackup.typeOf(1));
        assertEquals("long", SettingsBackup.typeOf(1L));
        assertEquals("float", SettingsBackup.typeOf(1f));
        assertEquals("string", SettingsBackup.typeOf("s"));
        Set<String> set = new HashSet<String>();
        assertEquals("string_set", SettingsBackup.typeOf(set));
        assertEquals(null, SettingsBackup.typeOf(1.0));
    }
}
