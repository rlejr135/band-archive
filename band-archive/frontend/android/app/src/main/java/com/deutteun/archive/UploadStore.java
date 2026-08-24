package com.deutteun.archive;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.HashSet;
import java.util.Set;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Durable task metadata. Capability tokens are AES-GCM encrypted before SQLite writes. */
public final class UploadStore extends SQLiteOpenHelper {
  private static final String KEY = "background_upload_capability_v1";
  public UploadStore(Context c) { super(c, "background_upload.db", null, 3); }
  @Override public void onCreate(SQLiteDatabase db) {
    db.execSQL("CREATE TABLE tasks (id TEXT PRIMARY KEY, work_id INTEGER NOT NULL UNIQUE CHECK(work_id > 0), uri TEXT NOT NULL, name TEXT, mime TEXT, bytes INTEGER, fingerprint TEXT, api TEXT, target TEXT, state TEXT, progress INTEGER, session TEXT, cap TEXT, iv TEXT, error TEXT, result TEXT, attempts INTEGER DEFAULT 0, created INTEGER, updated INTEGER, lease_owner TEXT, lease_expires_at INTEGER DEFAULT 0)");
  }
  @Override public void onUpgrade(SQLiteDatabase db, int o, int n) {
    if(o<2){db.execSQL("ALTER TABLE tasks ADD COLUMN lease_owner TEXT");db.execSQL("ALTER TABLE tasks ADD COLUMN lease_expires_at INTEGER DEFAULT 0");}
    if(o<3) ensureWorkIdSchema(db);
  }
  @Override public void onOpen(SQLiteDatabase db) { super.onOpen(db); if(hasColumn(db,"work_id")){ db.beginTransaction(); try{ensureWorkIdSchema(db);db.setTransactionSuccessful();}finally{db.endTransaction();} } }
  public void insert(Task t) {
    SQLiteDatabase db=getWritableDatabase(); db.beginTransaction(); try {
      int existing=existingWorkId(db,t.id); t.workId=UploadWorkIds.isValid(existing)?existing:allocateWorkId(db);
      long now=System.currentTimeMillis();
      db.execSQL("INSERT OR REPLACE INTO tasks(id,work_id,uri,name,mime,bytes,fingerprint,api,target,state,progress,attempts,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", new Object[]{t.id,t.workId,t.uri,t.name,t.mime,t.bytes,t.fingerprint,t.api,t.target,"preparing",0,0,now,now});
      db.setTransactionSuccessful();
    } finally { db.endTransaction(); }
  }
  public Task get(String id) { Cursor c=getReadableDatabase().rawQuery("SELECT * FROM tasks WHERE id=?",new String[]{id}); try { return c.moveToFirst()?read(c):null; } finally { c.close(); } }
  public Cursor active() { return getReadableDatabase().rawQuery("SELECT * FROM tasks WHERE state NOT IN ('completed','failed','cancelled')",null); }
  public Cursor runnable() { return getReadableDatabase().rawQuery("SELECT * FROM tasks WHERE state IN ('preparing','queued','uploading','retry_wait','completing')",null); }
  /** Terminal rows remain available until their matching UI consumes them, or startup GC expires them. */
  public Cursor retained() { return getReadableDatabase().rawQuery("SELECT * FROM tasks",null); }
  public boolean acknowledge(String id) { return getWritableDatabase().delete("tasks", "id=? AND state IN ('completed','failed','cancelled')", new String[]{id}) > 0; }
  public boolean deleteExpired(String id, long cutoff, long now) { return getWritableDatabase().delete("tasks", "id=? AND state IN ('completed','failed','cancelled') AND updated<=? AND (lease_owner IS NULL OR lease_expires_at<?)", new String[]{id,String.valueOf(cutoff),String.valueOf(now)}) > 0; }
  /** Local/UI transition before scheduling; engine writes must use updateForOwner instead. */
  public void update(Task t) { Sealed sealed=seal(t.capability); getWritableDatabase().execSQL("UPDATE tasks SET state=?,progress=?,session=?,cap=?,iv=?,error=?,result=?,attempts=?,updated=? WHERE id=?",new Object[]{t.state,t.progress,t.session,sealed.cipher,sealed.iv,t.error,t.result,t.attempts,System.currentTimeMillis(),t.id}); }
  /** Atomic engine write: an active matching lease and a nonterminal row are both required. */
  public boolean updateForOwner(Task t,String owner){if(owner==null||owner.isEmpty())return false;long now=System.currentTimeMillis();Sealed sealed=seal(t.capability);android.content.ContentValues v=new android.content.ContentValues();v.put("state",t.state);v.put("progress",t.progress);v.put("session",t.session);v.put("cap",sealed.cipher);v.put("iv",sealed.iv);v.put("error",t.error);v.put("result",t.result);v.put("attempts",t.attempts);v.put("updated",now);v.put("lease_expires_at",now+120_000L);return getWritableDatabase().update("tasks",v,"id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('completed','failed','cancelled')",new String[]{t.id,owner,String.valueOf(now)})>0;}
  public boolean acquire(String id,String owner){if(owner==null||owner.isEmpty())return false;long now=System.currentTimeMillis(),until=now+120_000L;android.content.ContentValues v=new android.content.ContentValues();v.put("lease_owner",owner);v.put("lease_expires_at",until);return getWritableDatabase().update("tasks",v,"id=? AND state NOT IN ('completed','failed','cancelled') AND (lease_owner IS NULL OR lease_expires_at<? OR lease_owner=?)",new String[]{id,String.valueOf(now),owner})>0;}
  public boolean renew(String id,String owner){if(owner==null||owner.isEmpty())return false;long now=System.currentTimeMillis();android.content.ContentValues v=new android.content.ContentValues();v.put("lease_expires_at",now+120_000L);return getWritableDatabase().update("tasks",v,"id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('cancelled','completed','failed')",new String[]{id,owner,String.valueOf(now)})>0;}
  public void release(String id,String owner){getWritableDatabase().execSQL("UPDATE tasks SET lease_owner=NULL,lease_expires_at=0 WHERE id=? AND lease_owner=?",new Object[]{id,owner});}
  /** The cancelled state is the durable cancel marker; clearing the lease happens in this same transaction. */
  public boolean cancel(String id){SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{android.content.ContentValues v=new android.content.ContentValues();v.put("state","cancelled");v.putNull("lease_owner");v.put("lease_expires_at",0);v.put("updated",System.currentTimeMillis());boolean changed=db.update("tasks",v,"id=? AND state NOT IN ('completed','failed','cancelled')",new String[]{id})>0;db.setTransactionSuccessful();return changed;}finally{db.endTransaction();}}
  private Task read(Cursor c) { Task t=new Task(); t.id=c.getString(c.getColumnIndexOrThrow("id")); t.workId=c.getInt(c.getColumnIndexOrThrow("work_id")); t.uri=c.getString(c.getColumnIndexOrThrow("uri")); t.name=c.getString(c.getColumnIndexOrThrow("name")); t.mime=c.getString(c.getColumnIndexOrThrow("mime")); t.bytes=c.getLong(c.getColumnIndexOrThrow("bytes")); t.fingerprint=c.getString(c.getColumnIndexOrThrow("fingerprint")); t.api=c.getString(c.getColumnIndexOrThrow("api")); t.target=c.getString(c.getColumnIndexOrThrow("target")); t.state=c.getString(c.getColumnIndexOrThrow("state")); t.progress=c.getInt(c.getColumnIndexOrThrow("progress")); t.session=c.getString(c.getColumnIndexOrThrow("session")); String cap=c.getString(c.getColumnIndexOrThrow("cap")); t.capability=dec(cap,c.getString(c.getColumnIndexOrThrow("iv"))); t.error=c.getString(c.getColumnIndexOrThrow("error")); if(cap!=null&&t.capability==null)t.error="credential_lost"; t.result=c.getString(c.getColumnIndexOrThrow("result")); t.attempts=c.getInt(c.getColumnIndexOrThrow("attempts")); t.updated=c.getLong(c.getColumnIndexOrThrow("updated")); t.leaseOwner=c.getString(c.getColumnIndexOrThrow("lease_owner")); t.leaseExpires=c.getLong(c.getColumnIndexOrThrow("lease_expires_at")); return t; }
  private static boolean hasColumn(SQLiteDatabase db,String name){Cursor c=db.rawQuery("PRAGMA table_info(tasks)",null);try{while(c.moveToNext())if(name.equals(c.getString(c.getColumnIndexOrThrow("name"))))return true;return false;}finally{c.close();}}
  private void ensureWorkIdSchema(SQLiteDatabase db){
    if(!hasColumn(db,"work_id"))db.execSQL("ALTER TABLE tasks ADD COLUMN work_id INTEGER");
    Set<Integer> used=new HashSet<>(); Cursor c=db.rawQuery("SELECT id,work_id FROM tasks ORDER BY created ASC,id ASC",null); try{while(c.moveToNext()){String id=c.getString(0);Integer current=c.isNull(1)?null:c.getInt(1);int assigned=UploadWorkIds.preserveOrAllocate(current,used);if(current==null||current!=assigned)db.execSQL("UPDATE tasks SET work_id=? WHERE id=?",new Object[]{assigned,id});}}finally{c.close();}
    db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS tasks_work_id_unique ON tasks(work_id)");
  }
  private int existingWorkId(SQLiteDatabase db,String id){Cursor c=db.rawQuery("SELECT work_id FROM tasks WHERE id=?",new String[]{id});try{return c.moveToFirst()&&!c.isNull(0)?c.getInt(0):0;}finally{c.close();}}
  private int allocateWorkId(SQLiteDatabase db){Set<Integer> used=new HashSet<>();Cursor c=db.rawQuery("SELECT work_id FROM tasks WHERE work_id>0",null);int preferred=1;try{while(c.moveToNext()){int value=c.getInt(0);used.add(value);if(value>=preferred)preferred=UploadWorkIds.nextPreferred(value);}}finally{c.close();}return UploadWorkIds.allocateFrom(preferred,used);}
  private SecretKey key() throws Exception { KeyStore ks=KeyStore.getInstance("AndroidKeyStore"); ks.load(null); if(!ks.containsAlias(KEY)){ KeyGenerator g=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore"); g.init(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build()); g.generateKey(); } return ((KeyStore.SecretKeyEntry)ks.getEntry(KEY,null)).getSecretKey(); }
  private Sealed seal(String text) { if(text==null)return new Sealed(null,null); try{ Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key());return new Sealed(Base64.encodeToString(c.doFinal(text.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP),Base64.encodeToString(c.getIV(),Base64.NO_WRAP)); }catch(Exception e){throw new IllegalStateException(e);} }
  private String dec(String cipher,String vector) { if(cipher==null||vector==null)return null; try{ Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(vector,Base64.NO_WRAP)));return new String(c.doFinal(Base64.decode(cipher,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception e){return null;} }
  private static final class Sealed { final String cipher,iv; Sealed(String cipher,String iv){this.cipher=cipher;this.iv=iv;} }
  public static final class Task { public String id,uri,name,mime,fingerprint,api,target,state,session,capability,error,result,leaseOwner; public long bytes,updated,leaseExpires; public int workId,progress,attempts; }
}
