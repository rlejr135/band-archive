package com.deutteun.archive;

import android.content.Context;
import android.net.Uri;
import java.io.File;
import java.io.IOException;

/** Keeps deletion constrained to filesDir/background_uploads even if persisted metadata is corrupt. */
public final class UploadFileRetention {
  private UploadFileRetention() {}

  public static File uploadsDir(Context context) { return new File(context.getFilesDir(), "background_uploads"); }

  static boolean isSafeChild(File root, File candidate) {
    try {
      String parent = root.getCanonicalPath();
      String child = candidate.getCanonicalPath();
      return child.startsWith(parent + File.separator);
    } catch (IOException ignored) { return false; }
  }

  public static File safeSource(Context context, String value) {
    try {
      Uri uri = Uri.parse(value);
      if (!"file".equals(uri.getScheme()) || uri.getPath() == null) return null;
      File source = new File(uri.getPath());
      return isSafeChild(uploadsDir(context), source) ? source : null;
    } catch (Exception ignored) { return null; }
  }

  public static boolean deleteSource(Context context, String value) {
    File source = safeSource(context, value);
    return source != null && (!source.exists() || source.delete());
  }

  static boolean deleteFile(File root, File candidate) {
    return isSafeChild(root, candidate) && (!candidate.exists() || candidate.delete());
  }
}
