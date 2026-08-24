package com.deutteun.archive;

/** Pure retention rules; source deletion itself is confined to the app upload directory. */
public final class UploadRetentionPolicy {
  public static final long RETENTION_MS = 7L * 24 * 60 * 60 * 1000;
  private UploadRetentionPolicy() {}

  public static boolean isTerminal(String state) {
    return "completed".equals(state) || "failed".equals(state) || "cancelled".equals(state);
  }

  public static boolean mayAcknowledge(String state) { return isTerminal(state); }

  public static boolean deleteSourceImmediately(String state) {
    return "processing".equals(state) || "completed".equals(state) || "cancelled".equals(state);
  }

  public static boolean hasActiveLease(String state, long leaseExpiresAt, long now) {
    return leaseExpiresAt >= now;
  }

  public static boolean eligibleForExpiry(String state, long updatedAt, long leaseExpiresAt, long now) {
    return isTerminal(state) && updatedAt <= now - RETENTION_MS && !hasActiveLease(state, leaseExpiresAt, now);
  }

  public static boolean isPartialName(String name) {
    return name != null && (name.endsWith(".tmp") || name.endsWith(".partial") || name.endsWith(".part"));
  }
}
