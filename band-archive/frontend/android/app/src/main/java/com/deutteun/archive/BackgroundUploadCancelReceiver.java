package com.deutteun.archive; import android.content.*;
public class BackgroundUploadCancelReceiver extends BroadcastReceiver { @Override public void onReceive(Context c,Intent i){String id=i.getStringExtra("id");UploadScheduler.cancel(c,id);new Thread(()->UploadEngine.abort(c,id)).start();} }
