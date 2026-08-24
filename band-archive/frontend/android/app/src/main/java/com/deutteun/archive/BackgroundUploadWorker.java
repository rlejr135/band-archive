package com.deutteun.archive;
import android.content.*; import androidx.annotation.NonNull; import androidx.work.*;
public class BackgroundUploadWorker extends Worker {
  public BackgroundUploadWorker(@NonNull Context c,@NonNull WorkerParameters p){super(c,p);}
  @NonNull @Override public Result doWork(){
    String id=getInputData().getString("id"); int workId=getInputData().getInt("work_id",0); if(!UploadWorkIds.isValid(workId)){UploadStore.Task task=new UploadStore(getApplicationContext()).get(id);workId=task==null?0:task.workId;}
    try { if(!UploadWorkIds.isValid(workId))return Result.failure(); setForegroundAsync(UploadNotifications.foreground(getApplicationContext(), id, workId)).get(); }
    catch (Exception ignored) { return Result.retry(); }
    UploadEngine.Outcome outcome=UploadEngine.run(getApplicationContext(),id);
    return outcome==UploadEngine.Outcome.RETRY?Result.retry():outcome==UploadEngine.Outcome.FAILURE?Result.failure():Result.success();
  }
}
