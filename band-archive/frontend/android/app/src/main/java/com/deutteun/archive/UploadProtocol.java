package com.deutteun.archive;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Pure upload rules shared by the scheduler and HTTP runner. */
public final class UploadProtocol {
  public static final Set<String> STATES = new HashSet<>(Arrays.asList("preparing","queued","uploading","retry_wait","completing","processing","completed","failed","cancelled"));
  private UploadProtocol() {}
  public static long partStart(int part, long partSize) { return (long) (part - 1) * partSize; }
  public static long partLength(long total, int part, long partSize) { return Math.max(0, Math.min(partSize, total - partStart(part, partSize))); }
  public static boolean retryable(int code) { return code == 401 || code == 403 || code == 408 || code == 429 || code >= 500; }
  public static long retryDelayMs(int attempt) { return Math.min(30_000L, 1_000L << Math.min(5, Math.max(0, attempt))); }
}
