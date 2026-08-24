package com.deutteun.archive;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import java.util.HashSet;
import java.util.Set;
import org.junit.Test;

public class UploadProtocolTest {
  @Test public void calculatesLastPartRangeWithoutOverflow() {
    assertEquals(10, UploadProtocol.partStart(2, 10));
    assertEquals(5, UploadProtocol.partLength(15, 2, 10));
  }
  @Test public void boundsRetryAndClassifiesHttpFailures() {
    assertTrue(UploadProtocol.retryDelayMs(0) > 0);
    assertTrue(UploadProtocol.retryDelayMs(100) <= 30_000);
    assertTrue(UploadProtocol.retryable(503));
    assertFalse(UploadProtocol.retryable(400));
  }
  @Test public void allocatorUsesDistinctPositiveIdsAndWrapsSafely() {
    Set<Integer> used=new HashSet<>(); used.add(1); used.add(Integer.MAX_VALUE);
    assertEquals(2,UploadWorkIds.allocateFrom(1,used));
    assertEquals(3,UploadWorkIds.allocateFrom(Integer.MAX_VALUE,used));
    assertTrue(UploadWorkIds.isValid(Integer.MAX_VALUE));
    assertFalse(UploadWorkIds.isValid(0));
    assertFalse(UploadWorkIds.isValid(-1));
  }
  @Test public void backfillPreservesFirstValidValueAndRepairsDuplicates() {
    Set<Integer> used=new HashSet<>();
    assertEquals(7,UploadWorkIds.preserveOrAllocate(7,used));
    assertEquals(1,UploadWorkIds.preserveOrAllocate(7,used));
    assertEquals(2,UploadWorkIds.preserveOrAllocate(0,used));
    assertEquals(3,UploadWorkIds.preserveOrAllocate(null,used));
  }
  @Test public void leasePolicyRejectsOwnerMismatchAndTerminalOverwrite() {
    assertTrue(UploadLeasePolicy.canEngineWrite("uploading","owner-a","owner-a"));
    assertFalse(UploadLeasePolicy.canEngineWrite("uploading","owner-a","owner-b"));
    assertFalse(UploadLeasePolicy.canEngineWrite("completed","owner-a","owner-a"));
    assertFalse(UploadLeasePolicy.canEngineWrite("failed","owner-a","owner-a"));
  }
  @Test public void cancelRaceRejectsTheEngineWriteAfterLeaseWasCleared() {
    // This mirrors the SQL predicate after cancel's one-transaction state+lease update.
    assertFalse(UploadLeasePolicy.canEngineWrite("cancelled",null,"owner-a"));
    assertTrue(UploadLeasePolicy.isNonterminal("processing"));
    assertFalse(UploadLeasePolicy.isNonterminal("cancelled"));
  }
  @Test public void registryRejectsDuplicateTaskButAllowsIndependentTasks() {
    UploadExecutionRegistry.Handle first=UploadExecutionRegistry.register("task-a",101,null);
    UploadExecutionRegistry.Handle second=UploadExecutionRegistry.register("task-b",102,null);
    assertNotNull(first); assertNotNull(second);
    assertNull(UploadExecutionRegistry.register("task-a",103,null));
    assertTrue(UploadExecutionRegistry.cancel(first));
    assertTrue(first.isCancelled()); assertFalse(second.isCancelled());
    assertTrue(first.finishOnce()); assertFalse(first.finishOnce());
    UploadExecutionRegistry.unregister(first); UploadExecutionRegistry.unregister(second);
  }
  @Test public void stopAndNotificationPoliciesAreTaskStateSpecific() {
    assertTrue(UploadExecutionRegistry.shouldRetryOnStop("uploading"));
    assertFalse(UploadExecutionRegistry.shouldRetryOnStop("processing"));
    assertFalse(UploadExecutionRegistry.shouldRetryOnStop("completed"));
    assertEquals(UploadNotificationPolicy.Mode.PROGRESS,UploadNotificationPolicy.mode("uploading"));
    assertEquals(UploadNotificationPolicy.Mode.RETRY,UploadNotificationPolicy.mode("retry_wait"));
    assertEquals(UploadNotificationPolicy.Mode.PROCESSING,UploadNotificationPolicy.mode("processing"));
    assertEquals(UploadNotificationPolicy.Mode.FAILURE,UploadNotificationPolicy.mode("failed"));
    assertEquals(UploadNotificationPolicy.Mode.REMOVE,UploadNotificationPolicy.mode("cancelled"));
  }
}
