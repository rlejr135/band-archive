package com.deutteun.archive;
import android.app.job.JobParameters;import android.app.job.JobService;import android.os.Build;
public class BackgroundUploadJobService extends JobService {
  private volatile Thread running;
  @Override public boolean onStartJob(JobParameters p){
    String id=p.getExtras().getString("id");
    if(Build.VERSION.SDK_INT>=34)setNotification(p,id.hashCode(),UploadNotifications.upload(this,id),JobService.JOB_END_NOTIFICATION_POLICY_DETACH);
    running=new Thread(()->{UploadEngine.Outcome outcome=UploadEngine.run(this,id);jobFinished(p,outcome==UploadEngine.Outcome.RETRY);}); running.start(); return true;
  }
  @Override public boolean onStopJob(JobParameters p){if(running!=null)running.interrupt();return true;}
}
