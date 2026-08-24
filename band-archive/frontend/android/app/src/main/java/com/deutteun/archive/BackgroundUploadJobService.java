package com.deutteun.archive;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.os.Build;

public class BackgroundUploadJobService extends JobService {
  @Override public boolean onStartJob(JobParameters parameters) {
    String id=parameters.getExtras().getString("id");
    int workId=parameters.getExtras().getInt("work_id",0);
    if(!UploadWorkIds.isValid(workId)){UploadStore.Task task=new UploadStore(this).get(id);workId=task==null?0:task.workId;}
    UploadExecutionRegistry.Handle handle=UploadExecutionRegistry.register(id,workId,parameters);
    if(handle==null)return false; // Another JobService/Worker already owns this task, or the input is invalid.
    if(Build.VERSION.SDK_INT>=34)try{setNotification(parameters,workId,UploadNotifications.upload(this,id,workId),JobService.JOB_END_NOTIFICATION_POLICY_DETACH);}catch(SecurityException ignored){/* Permission denial must not prevent upload execution. */}
    Thread thread=new Thread(()->{UploadEngine.Outcome outcome=UploadEngine.run(this,id,handle);if(handle.finishOnce())jobFinished(parameters,outcome==UploadEngine.Outcome.RETRY);},"background-upload-"+workId);
    handle.attach(thread); thread.start(); return true;
  }
  @Override public boolean onStopJob(JobParameters parameters) {
    String id=parameters.getExtras().getString("id"); UploadExecutionRegistry.Handle handle=UploadExecutionRegistry.task(id);
    if(handle!=null&&handle.jobParameters==parameters)UploadExecutionRegistry.cancel(handle);
    UploadStore.Task task=new UploadStore(this).get(id); return UploadExecutionRegistry.shouldRetryOnStop(task==null?null:task.state);
  }
}
