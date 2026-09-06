import { nativeTargetMatches } from './nativeUploadQueue.js';
import { isUploadCompleted, isUploadInProgress, isUploadRetryable } from './uploadPresentation.js';

const sameId = (left, right) => left !== undefined && left !== null && String(left) === String(right);

export const hasNativeUploadForPendingFile = (item, nativePending, rehearsalId) => (
  Boolean(item?.file?.id) && nativePending.some((nativeItem) => (
    sameId(nativeItem.id, item.file.id)
    && nativeTargetMatches(nativeItem, { rehearsalId, songId: item.songId })
  ))
);

/**
 * Only unstarted files and explicit failures can be submitted again. Completed,
 * in-flight, and durably queued native files must never be enqueued twice.
 */
export const getRehearsalUploadPlan = ({ items, nativePending = [], rehearsalId, indexes }) => (
  items.flatMap((item, index) => {
    if (indexes && !indexes.includes(index)) return [];
    if (!item?.songId || isUploadCompleted(item.status) || isUploadInProgress(item.status)) return [];
    if (hasNativeUploadForPendingFile(item, nativePending, rehearsalId)) return [];
    return item.status === 'pending' || isUploadRetryable(item.status) ? [{ item, index }] : [];
  })
);
