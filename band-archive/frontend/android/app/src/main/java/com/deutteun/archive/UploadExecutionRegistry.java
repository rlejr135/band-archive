package com.deutteun.archive;

import android.app.job.JobParameters;
import java.net.HttpURLConnection;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/** Process-wide ownership of one native execution per durable upload task. */
final class UploadExecutionRegistry {
  private static final ConcurrentHashMap<String, Handle> BY_TASK = new ConcurrentHashMap<>();
  private static final ConcurrentHashMap<Integer, Handle> BY_WORK = new ConcurrentHashMap<>();
  private UploadExecutionRegistry() {}

  static Handle register(String taskId, int workId, JobParameters parameters) {
    if (taskId == null || taskId.isEmpty() || !UploadWorkIds.isValid(workId)) return null;
    Handle handle = new Handle(taskId, workId, UUID.randomUUID().toString(), parameters);
    if (BY_TASK.putIfAbsent(taskId, handle) != null) return null;
    if (BY_WORK.putIfAbsent(workId, handle) != null) { BY_TASK.remove(taskId, handle); return null; }
    return handle;
  }

  static void unregister(Handle handle) {
    if (handle == null) return;
    BY_TASK.remove(handle.taskId, handle); BY_WORK.remove(handle.workId, handle);
  }

  static Handle task(String taskId) { return taskId == null ? null : BY_TASK.get(taskId); }
  static boolean cancel(String taskId) { Handle handle = task(taskId); if (handle == null) return false; handle.cancel(); return true; }
  static boolean cancel(Handle handle) { if (handle == null) return false; handle.cancel(); return true; }

  static boolean shouldRetryOnStop(String state) {
    return "preparing".equals(state) || "queued".equals(state) || "uploading".equals(state) || "retry_wait".equals(state) || "completing".equals(state);
  }

  static final class Handle {
    final String taskId, owner;
    final int workId;
    final JobParameters jobParameters;
    private final AtomicBoolean finished = new AtomicBoolean();
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private volatile Thread thread;
    private volatile HttpURLConnection connection;

    Handle(String taskId, int workId, String owner, JobParameters jobParameters) { this.taskId=taskId; this.workId=workId; this.owner=owner; this.jobParameters=jobParameters; }
    void attach(Thread value) { thread=value; if(cancelled.get() && value!=null)value.interrupt(); }
    void track(HttpURLConnection value) throws UploadStoppedException { connection=value; if(cancelled.get()){value.disconnect();throw new UploadStoppedException();} }
    void clear(HttpURLConnection value) { if(connection==value)connection=null; }
    void cancel() { cancelled.set(true); HttpURLConnection active=connection; if(active!=null)active.disconnect(); Thread running=thread; if(running!=null)running.interrupt(); }
    boolean isCancelled() { return cancelled.get(); }
    boolean finishOnce() { return finished.compareAndSet(false,true); }
  }

  static final class UploadStoppedException extends java.io.IOException { UploadStoppedException(){super("업로드 실행이 중단되었습니다.");} }
}
