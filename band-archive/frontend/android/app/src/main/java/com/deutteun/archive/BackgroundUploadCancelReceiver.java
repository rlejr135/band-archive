package com.deutteun.archive;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Notification cancellation must survive receiver lifetime without blocking the main thread. */
public final class BackgroundUploadCancelReceiver extends BroadcastReceiver {
  private static final ExecutorService ABORTS = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS,
      new ArrayBlockingQueue<>(8), new ThreadPoolExecutor.AbortPolicy());

  @Override public void onReceive(Context context, Intent intent) {
    PendingResult pending = goAsync();
    String id = intent.getStringExtra("id");
    Context app = context.getApplicationContext();
    boolean delegated = false;
    try {
      if (id == null || id.isEmpty()) return;
      UploadExecutionRegistry.cancel(id);
      UploadStore store = new UploadStore(app);
      boolean changed = store.cancel(id); // Durable marker comes before scheduler cancellation.
      UploadStore.Task task = store.get(id);
      if (UploadCancelPolicy.mayStopScheduler(changed, task == null ? null : task.state)) UploadScheduler.cancel(app, id);
      if (task != null) UploadEvents.emit(app, task);
      ABORTS.execute(() -> { try { UploadEngine.abort(app, id); } finally { pending.finish(); } });
      delegated = true;
    } catch (Exception ignored) {
      // Local cancellation is durable if the store call above completed; remote abort is best effort.
    } finally {
      if (!delegated) pending.finish();
    }
  }
}
