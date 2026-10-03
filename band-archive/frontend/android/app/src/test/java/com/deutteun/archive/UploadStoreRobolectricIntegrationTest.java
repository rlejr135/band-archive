package com.deutteun.archive;

import static org.junit.Assert.*;

import android.content.Context;
import android.database.Cursor;
import android.database.SQLException;
import android.database.sqlite.SQLiteDatabase;
import androidx.test.core.app.ApplicationProvider;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** Exercises the production SQLiteOpenHelper, including upgrades from released v1/v2 layouts. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class UploadStoreRobolectricIntegrationTest {
  private static final String DB = "background_upload.db";
  private Context context;

  @Before public void setUp() { context = ApplicationProvider.getApplicationContext(); context.deleteDatabase(DB); }
  @After public void tearDown() { context.deleteDatabase(DB); }

  @Test public void freshV4CreatesPositiveUniqueWorkIdsLeaseAndBandScopeColumns() {
    UploadStore store = new UploadStore(context);
    UploadStore.Task task = task("fresh"); store.insert(task);
    UploadStore.Task persisted = store.get("fresh");
    assertNotNull(persisted); assertTrue(UploadWorkIds.isValid(persisted.workId));
    assertColumn(store.getReadableDatabase(), "work_id"); assertColumn(store.getReadableDatabase(), "lease_owner");
    assertColumn(store.getReadableDatabase(), "lease_expires_at");
    assertColumn(store.getReadableDatabase(), "protocol"); assertColumn(store.getReadableDatabase(), "band_id");
    assertColumn(store.getReadableDatabase(), "account_id"); assertColumn(store.getReadableDatabase(), "direct_url");
    assertFalse(columnExists(store.getReadableDatabase(), "auth"));
    try {
      store.getWritableDatabase().execSQL("INSERT INTO tasks(id,work_id,uri,state) VALUES(?,?,?,?)",
          new Object[] { "duplicate", persisted.workId, "file:///safe", "queued" });
      fail("work_id must remain unique");
    } catch (SQLException expected) { }
    store.close();
  }

  @Test public void v1ToV3PreservesTaskFieldsAndBackfillsStableUniqueWorkIds() {
    createLegacy(1, false); insertLegacy("v1-first", "uploading", "session-a", "result-a", 7, 11L);
    insertLegacy("v1-second", "queued", "session-b", "result-b", 3, 12L);
    UploadStore firstOpen = new UploadStore(context);
    UploadStore.Task first = firstOpen.get("v1-first"), second = firstOpen.get("v1-second");
    assertEquals("session-a", first.session); assertEquals("result-a", first.result); assertEquals(7, first.attempts);
    assertTrue(UploadWorkIds.isValid(first.workId)); assertTrue(UploadWorkIds.isValid(second.workId)); assertNotEquals(first.workId, second.workId);
    int stable = first.workId; firstOpen.close();
    UploadStore reopen = new UploadStore(context);
    assertEquals(stable, reopen.get("v1-first").workId); // onOpen migration is safe to run again
    reopen.close();
  }

  @Test public void v2ToV3PreservesLeaseAndExistingTaskWhileAddingWorkId() {
    createLegacy(2, true); insertLegacy("v2", "retry_wait", "session-v2", "result-v2", 2, 20L);
    SQLiteDatabase legacy = context.openOrCreateDatabase(DB, Context.MODE_PRIVATE, null);
    legacy.execSQL("UPDATE tasks SET lease_owner=?, lease_expires_at=? WHERE id=?", new Object[] { "owner", 12345L, "v2" }); legacy.close();
    UploadStore store = new UploadStore(context); UploadStore.Task task = store.get("v2");
    assertEquals("session-v2", task.session); assertEquals("result-v2", task.result); assertEquals("owner", task.leaseOwner);
    assertEquals(12345L, task.leaseExpires); assertTrue(UploadWorkIds.isValid(task.workId));
    store.close();
  }

  @Test public void preBandRowsAreExplicitlyLegacyInsteadOfGuessingTheCurrentBand() {
    createLegacy(3, true); insertLegacy("released-v3", "queued", null, null, 0, 30L);
    UploadStore store = new UploadStore(context); UploadStore.Task task = store.get("released-v3");
    assertEquals("v1", task.protocol); assertEquals(0L, task.bandId); assertNull(task.accountId);
    store.close();
  }

  @Test public void v2ScopeIsDurableAndBridgeEventsExcludeCredentialsAndPresignedUrls() {
    UploadStore store = new UploadStore(context);
    UploadStore.Task task = task("scoped"); task.protocol="v2"; task.mode="direct"; task.bandId=41;
    task.bandName="First band"; task.bandSlug="first-band"; task.accountId="account-a";
    task.capability="capability-secret"; task.directUrl="https://storage.example/direct-secret";
    store.insert(task); UploadStore.Task persisted=store.get("scoped");
    assertEquals("v2",persisted.protocol);assertEquals(41L,persisted.bandId);assertEquals("first-band",persisted.bandSlug);assertEquals("account-a",persisted.accountId);
    String event=UploadEvents.json(persisted).toString();
    assertFalse(event.contains("capability-secret"));assertFalse(event.contains("direct-secret"));assertFalse(event.contains("account-a"));
    store.close();
  }

  private void createLegacy(int version, boolean leaseColumns) {
    SQLiteDatabase db = context.openOrCreateDatabase(DB, Context.MODE_PRIVATE, null);
    String lease = leaseColumns ? ", lease_owner TEXT, lease_expires_at INTEGER DEFAULT 0" : "";
    db.execSQL("CREATE TABLE tasks (id TEXT PRIMARY KEY, uri TEXT NOT NULL, name TEXT, mime TEXT, bytes INTEGER, fingerprint TEXT, api TEXT, target TEXT, state TEXT, progress INTEGER, session TEXT, cap TEXT, iv TEXT, error TEXT, result TEXT, attempts INTEGER DEFAULT 0, created INTEGER, updated INTEGER" + lease + ")");
    db.setVersion(version); db.close();
  }

  private void insertLegacy(String id, String state, String session, String result, int attempts, long created) {
    SQLiteDatabase db = context.openOrCreateDatabase(DB, Context.MODE_PRIVATE, null);
    db.execSQL("INSERT INTO tasks(id,uri,state,session,result,attempts,created,updated) VALUES(?,?,?,?,?,?,?,?)",
        new Object[] { id, "file:///safe/" + id, state, session, result, attempts, created, created });
    db.close();
  }

  private static UploadStore.Task task(String id) {
    UploadStore.Task task = new UploadStore.Task(); task.id = id; task.uri = "file:///safe/" + id; task.bytes = 1;
    task.api = "https://example.invalid"; task.target = "{}"; return task;
  }
  private static void assertColumn(SQLiteDatabase db, String expected) {
    Cursor cursor = db.rawQuery("PRAGMA table_info(tasks)", null);
    try { while (cursor.moveToNext()) if (expected.equals(cursor.getString(cursor.getColumnIndexOrThrow("name")))) return; }
    finally { cursor.close(); }
    fail("missing column " + expected);
  }
  private static boolean columnExists(SQLiteDatabase db, String expected) {
    Cursor cursor = db.rawQuery("PRAGMA table_info(tasks)", null);
    try { while (cursor.moveToNext()) if (expected.equals(cursor.getString(cursor.getColumnIndexOrThrow("name")))) return true; }
    finally { cursor.close(); }
    return false;
  }
}
