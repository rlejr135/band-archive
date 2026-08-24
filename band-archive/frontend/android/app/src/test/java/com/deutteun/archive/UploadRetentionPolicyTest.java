package com.deutteun.archive;

import static org.junit.Assert.*;
import java.io.File;
import java.nio.file.Files;
import org.junit.Test;

public class UploadRetentionPolicyTest {
  @Test public void retentionKeepsRetryableAndProcessingRowsButExpiresOldTerminalRows() {
    long now=10L*UploadRetentionPolicy.RETENTION_MS;
    assertFalse(UploadRetentionPolicy.eligibleForExpiry("retry_wait",0,0,now));
    assertFalse(UploadRetentionPolicy.eligibleForExpiry("processing",0,0,now));
    assertFalse(UploadRetentionPolicy.eligibleForExpiry("failed",now-UploadRetentionPolicy.RETENTION_MS+1,0,now));
    assertTrue(UploadRetentionPolicy.eligibleForExpiry("failed",now-UploadRetentionPolicy.RETENTION_MS,0,now));
  }

  @Test public void activeLeaseAndAcknowledgementGuardsProtectNonterminalWork() {
    long now=1000;
    assertTrue(UploadRetentionPolicy.hasActiveLease("uploading",now,now));
    assertTrue(UploadRetentionPolicy.hasActiveLease("failed",now,now));
    assertFalse(UploadRetentionPolicy.hasActiveLease("processing",now-1,now));
    assertFalse(UploadRetentionPolicy.mayAcknowledge("uploading"));
    assertTrue(UploadRetentionPolicy.mayAcknowledge("completed"));
    assertTrue(UploadRetentionPolicy.mayAcknowledge("failed"));
    assertTrue(UploadRetentionPolicy.mayAcknowledge("cancelled"));
  }

  @Test public void canonicalPathPreventsDeletionOutsideUploadsDirectory() throws Exception {
    File root=Files.createTempDirectory("upload-root").toFile();
    File source=new File(root,"source"); File outside=new File(root.getParentFile(),"outside");
    assertTrue(UploadFileRetention.isSafeChild(root,source));
    assertFalse(UploadFileRetention.isSafeChild(root,new File(root,"../outside")));
    assertFalse(UploadFileRetention.isSafeChild(root,outside));
  }

  @Test public void partialNamesAreAlwaysEligibleForImmediateCleanup() {
    assertTrue(UploadRetentionPolicy.isPartialName("id.tmp"));
    assertTrue(UploadRetentionPolicy.isPartialName("id.partial"));
    assertTrue(UploadRetentionPolicy.isPartialName("id.part"));
    assertFalse(UploadRetentionPolicy.isPartialName("id"));
  }
}
