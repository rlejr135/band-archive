package com.deutteun.archive;
import android.app.job.JobParameters;import android.app.job.JobService;import android.os.Build;
public class BackgroundUploadJobService extends JobService {
  private volatile Thread running;
  @Override public boolean onStartJob(JobParameters p){
    String id=p.getExtras().getString("id");
    int workId=p.getExtras().getInt("work_id",0); if(!UploadWorkIds.isValid(workId)){UploadStore.Task task=new UploadStore(this).get(id);workId=task==null?0:task.workId;} if(Build.VERSION.SDK_INT>=34&&UploadWorkIds.isValid(workId))setNotification(p,workId,UploadNotifications.upload(this,id,workId),JobService.JOB_END_NOTIFICATION_POLICY_DETACH);
    running=new Thread(()->{UploadEngine.Outcome outcome=UploadEngine.run(this,id);jobFinished(p,outcome==UploadEngine.Outcome.RETRY);}); running.start(); return true;
  }
  @Override public boolean onStopJob(JobParameters p){if(running!=null)running.interrupt();return true;}
}
