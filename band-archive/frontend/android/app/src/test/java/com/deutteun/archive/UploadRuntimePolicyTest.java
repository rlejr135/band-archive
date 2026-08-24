package com.deutteun.archive;

import static org.junit.Assert.*;
import androidx.work.ExistingWorkPolicy;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.Arrays;
import org.junit.Test;

public class UploadRuntimePolicyTest {
  @Test public void copyRejectsDeclaredAndStreamingSizesOverOneGiB() throws Exception {
    assertTrue(UploadSourcePolicy.declaredSizeAllowed(UploadSourcePolicy.MAX_VIDEO_BYTES));
    assertFalse(UploadSourcePolicy.declaredSizeAllowed(UploadSourcePolicy.MAX_VIDEO_BYTES + 1));
    assertTrue(UploadSourcePolicy.declaredSizeAllowed(0)); // unknown metadata is bounded while streaming
    assertEquals(UploadSourcePolicy.MAX_VIDEO_BYTES, UploadSourcePolicy.checkedTotal(UploadSourcePolicy.MAX_VIDEO_BYTES - 1, 1));
    try { UploadSourcePolicy.checkedTotal(UploadSourcePolicy.MAX_VIDEO_BYTES, 1); fail("must stop unknown-size streams at the limit"); }
    catch (IOException expected) { }
    assertTrue(UploadSourcePolicy.hasSpace(UploadSourcePolicy.FREE_SPACE_HEADROOM_BYTES, 0));
    assertFalse(UploadSourcePolicy.hasSpace(UploadSourcePolicy.FREE_SPACE_HEADROOM_BYTES - 1, 0));
  }

  @Test public void failedMultiSelectCanRollbackOnlySafeDurableFiles() throws Exception {
    File root = Files.createTempDirectory("upload-retention").toFile();
    File one = new File(root, "one"); File two = new File(root, "two");
    assertTrue(one.createNewFile()); assertTrue(two.createNewFile());
    assertTrue(UploadFileRetention.deleteBatchFiles(root, Arrays.asList(one, two)));
    assertFalse(one.exists()); assertFalse(two.exists());
    assertFalse(UploadFileRetention.deleteFile(root, new File(root.getParentFile(), "outside")));
    root.delete();
  }

  @Test public void durableReadRequiresCanonicalRegularChild() throws Exception {
    File root = Files.createTempDirectory("upload-source").toFile();
    File regular = new File(root, "video"); assertTrue(regular.createNewFile());
    assertTrue(UploadFileRetention.isSafeRegularChild(root, regular));
    assertFalse(UploadFileRetention.isSafeRegularChild(root, root));
    assertFalse(UploadFileRetention.isSafeRegularChild(root, new File(root.getParentFile(), "outside")));
    regular.delete(); root.delete();
  }

  @Test public void retryReplacesOnlyDormantRetryWhileNormalSchedulingKeepsWork() {
    assertEquals(ExistingWorkPolicy.KEEP, UploadRetryPolicy.workPolicy(false));
    assertEquals(ExistingWorkPolicy.REPLACE, UploadRetryPolicy.workPolicy(true));
    assertTrue(UploadRetryPolicy.canRetryNow("retry_wait", 0, 1, false));
    assertFalse(UploadRetryPolicy.canRetryNow("retry_wait", 2, 1, false));
    assertFalse(UploadRetryPolicy.canRetryNow("retry_wait", 0, 1, true));
  }

  @Test public void schedulerCancellationNeedsDurableCancelledMarker() {
    assertTrue(UploadCancelPolicy.mayStopScheduler(true, "uploading"));
    assertTrue(UploadCancelPolicy.mayStopScheduler(false, "cancelled"));
    assertFalse(UploadCancelPolicy.mayStopScheduler(false, "uploading"));
  }

  @Test public void receiverMarksCancellationBeforeSchedulerAndAlwaysUsesAsyncCompletion() throws Exception {
    String receiver = new String(Files.readAllBytes(new File("src/main/java/com/deutteun/archive/BackgroundUploadCancelReceiver.java").toPath()));
    assertTrue(receiver.contains("goAsync()"));
    assertTrue(receiver.indexOf("store.cancel(id)") < receiver.indexOf("UploadScheduler.cancel"));
    assertTrue(receiver.contains("finally { pending.finish(); }"));
    assertTrue(receiver.contains("if (!delegated) pending.finish();"));
  }

  @Test public void manifestDeclaresWorkManagerDataSyncMergeOverride() throws Exception {
    String manifest = new String(Files.readAllBytes(new File("src/main/AndroidManifest.xml").toPath()));
    assertTrue(manifest.contains("xmlns:tools"));
    assertTrue(manifest.contains("androidx.work.impl.foreground.SystemForegroundService"));
    assertTrue(manifest.contains("android:foregroundServiceType=\"dataSync\""));
    assertTrue(manifest.contains("tools:node=\"merge\""));
  }
}
