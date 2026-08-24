package com.deutteun.archive;

import static org.junit.Assert.*;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import java.io.File;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.util.Set;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** Uses MockWebServer while invoking UploadEngine's production HttpURLConnection helpers. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class UploadEngineHttpIntegrationTest {
  private MockWebServer server;
  @Before public void setUp() throws Exception { server = new MockWebServer(); server.start(); }
  @After public void tearDown() throws Exception { server.shutdown(); }

  @Test public void productionHttpLayerReadsAcknowledgementsReissuesPartUrlAndOrdersPutAckComplete() throws Exception {
    server.enqueue(json("{\"status\":\"uploading\",\"part_size\":4,\"parts\":[{\"part_number\":1,\"status\":\"acknowledged\"}]}"));
    server.enqueue(json("{\"upload_url\":\"" + server.url("/r2/part-2") + "\"}"));
    server.enqueue(new MockResponse().setResponseCode(200).setHeader("ETag", "\"part-two\""));
    server.enqueue(json("{}"));
    server.enqueue(json("{\"status\":\"completed\"}"));
    server.enqueue(json("{\"upload_url\":\"" + server.url("/r2/reissued") + "\"}"));

    JSONObject status = get(server.url("/uploads/multipart/session").toString(), "capability");
    assertEquals("uploading", status.getString("status"));
    assertTrue(acked(status).contains(1)); // Engine will skip this server-acknowledged part.
    JSONObject firstUrl = post(server.url("/uploads/multipart/session/parts").toString(), new JSONObject().put("part_number", 2), "capability");
    File part = Files.createTempFile("upload-engine", ".bin").toFile(); Files.write(part.toPath(), new byte[] { 1, 2, 3, 4 });
    assertEquals("part-two", put(firstUrl.getString("upload_url"), part, 0, 4, "video/mp4"));
    post(server.url("/uploads/multipart/session/parts/2/ack").toString(), new JSONObject().put("etag", "part-two").put("bytes", 4), "capability");
    post(server.url("/uploads/multipart/session/complete").toString(), new JSONObject(), "capability");
    JSONObject reissued = post(server.url("/uploads/multipart/session/parts").toString(), new JSONObject().put("part_number", 2), "capability");
    assertTrue(reissued.getString("upload_url").endsWith("/r2/reissued"));
    part.delete();

    assertRequest("GET", "/uploads/multipart/session", true, server.takeRequest());
    assertRequest("POST", "/uploads/multipart/session/parts", true, server.takeRequest());
    assertRequest("PUT", "/r2/part-2", false, server.takeRequest());
    assertRequest("POST", "/uploads/multipart/session/parts/2/ack", true, server.takeRequest());
    assertRequest("POST", "/uploads/multipart/session/complete", true, server.takeRequest());
    assertRequest("POST", "/uploads/multipart/session/parts", true, server.takeRequest());
  }

  private static MockResponse json(String body) { return new MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(body); }
  private static void assertRequest(String method, String path, boolean capability, RecordedRequest request) { assertEquals(method, request.getMethod()); assertEquals(path, request.getPath()); assertEquals(capability ? "capability" : null, request.getHeader("X-Upload-Capability")); }
  @SuppressWarnings("unchecked") private static Set<Integer> acked(JSONObject value) throws Exception { Method method=UploadEngine.class.getDeclaredMethod("ackedParts",JSONObject.class);method.setAccessible(true);return (Set<Integer>)method.invoke(null,value); }
  private static JSONObject get(String url,String capability)throws Exception{Method method=UploadEngine.class.getDeclaredMethod("get",String.class,String.class);method.setAccessible(true);return (JSONObject)method.invoke(null,url,capability);}
  private static JSONObject post(String url,JSONObject body,String capability)throws Exception{Method method=UploadEngine.class.getDeclaredMethod("post",String.class,JSONObject.class,String.class);method.setAccessible(true);return (JSONObject)method.invoke(null,url,body,capability);}
  private static String put(String url,File file,long offset,long length,String mime)throws Exception{Class<?> progress=Class.forName("com.deutteun.archive.UploadEngine$Progress");Object callback=Proxy.newProxyInstance(progress.getClassLoader(),new Class<?>[]{progress},(proxy,method,args)->null);Method method=UploadEngine.class.getDeclaredMethod("put",String.class,File.class,long.class,long.class,String.class,progress);method.setAccessible(true);return (String)method.invoke(null,url,file,offset,length,mime,callback);}
}
