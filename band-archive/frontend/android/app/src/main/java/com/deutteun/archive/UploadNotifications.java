package com.deutteun.archive;

import android.app.*;
import android.content.pm.ServiceInfo;
import android.content.*;
import android.os.Build;
import androidx.core.app.NotificationCompat;
import androidx.work.ForegroundInfo;

final class UploadNotifications {
  private static final String CHANNEL = "uploads";
  private UploadNotifications() {}
  static Notification upload(Context context, String id, int workId) { return upload(context, id, workId, -1); }
  static Notification upload(Context context, String id, int workId, int progress) {
    if (!UploadWorkIds.isValid(workId)) throw new IllegalArgumentException("A positive work ID is required");
    NotificationManager manager = context.getSystemService(NotificationManager.class);
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) manager.createNotificationChannel(new NotificationChannel(CHANNEL, "업로드", NotificationManager.IMPORTANCE_LOW));
    Intent intent = new Intent(context, BackgroundUploadCancelReceiver.class).putExtra("id", id).putExtra("work_id", workId);
    PendingIntent cancel = PendingIntent.getBroadcast(context, workId, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle("파일 업로드 중").setContentText("취소할 수 있습니다").setOngoing(true)
        .addAction(android.R.drawable.ic_menu_close_clear_cancel, "취소", cancel);
    if (progress >= 0) builder.setProgress(100, progress, false).setContentText("업로드 " + progress + "% · 취소할 수 있습니다");
    return builder.build();
  }
  static ForegroundInfo foreground(Context context, String id, int workId) { return new ForegroundInfo(workId, upload(context, id, workId), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC); }
  static void update(Context context, UploadStore.Task task) {
    if (!UploadWorkIds.isValid(task.workId)) return;
    NotificationManager manager=context.getSystemService(NotificationManager.class); if(manager==null)return;
    try {
      switch(UploadNotificationPolicy.mode(task.state)) {
        case REMOVE: manager.cancel(task.workId); return;
        case RETRY: manager.notify(task.workId, status(context,task,"업로드 재시도 대기","네트워크가 복구되면 다시 시도합니다.")); return;
        case PROCESSING: manager.notify(task.workId, status(context,task,"서버에서 미디어 처리 중","처리가 끝나면 재생할 수 있습니다.")); return;
        case FAILURE: manager.notify(task.workId, status(context,task,"업로드 실패",task.error==null?"다시 시도할 수 있습니다.":task.error)); return;
        default: manager.notify(task.workId,upload(context,task.id,task.workId,task.progress));
      }
    } catch (SecurityException ignored) { /* POST_NOTIFICATIONS denial must not stop the upload. */ }
  }
  private static Notification status(Context context,UploadStore.Task task,String title,String text) {
    NotificationManager manager=context.getSystemService(NotificationManager.class);
    if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.O)manager.createNotificationChannel(new NotificationChannel(CHANNEL,"업로드",NotificationManager.IMPORTANCE_LOW));
    return new NotificationCompat.Builder(context,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload).setContentTitle(title).setContentText(text).setOngoing(false).setAutoCancel(true).build();
  }
}
