package com.deutteun.archive;

import static org.junit.Assert.*;
import androidx.test.core.app.ApplicationProvider;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** Covers encrypted session rotation and account binding without putting tokens in task rows. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class SecureAuthSessionStoreRobolectricIntegrationTest {
  private MockWebServer server;
  @Before public void setUp() throws Exception { server=new MockWebServer();server.start();ApplicationProvider.getApplicationContext().getSharedPreferences("background_upload_auth_v1",0).edit().clear().commit(); }
  @After public void tearDown() throws Exception { server.shutdown(); }

  @Test public void rotatesExpiredSessionOnlyForItsAccountAndPersistsTheRotatedRefreshToken() throws Exception {
    SecureAuthSessionStore store=new SecureAuthSessionStore(ApplicationProvider.getApplicationContext());
    store.configure(server.url("/").toString(),"anon-key");
    store.setSession(session("old-access","old-refresh",0,"account-a").toString());
    server.enqueue(json(session("new-access","new-refresh",4_000_000_000L,"account-a").toString()));
    assertEquals("new-access",store.accessTokenFor("account-a"));
    assertNull(store.accessTokenFor("account-b"));
    JSONObject persisted=new JSONObject(store.getSession());assertEquals("new-refresh",persisted.getString("refresh_token"));
    RecordedRequest request=server.takeRequest();assertEquals("/auth/v1/token?grant_type=refresh_token",request.getPath());assertEquals("anon-key",request.getHeader("apikey"));assertEquals("Bearer anon-key",request.getHeader("Authorization"));assertTrue(request.getBody().readUtf8().contains("old-refresh"));
  }

  @Test public void logoutClearRemovesTheOnlyPersistedSession() throws Exception {
    SecureAuthSessionStore store=new SecureAuthSessionStore(ApplicationProvider.getApplicationContext());store.setSession(session("access","refresh",4_000_000_000L,"account-a").toString());
    assertEquals("account-a",store.accountId());store.removeSession();assertNull(store.getSession());assertNull(store.accountId());
  }

  @Test public void storedSessionIsAccountBoundAndNeverWrittenAsPlaintext() throws Exception {
    String access="access-secret",refresh="refresh-secret";
    SecureAuthSessionStore store=new SecureAuthSessionStore(ApplicationProvider.getApplicationContext());store.setSession(session(access,refresh,4_000_000_000L,"account-a").toString());
    assertTrue(store.hasSessionFor("account-a"));assertFalse(store.hasSessionFor("account-b"));
    android.content.SharedPreferences prefs=ApplicationProvider.getApplicationContext().getSharedPreferences("background_upload_auth_v1",0);
    assertFalse(prefs.getAll().values().toString().contains(access));assertFalse(prefs.getAll().values().toString().contains(refresh));
  }

  private static JSONObject session(String access,String refresh,long expires,String account) throws Exception{return new JSONObject().put("access_token",access).put("refresh_token",refresh).put("expires_at",expires).put("user",new JSONObject().put("id",account));}
  private static MockResponse json(String value){return new MockResponse().setResponseCode(200).setHeader("Content-Type","application/json").setBody(value);}
}
