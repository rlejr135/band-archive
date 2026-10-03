package com.deutteun.archive;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;

/** Android-only Supabase session storage. The full session, including refresh token,
 * is encrypted with an Android Keystore key and never appears in upload task rows. */
final class SecureAuthSessionStore {
  private static final String PREFS="background_upload_auth_v1",KEY="background_upload_auth_key_v1";
  private static final String SESSION="session",SESSION_IV="session_iv",URL_KEY="url",URL_IV="url_iv",ANON="anon",ANON_IV="anon_iv";
  private final SharedPreferences prefs;
  private static final Object REFRESH_LOCK=new Object();
  SecureAuthSessionStore(Context context){prefs=context.getSharedPreferences(PREFS,Context.MODE_PRIVATE);}
  String getSession(){return open(SESSION,SESSION_IV);}
  void setSession(String value){seal(SESSION,SESSION_IV,value);}
  String accountId(){try{JSONObject session=new JSONObject(getSession());JSONObject user=session.optJSONObject("user");return user==null?null:user.optString("id",null);}catch(Exception ignored){return null;}}
  /** A bridge enqueue must never synchronously refresh on the UI thread. */
  boolean hasSessionFor(String accountId){try{return sessionFor(accountId)!=null;}catch(Exception ignored){return false;}}
  void removeSession(){prefs.edit().remove(SESSION).remove(SESSION_IV).commit();}
  void configure(String url,String anon){seal(URL_KEY,URL_IV,url!=null&&url.endsWith("/")?url.substring(0,url.length()-1):url);seal(ANON,ANON_IV,anon);}
  String accessTokenFor(String accountId) throws Exception {
    synchronized(REFRESH_LOCK){
      JSONObject session=sessionFor(accountId); if(session==null)return null;
      long expires=session.optLong("expires_at",0); long now=System.currentTimeMillis()/1000L;
      if(expires>now+60)return session.optString("access_token",null);
      return refresh(session,accountId);
    }
  }
  private JSONObject sessionFor(String accountId) throws Exception {
    String raw=getSession();if(raw==null||accountId==null)return null;
    JSONObject session=new JSONObject(raw);JSONObject user=session.optJSONObject("user");
    return user!=null&&accountId.equals(user.optString("id"))?session:null;
  }
  private String refresh(JSONObject session,String accountId) throws Exception {
    String refresh=session.optString("refresh_token"),url=open(URL_KEY,URL_IV),anon=open(ANON,ANON_IV);
    if(refresh.isEmpty()||url==null||anon==null)return null;
    HttpURLConnection c=(HttpURLConnection)new URL(url+"/auth/v1/token?grant_type=refresh_token").openConnection();
    c.setRequestMethod("POST");c.setConnectTimeout(20_000);c.setReadTimeout(60_000);c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");c.setRequestProperty("apikey",anon);c.setRequestProperty("Authorization","Bearer "+anon);
    try{try(OutputStream out=c.getOutputStream()){out.write(new JSONObject().put("refresh_token",refresh).toString().getBytes(StandardCharsets.UTF_8));}
      int code=c.getResponseCode();InputStream in=code/100==2?c.getInputStream():c.getErrorStream();StringBuilder body=new StringBuilder();if(in!=null)try(BufferedReader reader=new BufferedReader(new InputStreamReader(in,StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null)body.append(line);}
      if(code/100!=2)return null;JSONObject renewed=new JSONObject(body.toString());JSONObject user=renewed.optJSONObject("user");if(user==null||!accountId.equals(user.optString("id")))return null;setSession(renewed.toString());return renewed.optString("access_token",null);
    }finally{c.disconnect();}
  }
  private SecretKey key() throws Exception{KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);if(!ks.containsAlias(KEY)){KeyGenerator g=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");g.init(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());g.generateKey();}return ((KeyStore.SecretKeyEntry)ks.getEntry(KEY,null)).getSecretKey();}
  private void seal(String valueKey,String ivKey,String value){if(value==null){prefs.edit().remove(valueKey).remove(ivKey).commit();return;}try{Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key());if(!prefs.edit().putString(valueKey,Base64.encodeToString(c.doFinal(value.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP)).putString(ivKey,Base64.encodeToString(c.getIV(),Base64.NO_WRAP)).commit())throw new IllegalStateException("secure_auth_storage_unavailable");}catch(Exception e){throw new IllegalStateException("secure_auth_storage_unavailable",e);}}
  private String open(String valueKey,String ivKey){String value=prefs.getString(valueKey,null),iv=prefs.getString(ivKey,null);if(value==null||iv==null)return null;try{Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));return new String(c.doFinal(Base64.decode(value,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception e){return null;}}
}
