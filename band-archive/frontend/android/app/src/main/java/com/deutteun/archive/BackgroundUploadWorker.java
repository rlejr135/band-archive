package com.deutteun.archive;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

public class BackgroundUploadWorker extends Worker {
  private volatile UploadExecutionRegistry.Handle handle;
  public BackgroundUploadWorker(@NonNull Context c,@NonNull WorkerParameters p){super(c,p);}
  @NonNull @Override public Result doWork(){
    String id=getInputData().getString("id"); int workId=getInputData().getInt("work_id",0);
    if(!UploadWorkIds.isValid(workId)){UploadStore.Task task=new UploadStore(getApplicationContext()).get(id);workId=task==null?0:task.workId;}
    handle=UploadExecutionRegistry.register(id,workId,null); if(handle==null)return Result.success();
    handle.attach(Thread.currentThread());
    try { setForegroundAsync(UploadNotifications.foreground(getApplicationContext(),id,workId)).get(); }
    catch (SecurityException ignored) { /* Notification permission denial limits visibility, not upload correctness. */ }
    catch (Exception ignored) { UploadExecutionRegistry.unregister(handle); return Result.retry(); }
    UploadEngine.Outcome outcome=UploadEngine.run(getApplicationContext(),id,handle);
    return outcome==UploadEngine.Outcome.RETRY?Result.retry():outcome==UploadEngine.Outcome.FAILURE?Result.failure():Result.success();
  }
  @Override public void onStopped(){UploadExecutionRegistry.cancel(handle);super.onStopped();}
}
