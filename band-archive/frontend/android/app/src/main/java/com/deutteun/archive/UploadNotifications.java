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
  static Notification upload(Context context, String id) { return upload(context, id, -1); }
  static Notification upload(Context context, String id, int progress) {
    NotificationManager manager = context.getSystemService(NotificationManager.class);
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) manager.createNotificationChannel(new NotificationChannel(CHANNEL, "업로드", NotificationManager.IMPORTANCE_LOW));
    Intent intent = new Intent(context, BackgroundUploadCancelReceiver.class).putExtra("id", id);
    PendingIntent cancel = PendingIntent.getBroadcast(context, id.hashCode(), intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle("파일 업로드 중").setContentText("취소할 수 있습니다").setOngoing(true)
        .addAction(android.R.drawable.ic_menu_close_clear_cancel, "취소", cancel);
    if (progress >= 0) builder.setProgress(100, progress, false).setContentText("업로드 " + progress + "% · 취소할 수 있습니다");
    return builder.build();
  }
  static ForegroundInfo foreground(Context context, String id) { return new ForegroundInfo(id.hashCode(), upload(context, id), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC); }
}
