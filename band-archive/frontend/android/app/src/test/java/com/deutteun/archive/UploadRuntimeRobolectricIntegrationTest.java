package com.deutteun.archive;

import static org.junit.Assert.*;

import android.content.Context;
import android.content.BroadcastReceiver;
import android.content.IntentFilter;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Looper;
import androidx.test.core.app.ApplicationProvider;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.Shadows;
import org.robolectric.annotation.Config;

/** Calls the production receiver and inspects the packaged manifest through PackageManager. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class UploadRuntimeRobolectricIntegrationTest {
  private Context context;
  @Before public void setUp() { context = ApplicationProvider.getApplicationContext(); context.deleteDatabase("background_upload.db"); }
  @After public void tearDown() { context.deleteDatabase("background_upload.db"); }

  @Test public void cancelReceiverPersistsCancelledStateClearsLeaseAndExcludesResume() {
    UploadStore store = new UploadStore(context); UploadStore.Task task = task("cancel-me"); store.insert(task); task = store.get(task.id);
    assertTrue(store.acquire(task.id, "lease-owner"));
    BroadcastReceiver receiver = new BackgroundUploadCancelReceiver();
    context.registerReceiver(receiver, new IntentFilter("test.cancel"));
    context.sendBroadcast(new Intent("test.cancel").putExtra("id", task.id));
    Shadows.shadowOf(Looper.getMainLooper()).idle();
    context.unregisterReceiver(receiver);
    UploadStore.Task cancelled = store.get(task.id);
    assertEquals("cancelled", cancelled.state); assertNull(cancelled.leaseOwner); assertEquals(0L, cancelled.leaseExpires);
    CursorAssert.assertNoTask(store.runnable(), task.id);
    store.close();
  }

  @Test public void mergedManifestRegistersDataSyncServiceAndNonExportedReceiver() throws Exception {
    PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(),
        PackageManager.GET_SERVICES | PackageManager.GET_RECEIVERS | PackageManager.GET_PERMISSIONS);
    ServiceInfo foreground = findService(info.services, "androidx.work.impl.foreground.SystemForegroundService");
    assertNotNull(foreground); assertFalse(foreground.exported);
    assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC, foreground.getForegroundServiceType());
    ServiceInfo job = findService(info.services, context.getPackageName() + ".BackgroundUploadJobService");
    assertNotNull(job); assertFalse(job.exported); assertEquals("android.permission.BIND_JOB_SERVICE", job.permission);
    ActivityInfo receiver = findReceiver(info.receivers, context.getPackageName() + ".BackgroundUploadCancelReceiver");
    assertNotNull(receiver); assertFalse(receiver.exported);
    assertTrue(hasPermission(info, "android.permission.FOREGROUND_SERVICE_DATA_SYNC"));
    assertTrue(hasPermission(info, "android.permission.RUN_USER_INITIATED_JOBS"));
  }

  private static UploadStore.Task task(String id) { UploadStore.Task task = new UploadStore.Task(); task.id=id; task.uri="file:///safe/"+id; task.bytes=1; task.api="https://example.invalid"; task.target="{}"; return task; }
  private static ServiceInfo findService(ServiceInfo[] values, String name) { if(values!=null)for(ServiceInfo value:values)if(name.equals(value.name))return value; return null; }
  private static ActivityInfo findReceiver(ActivityInfo[] values, String name) { if(values!=null)for(ActivityInfo value:values)if(name.equals(value.name))return value; return null; }
  private static boolean hasPermission(PackageInfo info, String permission) { if(info.requestedPermissions!=null)for(String value:info.requestedPermissions)if(permission.equals(value))return true; return false; }
  private static final class CursorAssert { static void assertNoTask(android.database.Cursor cursor,String id){try{while(cursor.moveToNext())assertNotEquals(id,cursor.getString(cursor.getColumnIndexOrThrow("id")));}finally{cursor.close();}} }
}
