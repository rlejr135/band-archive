package com.deutteun.archive;

import com.getcapacitor.JSObject;
import android.content.Context;
import java.lang.ref.WeakReference;

public final class UploadEvents {
  private static WeakReference<BackgroundUploadPlugin> plugin = new WeakReference<>(null);
  private UploadEvents() {}
  static void attach(BackgroundUploadPlugin value) { plugin = new WeakReference<>(value); }
  static JSObject json(UploadStore.Task t) {
    JSObject out = new JSObject().put("id", t.id).put("state", t.state).put("progress", t.progress);
    out.put("name", t.name);
    try { out.put("target", new org.json.JSONObject(t.target)); } catch (Exception ignored) {}
    if (t.error != null) out.put("error", t.error);
    if (t.result != null) out.put("result", t.result);
    return out;
  }
  static void emit(Context context, UploadStore.Task task) {
    UploadNotifications.update(context,task);
    BackgroundUploadPlugin active = plugin.get(); if (active == null) return;
    JSObject event = json(task); active.emitNative("state", event);
    if ("uploading".equals(task.state)) active.emitNative("progress", event);
    if ("completed".equals(task.state)) active.emitNative("completed", event);
    if ("failed".equals(task.state)) active.emitNative("failed", event);
  }
}
