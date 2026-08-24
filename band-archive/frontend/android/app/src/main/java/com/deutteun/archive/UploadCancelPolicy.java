package com.deutteun.archive;

/** A receiver may cancel scheduler work only after a durable terminal marker exists. */
final class UploadCancelPolicy {
  private UploadCancelPolicy() {}
  static boolean mayStopScheduler(boolean cancelChanged, String currentState) {
    return cancelChanged || "cancelled".equals(currentState);
  }
}
