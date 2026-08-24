package com.deutteun.archive;

/** State-to-notification policy; terminal progress is always removed to avoid stale UIDT UI. */
final class UploadNotificationPolicy {
  enum Mode { PROGRESS, RETRY, PROCESSING, FAILURE, REMOVE }
  private UploadNotificationPolicy() {}
  static Mode mode(String state) {
    if ("retry_wait".equals(state)) return Mode.RETRY;
    if ("processing".equals(state)) return Mode.PROCESSING;
    if ("failed".equals(state)) return Mode.FAILURE;
    if ("completed".equals(state) || "cancelled".equals(state)) return Mode.REMOVE;
    return Mode.PROGRESS;
  }
}
