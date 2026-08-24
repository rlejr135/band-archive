package com.deutteun.archive;

import java.io.File;

/** Bounds durable SAF copies before they can consume unbounded private storage. */
final class UploadSourcePolicy {
  static final long MAX_VIDEO_BYTES = 1024L * 1024 * 1024;
  static final long FREE_SPACE_HEADROOM_BYTES = 16L * 1024 * 1024;
  private UploadSourcePolicy() {}

  static boolean declaredSizeAllowed(long size) { return size <= 0 || size <= MAX_VIDEO_BYTES; }
  static boolean hasSpace(long available, long declared) {
    long required = declared > 0 ? declared + FREE_SPACE_HEADROOM_BYTES : FREE_SPACE_HEADROOM_BYTES;
    return available >= required;
  }
  static long checkedTotal(long total, int read) throws java.io.IOException {
    if (read < 0 || total < 0 || total > MAX_VIDEO_BYTES - read) throw new java.io.IOException("영상 파일은 1GiB를 초과할 수 없습니다.");
    return total + read;
  }
  static boolean isRegularFile(File file) { return file != null && file.isFile(); }
}
