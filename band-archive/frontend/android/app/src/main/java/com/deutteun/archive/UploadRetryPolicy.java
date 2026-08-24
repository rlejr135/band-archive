package com.deutteun.archive;

import androidx.work.ExistingWorkPolicy;

/** Manual retry only replaces a dormant retry; it never steals an active lease. */
final class UploadRetryPolicy {
  private UploadRetryPolicy() {}
  static boolean canRetryNow(String state, long leaseExpiresAt, long now, boolean activeExecution) {
    return "retry_wait".equals(state) && !activeExecution && leaseExpiresAt < now;
  }
  static ExistingWorkPolicy workPolicy(boolean explicitManualRetry) {
    return explicitManualRetry ? ExistingWorkPolicy.REPLACE : ExistingWorkPolicy.KEEP;
  }
}
