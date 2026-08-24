package com.deutteun.archive;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
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
}
