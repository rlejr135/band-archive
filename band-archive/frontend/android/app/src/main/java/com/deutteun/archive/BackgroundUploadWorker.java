package com.deutteun.archive;
import android.content.*; import androidx.annotation.NonNull; import androidx.work.*;
public class BackgroundUploadWorker extends Worker {
  public BackgroundUploadWorker(@NonNull Context c,@NonNull WorkerParameters p){super(c,p);}
  @NonNull @Override public Result doWork(){
    String id=getInputData().getString("id");
    try { setForegroundAsync(UploadNotifications.foreground(getApplicationContext(), id)).get(); }
    catch (Exception ignored) { return Result.retry(); }
    UploadEngine.Outcome outcome=UploadEngine.run(getApplicationContext(),id);
    return outcome==UploadEngine.Outcome.RETRY?Result.retry():outcome==UploadEngine.Outcome.FAILURE?Result.failure():Result.success();
  }
}
