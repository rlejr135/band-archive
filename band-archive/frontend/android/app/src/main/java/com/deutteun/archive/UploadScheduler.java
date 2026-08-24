package com.deutteun.archive;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.os.Build;
import android.os.PersistableBundle;
import androidx.work.BackoffPolicy;
import androidx.work.Constraints;
import androidx.work.Data;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import java.util.concurrent.TimeUnit;

public final class UploadScheduler {
  private UploadScheduler() {}
  private static String workName(int workId) { return "upload-work-" + workId; }

  public static void schedule(Context context, UploadStore.Task task) {
    validate(task);
    if (scheduleUserInitiated(context, task)) return;
    scheduleWork(context, task, UploadRetryPolicy.workPolicy(false));
  }

  /** A manual retry replaces a delayed retry; normal enqueue and startup recovery remain KEEP. */
  public static boolean retryNow(Context context, UploadStore.Task task) {
    if (task == null || !UploadRetryPolicy.canRetryNow(task.state, task.leaseExpires,
        System.currentTimeMillis(), UploadExecutionRegistry.task(task.id) != null)) return false;
    cancel(context, task);
    if (!scheduleUserInitiated(context, task)) scheduleWork(context, task, UploadRetryPolicy.workPolicy(true));
    return true;
  }

  private static void validate(UploadStore.Task task) {
    if (task == null || !UploadWorkIds.isValid(task.workId)) throw new IllegalArgumentException("A persistent positive work ID is required");
  }

  private static boolean scheduleUserInitiated(Context context, UploadStore.Task task) {
    if (Build.VERSION.SDK_INT < 34) return false;
    try {
      PersistableBundle extras = new PersistableBundle(); extras.putString("id", task.id); extras.putInt("work_id", task.workId);
      JobInfo job = new JobInfo.Builder(task.workId, new ComponentName(context, BackgroundUploadJobService.class))
          .setExtras(extras).setRequiredNetwork(new NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build())
          .setEstimatedNetworkBytes(0, task.bytes).setUserInitiated(true).build();
      return context.getSystemService(JobScheduler.class).schedule(job) == JobScheduler.RESULT_SUCCESS;
    } catch (SecurityException ignored) { return false; }
  }

  private static void scheduleWork(Context context, UploadStore.Task task, ExistingWorkPolicy policy) {
    Constraints constraints = new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
    WorkManager.getInstance(context).enqueueUniqueWork(workName(task.workId), policy,
        new OneTimeWorkRequest.Builder(BackgroundUploadWorker.class).setConstraints(constraints)
            .setInputData(new Data.Builder().putString("id", task.id).putInt("work_id", task.workId).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS).build());
  }

  public static void resumeAll(Context context) {
    UploadStore store = new UploadStore(context); android.database.Cursor cursor = store.runnable();
    try { while (cursor.moveToNext()) { UploadStore.Task task = store.get(cursor.getString(cursor.getColumnIndexOrThrow("id"))); if (task != null) schedule(context, task); } }
    finally { cursor.close(); }
  }

  public static void cancel(Context context, String id) { if (id != null) cancel(context, new UploadStore(context).get(id)); }
  private static void cancel(Context context, UploadStore.Task task) {
    if (task == null || !UploadWorkIds.isValid(task.workId)) return;
    context.getSystemService(JobScheduler.class).cancel(task.workId);
    WorkManager.getInstance(context).cancelUniqueWork(workName(task.workId));
  }
}
