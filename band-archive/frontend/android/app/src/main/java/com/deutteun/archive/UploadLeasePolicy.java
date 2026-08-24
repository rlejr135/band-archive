package com.deutteun.archive;

/** Pure counterpart of the lease predicates used by UploadStore's atomic SQL updates. */
final class UploadLeasePolicy {
  private UploadLeasePolicy() {}

  static boolean isNonterminal(String state) {
    return !"completed".equals(state) && !"failed".equals(state) && !"cancelled".equals(state);
  }

  static boolean canEngineWrite(String currentState, String leaseOwner, String owner) {
    return owner != null && !owner.isEmpty() && owner.equals(leaseOwner) && isNonterminal(currentState);
  }
}
