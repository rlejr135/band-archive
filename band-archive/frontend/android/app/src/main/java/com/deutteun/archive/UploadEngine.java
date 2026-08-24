package com.deutteun.archive;

import android.content.Context;
import android.net.Uri;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import org.json.*;

/** Executes the capability-protected multipart contract without exposing its token. */
public final class UploadEngine {
  public enum Outcome { SUCCESS, RETRY, FAILURE }
  private static final int MAX_ATTEMPTS = 4;
  private static final ThreadLocal<Execution> EXECUTION = new ThreadLocal<>();
  private static final Map<String, Execution> ACTIVE = new ConcurrentHashMap<>();
  private UploadEngine() {}

  public static Outcome run(Context context, String id) {
    UploadStore store = new UploadStore(context); UploadStore.Task task = store.get(id);
    if (task == null || "cancelled".equals(task.state) || "completed".equals(task.state)) return Outcome.SUCCESS;
    String owner = UUID.randomUUID().toString(); if (!store.acquire(id, owner)) return Outcome.SUCCESS;
    Execution execution = new Execution(id, owner); EXECUTION.set(execution); ACTIVE.put(id, execution);
    try { try {
      File file = durableFile(task); verifyFile(file, task);
      task.state = "preparing"; update(store, task, execution);
      Session session = openOrResume(task, store, execution);
      requireLease(store, task, execution);
      JSONObject remote = get(task.api + "/uploads/multipart/" + session.id, session.capability);
      String remoteState = remote.optString("status");
      if ("completed".equals(remoteState)) { task.result = minimalResult(remote); task.progress = 100; task.state = queueState(remote); update(store, task, execution); deleteSource(task); return "failed".equals(task.state) ? Outcome.FAILURE : Outcome.SUCCESS; }
      if ("expired".equals(remoteState) || "aborted".equals(remoteState) || "failed".equals(remoteState)) throw new TerminalException("업로드 세션이 " + remoteState + " 상태입니다.");
      Set<Integer> acked = ackedParts(remote);
      long partSize = remote.optLong("part_size", session.partSize);
      if (partSize <= 0) throw new TerminalException("서버가 유효한 분할 크기를 반환하지 않았습니다.");
      int parts = (int) Math.ceil((double) task.bytes / partSize);
      for (int part = 1; part <= parts; part++) if (!acked.contains(part)) uploadPart(task, store, file, session, part, partSize, execution);
      task.state = "completing"; update(store, task, execution);
      requireLease(store, task, execution);
      JSONObject result = post(task.api + "/uploads/multipart/" + session.id + "/complete", new JSONObject(), session.capability);
      task.result = minimalResult(result); task.progress = 100;
      task.state = queueState(result);
      // "processing" deliberately remains nonterminal: upload ownership ends here and UI polling owns status observation.
      update(store, task, execution); deleteSource(task); return Outcome.SUCCESS;
    } catch (LeaseLostException lost) { return Outcome.SUCCESS; }
      catch (TerminalException error) { return fail(store, task, execution, error, false); }
      catch (Exception error) { return fail(store, task, execution, error, true); }
    } finally { ACTIVE.remove(id, execution); store.release(id, owner); EXECUTION.remove(); }
  }

  public static void abort(Context context, String id) {
    UploadStore store = new UploadStore(context); store.cancel(id); Execution running=ACTIVE.get(id); if(running!=null)running.stop(); UploadStore.Task task = store.get(id); if (task == null) return;
    try { if (task.session != null && task.capability != null) post(task.api + "/uploads/multipart/" + task.session + "/abort", new JSONObject(), task.capability); }
    catch (Exception ignored) { /* server abort is idempotent; local cancellation still stops the job */ }
    deleteSource(task);
  }

