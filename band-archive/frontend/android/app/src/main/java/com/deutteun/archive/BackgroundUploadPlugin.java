package com.deutteun.archive;

import android.app.Activity;
import android.Manifest;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import com.getcapacitor.*;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import androidx.activity.result.ActivityResult;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import android.os.StatFs;

@CapacitorPlugin(name="BackgroundUpload", permissions={@Permission(alias="notifications", strings={Manifest.permission.POST_NOTIFICATIONS})})
public class BackgroundUploadPlugin extends Plugin {
  private UploadStore store;
  private final ExecutorService io = Executors.newSingleThreadExecutor();
  @Override public void load(){ store=new UploadStore(getContext()); cleanupFiles(); UploadEvents.attach(this); UploadScheduler.resumeAll(getContext()); }
  void emitNative(String name, JSObject value) { notifyListeners(name, value, true); }
  private void cleanupFiles(){File dir=new File(getContext().getFilesDir(),"background_uploads");File[] files=dir.listFiles();if(files==null)return;Set<String> active=new HashSet<>();Cursor c=store.active();try{while(c.moveToNext()){UploadStore.Task t=store.get(c.getString(c.getColumnIndexOrThrow("id")));if(t!=null)active.add(Uri.parse(t.uri).getPath());}}finally{c.close();}long cutoff=System.currentTimeMillis()-7L*24*60*60*1000;for(File file:files){if(file.getName().endsWith(".tmp")){file.delete();continue;}if(!active.contains(file.getPath())&&file.lastModified()<cutoff)file.delete();}}
  @PluginMethod public void requestNotificationPermission(PluginCall call){requestPermissionForAlias("notifications",call,"notificationPermissionResult");}
  @PermissionCallback private void notificationPermissionResult(PluginCall call){boolean granted=getPermissionState("notifications")==PermissionState.GRANTED;call.resolve(new JSObject().put("granted",granted).put("backgroundLimited",!granted).put("message",granted?"":"알림 권한이 없으면 백그라운드 업로드 진행 상황이 제한될 수 있습니다."));}
  @PluginMethod public void pickFiles(PluginCall call){ Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("video/*").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_ALLOW_MULTIPLE,call.getBoolean("multiple",true)); i.setFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION); startActivityForResult(call,i,"pickedFiles"); }
  @ActivityCallback private void pickedFiles(PluginCall call,ActivityResult result){ if(result.getResultCode()!=Activity.RESULT_OK||result.getData()==null){call.resolve(new JSObject().put("files",new JSArray()));return;} Intent data=result.getData(); List<Uri> uris=new ArrayList<>(); if(data.getClipData()!=null)for(int n=0;n<data.getClipData().getItemCount();n++)uris.add(data.getClipData().getItemAt(n).getUri()); else uris.add(data.getData()); io.execute(()->{try{JSArray files=new JSArray();for(Uri uri:uris){getContext().getContentResolver().takePersistableUriPermission(uri,Intent.FLAG_GRANT_READ_URI_PERMISSION);files.put(copy(uri));}getActivity().runOnUiThread(()->call.resolve(new JSObject().put("files",files)));}catch(Exception e){getActivity().runOnUiThread(()->call.reject("파일을 앱 저장소로 복사하지 못했습니다.",e));}}); }
  private JSObject copy(Uri uri)throws Exception{ String name="upload";long declared=0;try(Cursor c=getContext().getContentResolver().query(uri,null,null,null,null)){if(c!=null&&c.moveToFirst()){name=c.getString(c.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME));int col=c.getColumnIndex(OpenableColumns.SIZE);if(col>=0)declared=c.getLong(col);}} File dir=new File(getContext().getFilesDir(),"background_uploads");dir.mkdirs();if(new StatFs(dir.getPath()).getAvailableBytes()<declared+16L*1024*1024)throw new IOException("기기에 업로드 파일을 보관할 여유 공간이 부족합니다.");String id=UUID.randomUUID().toString();File temp=new File(dir,id+".tmp"),out=new File(dir,id);try(InputStream in=getContext().getContentResolver().openInputStream(uri);FileOutputStream os=new FileOutputStream(temp)){byte[] b=new byte[64*1024];int r;while((r=in.read(b))!=-1)os.write(b,0,r);os.getFD().sync();}catch(Exception e){temp.delete();throw e;}if(!temp.renameTo(out)){temp.delete();throw new IOException("업로드 파일을 안전하게 저장하지 못했습니다.");} String mime=getContext().getContentResolver().getType(uri); return new JSObject().put("id",id).put("uri",Uri.fromFile(out).toString()).put("name",name).put("mimeType",mime==null?"application/octet-stream":mime).put("size",out.length()).put("fingerprint",sha256(out)); }
  private String sha256(File f)throws Exception{MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(f)){byte[] b=new byte[64*1024];int r;while((r=in.read(b))!=-1)d.update(b,0,r);}StringBuilder s=new StringBuilder();for(byte x:d.digest())s.append(String.format("%02x",x));return s.toString();}
  @PluginMethod public void enqueue(PluginCall call){ try{UploadStore.Task t=new UploadStore.Task();t.id=call.getString("fileId");t.uri=call.getString("uri");t.name=call.getString("name");t.mime=call.getString("mimeType","application/octet-stream");t.bytes=call.getLong("size");t.fingerprint=call.getString("fingerprint");t.api=call.getString("apiUrl");JSObject target=call.getObject("target");t.target=target==null?null:target.toString();if(t.id==null||t.uri==null||t.api==null||t.target==null||t.bytes<=0)throw new IllegalArgumentException("fileId, uri, apiUrl, target, size are required");store.insert(t);t=store.get(t.id);t.state="queued";store.update(t);UploadEvents.emit(getContext(),t);UploadScheduler.schedule(getContext(),t);call.resolve(new JSObject().put("id",t.id).put("state","queued"));}catch(Exception e){call.reject(e.getMessage(),e);} }
  @PluginMethod public void resume(PluginCall call){String id=call.getString("id");if(id!=null){UploadStore.Task t=store.get(id);if(t!=null)UploadScheduler.schedule(getContext(),t);}else UploadScheduler.resumeAll(getContext());call.resolve();}
  @PluginMethod public void cancel(PluginCall call){String id=call.getString("id");UploadScheduler.cancel(getContext(),id);UploadExecutionRegistry.cancel(id);store.cancel(id);new Thread(()->UploadEngine.abort(getContext(),id)).start();UploadStore.Task t=store.get(id);if(t!=null)UploadEvents.emit(getContext(),t);call.resolve();}
  @PluginMethod public void acknowledge(PluginCall call){store.acknowledge(call.getString("id"));call.resolve();}
  @PluginMethod public void listPending(PluginCall call){JSArray out=new JSArray();Cursor c=store.retained();try{while(c.moveToNext()){UploadStore.Task t=store.get(c.getString(c.getColumnIndexOrThrow("id")));out.put(UploadEvents.json(t));}}finally{c.close();}call.resolve(new JSObject().put("items",out).put("supportsBackground",true));}
}
