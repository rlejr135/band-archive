package com.deutteun.archive;

import java.util.Set;

/** Allocation and migration policy for Android scheduler/notification identifiers. */
final class UploadWorkIds {
  private UploadWorkIds() {}

  static boolean isValid(int value) {
    return value > 0;
  }

  /** Preserves a usable legacy value once; duplicates and invalid values receive a free ID. */
  static int preserveOrAllocate(Integer existing, Set<Integer> used) {
    if (existing != null && isValid(existing) && used.add(existing)) return existing;
    return allocateFrom(1, used);
  }

  /** Reserves the next free positive Java int, wrapping safely after Integer.MAX_VALUE. */
  static int allocateFrom(int preferred, Set<Integer> used) {
    int start = isValid(preferred) ? preferred : 1;
    for (long value = start; value <= Integer.MAX_VALUE; value++) {
      int candidate = (int) value;
      if (used.add(candidate)) return candidate;
    }
    for (int candidate = 1; candidate < start; candidate++) {
      if (used.add(candidate)) return candidate;
    }
    throw new IllegalStateException("No positive Android work ID remains");
  }

  static int nextPreferred(int assigned) {
    return assigned == Integer.MAX_VALUE ? 1 : assigned + 1;
  }
}