  private static Outcome fail(UploadStore store, UploadStore.Task task, Execution execution, Exception error, boolean retryable) {
    if (execution.stopped || Thread.currentThread().isInterrupted()) return Outcome.SUCCESS;
    UploadStore.Task latest = store.get(task.id); if (latest == null || !UploadLeasePolicy.canEngineWrite(latest.state, latest.leaseOwner, execution.owner)) { execution.stop(); return Outcome.SUCCESS; }
    task.attempts++; task.error = userMessage(error);
    task.state = retryable && task.attempts < MAX_ATTEMPTS ? "retry_wait" : "failed";
    try { update(store, task, execution); } catch (LeaseLostException lost) { return Outcome.SUCCESS; }
    return "retry_wait".equals(task.state) ? Outcome.RETRY : Outcome.FAILURE;
  }
  private static void update(UploadStore s, UploadStore.Task t, Execution execution) throws LeaseLostException { if(!s.updateForOwner(t,execution.owner)) lost(execution); UploadEvents.emit(t); }
  private static void requireLease(UploadStore s, UploadStore.Task t, Execution execution) throws LeaseLostException { if(!s.renew(t.id,execution.owner)) lost(execution); }
  private static void lost(Execution execution) throws LeaseLostException { execution.stop(); throw new LeaseLostException(); }
  private static File durableFile(UploadStore.Task t) throws TerminalException {
    Uri uri = Uri.parse(t.uri); if (!"file".equals(uri.getScheme()) || uri.getPath() == null) throw new TerminalException("지속 저장된 파일을 찾을 수 없습니다.");
    return new File(uri.getPath());
  }
  private static void deleteSource(UploadStore.Task t) { try { Uri uri=Uri.parse(t.uri); if ("file".equals(uri.getScheme()) && uri.getPath()!=null) new File(uri.getPath()).delete(); } catch (Exception ignored) {} }
  private static void verifyFile(File f, UploadStore.Task t) throws TerminalException {
    if (!f.exists() || f.length() != t.bytes) throw new TerminalException("선택한 파일이 변경되었거나 없어졌습니다.");
    if (t.fingerprint != null && !t.fingerprint.equals(sha256(f))) throw new TerminalException("선택한 파일의 내용이 변경되었습니다.");
  }
  private static String sha256(File file) throws TerminalException { try {
    MessageDigest digest = MessageDigest.getInstance("SHA-256"); try (InputStream in = new FileInputStream(file)) { byte[] bytes = new byte[64 * 1024]; int read; while ((read = in.read(bytes)) != -1) digest.update(bytes, 0, read); }
    StringBuilder text = new StringBuilder(); for (byte value : digest.digest()) text.append(String.format("%02x", value)); return text.toString();
  } catch (Exception error) { throw new TerminalException("업로드 파일을 확인하지 못했습니다."); } }
  private static Session openOrResume(UploadStore.Task t, UploadStore s, Execution execution) throws Exception {
    if (t.session != null && t.capability != null) return new Session(t.session, t.capability, 0);
    if (t.session != null && "credential_lost".equals(t.error)) throw new TerminalException("업로드 인증 정보를 복구하지 못했습니다. 파일을 다시 선택하세요.");
    requireLease(s,t,execution); JSONObject init = post(t.api + "/uploads/multipart/initiate", new JSONObject(t.target)
        .put("filename", t.name).put("content_type", t.mime).put("declared_bytes", t.bytes), null);
    String capability = init.optString("upload_capability_token"); if (capability.isEmpty()) throw new TerminalException("업로드 권한 토큰을 받지 못했습니다.");
    t.session = init.getString("session_id"); t.capability = capability; update(s, t, execution);
    return new Session(t.session, capability, init.getLong("part_size"));
  }
  private static Set<Integer> ackedParts(JSONObject status) {
    Set<Integer> acked = new HashSet<>(); JSONArray parts = status.optJSONArray("parts");
    if (parts == null) parts = status.optJSONArray("ack_parts");
    if (parts == null) parts = status.optJSONArray("acknowledged_parts");
    if (parts != null) for (int i = 0; i < parts.length(); i++) { JSONObject p = parts.optJSONObject(i); if (p != null && "acknowledged".equals(p.optString("status", "acknowledged"))) acked.add(p.optInt("part_number", p.optInt("partNumber", 0))); }
    acked.remove(0); return acked;
  }
  private static void uploadPart(UploadStore.Task t, UploadStore s, File f, Session session, int number, long partSize, Execution execution) throws Exception {
    long offset = UploadProtocol.partStart(number, partSize), length = UploadProtocol.partLength(t.bytes, number, partSize); Exception last = null;
    for (int attempt = 0; attempt < MAX_ATTEMPTS; attempt++) try {
      requireLease(s,t,execution); JSONObject issued = post(t.api + "/uploads/multipart/" + session.id + "/parts", new JSONObject().put("part_number", number), session.capability);
      final long[] lastReported = { -1 };
      String etag = put(issued.getString("upload_url"), f, offset, length, t.mime, sent -> {
        int progress = (int) Math.min(99, ((offset + sent) * 100) / t.bytes);
        if (progress != lastReported[0]) { lastReported[0] = progress; t.state = "uploading"; t.progress = progress; update(s, t, execution); }
      });
      requireLease(s,t,execution);
      post(t.api + "/uploads/multipart/" + session.id + "/parts/" + number + "/ack", new JSONObject().put("etag", etag).put("bytes", length), session.capability);
      t.state = "uploading"; t.progress = (int) Math.min(99, ((offset + length) * 100) / t.bytes); update(s, t, execution); return;
    } catch (LeaseLostException e) { throw e;
    } catch (HttpException e) {
      last = e; if (!e.retryable || attempt == MAX_ATTEMPTS - 1) break; retry(t, s, attempt, execution);
    } catch (IOException e) {
      last = e; if (attempt == MAX_ATTEMPTS - 1) break; retry(t, s, attempt, execution);
    }
    if (last instanceof HttpException && !((HttpException) last).retryable) throw new TerminalException(last.getMessage());
    throw last == null ? new IOException("분할 업로드에 실패했습니다.") : last;
  }
  private static void retry(UploadStore.Task t, UploadStore s, int attempt, Execution execution) throws InterruptedException, LeaseLostException { t.state = "retry_wait"; update(s, t, execution); Thread.sleep(UploadProtocol.retryDelayMs(attempt)); }
  private static JSONObject get(String url, String cap) throws Exception { HttpURLConnection c = connection(url, "GET", cap); try{return json(c);}finally{close(c);} }
  private static JSONObject post(String url, JSONObject body, String cap) throws Exception {
    HttpURLConnection c = connection(url, "POST", cap); c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
    try { try (OutputStream out = c.getOutputStream()) { out.write(body.toString().getBytes(StandardCharsets.UTF_8)); } return json(c); } finally { close(c); }
  }
  private static HttpURLConnection connection(String url, String method, String cap) throws Exception {
    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection(); c.setRequestMethod(method); c.setConnectTimeout(20_000); c.setReadTimeout(60_000);
    if (cap != null) c.setRequestProperty("X-Upload-Capability", cap); Execution execution=EXECUTION.get(); if(execution!=null)execution.track(c); return c;
  }
  private interface Progress { void onBytes(long sent) throws Exception; }
  private static String put(String url, File f, long offset, long length, String mime, Progress progress) throws Exception {
    HttpURLConnection c = connection(url, "PUT", null); c.setDoOutput(true); c.setFixedLengthStreamingMode(length); c.setRequestProperty("Content-Type", mime);
    try { try (RandomAccessFile in = new RandomAccessFile(f, "r"); OutputStream out = c.getOutputStream()) { in.seek(offset); byte[] buf = new byte[64 * 1024]; long remaining = length; while (remaining > 0) { int read = in.read(buf, 0, (int) Math.min(buf.length, remaining)); if (read < 0) throw new EOFException(); out.write(buf, 0, read); remaining -= read; progress.onBytes(length - remaining); } }
      int code = c.getResponseCode(); if (code / 100 != 2) throw new HttpException(code, "R2 업로드 실패 (" + code + ")"); String etag = c.getHeaderField("ETag");
      if (etag == null || etag.isEmpty()) throw new TerminalException("R2 ETag 헤더가 보이지 않습니다. CORS expose headers를 확인하세요."); return etag.replace("\"", "");
    } finally { close(c); }
  }
  private static void close(HttpURLConnection c){Execution execution=EXECUTION.get();if(execution!=null)execution.clear(c);c.disconnect();}
  private static JSONObject json(HttpURLConnection c) throws Exception {
    int code = c.getResponseCode(); InputStream source = code / 100 == 2 ? c.getInputStream() : c.getErrorStream(); StringBuilder body = new StringBuilder();
    if (source != null) try (BufferedReader reader = new BufferedReader(new InputStreamReader(source, StandardCharsets.UTF_8))) { String line; while ((line = reader.readLine()) != null) body.append(line); }
    if (code / 100 != 2) throw new HttpException(code, "HTTP " + code + (body.length() == 0 ? "" : ": " + body)); return new JSONObject(body.toString());
  }
  private static String userMessage(Exception e) { String m = e.getMessage(); return m == null ? "업로드 중 알 수 없는 오류가 발생했습니다." : m; }
  private static JSONObject entity(JSONObject value) { JSONObject result=value.optJSONObject("result"); if(result!=null)value=result; JSONObject item=value.optJSONObject("media"); if(item==null)item=value.optJSONObject("personal_log"); return item==null?value:item; }
  private static String queueState(JSONObject value) { String status=entity(value).optString("transcoding_status", entity(value).optString("status")); return "failed".equals(status)?"failed":"completed".equals(status)?"completed":"processing"; }
  private static String minimalResult(JSONObject value) throws JSONException {
    JSONObject item = entity(value); String status=item.optString("transcoding_status",item.optString("status"));
    return new JSONObject().put("id", item.opt("id")).put("status", status).put("transcoding_status", status).toString();
  }
  private static final class Execution { final String id,owner; final Thread thread=Thread.currentThread(); volatile HttpURLConnection active; volatile boolean stopped; Execution(String id,String owner){this.id=id;this.owner=owner;} void track(HttpURLConnection c) throws LeaseLostException {active=c;if(stopped){c.disconnect();throw new LeaseLostException();}} void clear(HttpURLConnection c){if(active==c)active=null;} void stop(){stopped=true;HttpURLConnection c=active;if(c!=null)c.disconnect();thread.interrupt();} }
  private static final class Session { final String id, capability; final long partSize; Session(String i, String c, long p) { id=i; capability=c; partSize=p; } }
  private static final class LeaseLostException extends IOException { LeaseLostException(){super("업로드 실행 권한이 만료되었습니다.");} }
  private static final class TerminalException extends Exception { TerminalException(String message) { super(message); } }
  private static final class HttpException extends IOException { final boolean retryable; HttpException(int status, String message) { super(message); retryable=status==401 || status==403 || status==408 || status==429 || status>=500; } }
}
