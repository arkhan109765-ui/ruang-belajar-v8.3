from pathlib import Path
import hashlib, hmac, json, os, secrets, sqlite3, time
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

from backend.ai import jelaskan_materi, tanya_materi, buat_paket_belajar, buat_data_game, buat_konsep

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
PDF_DIR = DATA_DIR / "pdf"
DATA_DIR.mkdir(exist_ok=True); PDF_DIR.mkdir(exist_ok=True)
DB = DATA_DIR / "ruang_belajar.db"
MAX_PDF = 25 * 1024 * 1024

app = FastAPI(title="Ruang Belajar", version="6.0.0")
app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")

# ---------- database ----------
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c
with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT "student", created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS materials(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL, filename TEXT, pdf_path TEXT, text TEXT NOT NULL, explanation TEXT, concepts TEXT, games TEXT, exam TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS progress(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, material_id INTEGER NOT NULL, exp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0, games INTEGER DEFAULT 0, exams INTEGER DEFAULT 0, best_score INTEGER DEFAULT 0, updated_at INTEGER NOT NULL, UNIQUE(user_id, material_id));
    """)
    try: c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
    except sqlite3.OperationalError: pass


def pw_hash(pw: str, salt: Optional[bytes] = None):
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex() + ":" + key.hex()

def pw_check(pw, stored):
    try:
        salt, key = stored.split(":",1)
        got = pw_hash(pw, bytes.fromhex(salt)).split(":",1)[1]
        return hmac.compare_digest(got, key)
    except Exception:
        return False

OWNER_USERNAME = "davian"
# Password tidak diubah. Akun Davian yang sudah ada akan dinaikkan menjadi owner.
OWNER_PASSWORD = None
with db() as c:
    row = c.execute("SELECT id FROM users WHERE username=?", (OWNER_USERNAME,)).fetchone()
    if row:
        # Akun Davian yang sudah dibuat pengguna tetap memakai password lamanya.
        c.execute("UPDATE users SET role=? WHERE username=?", ("owner", OWNER_USERNAME))
    # Tidak membuat password baru untuk Davian agar password akun yang sudah dipakai tidak berubah.


def user_from_token(token: str):
    if not token: raise HTTPException(401, "Silakan masuk terlebih dahulu.")
    now=int(time.time())
    with db() as c:
        row=c.execute("SELECT user_id FROM sessions WHERE token=? AND expires_at>?",(token,now)).fetchone()
    if not row: raise HTTPException(401,"Sesi habis. Silakan masuk lagi.")
    return row["user_id"]

def auth(authorization: Optional[str]):
    token=(authorization or "").replace("Bearer ","").strip()
    return user_from_token(token)

# ---------- models ----------
class AuthReq(BaseModel): username: str = Field(min_length=3,max_length=32); password: str = Field(min_length=6,max_length=128)
class TextReq(BaseModel): material_id: int
class AskReq(BaseModel): material_id: int; question: str = Field(min_length=1,max_length=3000)
class ProgressReq(BaseModel): material_id: int; exp: int=Field(ge=0,le=1000000); streak: int=Field(ge=0,le=100000); games: int=Field(ge=0,le=100000); exams: int=Field(ge=0,le=100000); best_score: int=Field(ge=0,le=100)

# ---------- pages ----------
@app.get("/")
def root(): return FileResponse(FRONTEND_DIR/"index.html")
@app.get("/manifest.json")
def manifest(): return FileResponse(FRONTEND_DIR/"manifest.json", media_type="application/manifest+json")
@app.get("/service-worker.js")
def sw(): return FileResponse(FRONTEND_DIR/"service-worker.js", media_type="application/javascript")

@app.get("/api/status")
def status(): return {"status":"ok"}

# ---------- auth ----------
@app.post("/api/auth/register")
def register(r: AuthReq):
    u=r.username.strip().lower()
    if not u.replace("_", "").isalnum(): raise HTTPException(400,"Username hanya boleh berisi huruf, angka, dan underscore.")
    if u == OWNER_USERNAME: raise HTTPException(400,"Username tersebut sudah dipakai.")
    with db() as c:
        try:
            c.execute("INSERT INTO users(username,password,role,created_at) VALUES(?,?,?,?)", (u,pw_hash(r.password),"student",int(time.time())))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Username sudah digunakan.")
    return {"ok":True,"username":u}
@app.post("/api/auth/login")
def login(r: AuthReq):
    u=r.username.strip().lower()
    with db() as c: row=c.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone()
    if not row or not pw_check(r.password,row["password"]): raise HTTPException(401,"Username atau password salah.")
    token=secrets.token_urlsafe(32)
    with db() as c: c.execute("INSERT INTO sessions VALUES(?,?,?)",(token,row["id"],int(time.time())+60*60*24*30))
    return {"token":token,"username":u,"role":row["role"]}
@app.get("/api/me")
def me(authorization: Optional[str]=Header(None)):
    uid=auth(authorization)
    with db() as c: row=c.execute("SELECT username, role FROM users WHERE id=?",(uid,)).fetchone()
    if row["role"] == "owner":
        return {"username":row["username"],"role":"owner","exp":1000000,"level":2001,"streak":100000,"best_score":100}
    return {"username":row["username"],"role":row["role"],"exp":0,"level":1,"streak":0,"best_score":0}

# ---------- materials ----------
def is_owner(uid):
    with db() as c: r=c.execute("SELECT role FROM users WHERE id=?",(uid,)).fetchone()
    return bool(r and r["role"]=="owner")

def material_row(uid, mid):
    with db() as c: row=c.execute("SELECT * FROM materials WHERE id=? AND user_id=?",(mid,uid)).fetchone()
    if not row: raise HTTPException(404,"Materi tidak ditemukan.")
    return row

def safe_title(name):
    name=Path(name).stem.strip() or "Materi PDF"
    return name[:100]

def extract_pdf(data: bytes):
    import io
    reader=PdfReader(io.BytesIO(data))
    parts=[]
    for i,p in enumerate(reader.pages):
        try:
            t=p.extract_text() or ""
        except Exception: t=""
        if t.strip(): parts.append(f"[Halaman {i+1}]\n{t.strip()}")
    text="\n\n".join(parts).strip()
    return text[:120000], len(reader.pages)

@app.post("/api/materials/text")
def create_text(payload: dict, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); title=str(payload.get("title") or "Materi baru").strip()[:100]; text=str(payload.get("text") or "").strip()
    if len(text)<20: raise HTTPException(400,"Materi terlalu pendek.")
    now=int(time.time())
    with db() as c:
        cur=c.execute("INSERT INTO materials(user_id,title,source,filename,pdf_path,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(uid,title,"text",None,None,text,now,now))
        mid=cur.lastrowid
        c.execute("INSERT INTO progress(user_id,material_id,exp,level,streak,games,exams,best_score,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (uid,mid,1000000,2001,100000,0,0,100,now) if is_owner(uid) else (uid,mid,0,1,0,0,0,0,now))
    return {"id":mid,"title":title}

@app.post("/api/materials/pdf")
async def create_pdf(file: UploadFile=File(...), authorization: Optional[str]=Header(None)):
    uid=auth(authorization)
    if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400,"File harus PDF.")
    data=await file.read()
    if len(data)>MAX_PDF: raise HTTPException(413,"PDF maksimal 25 MB.")
    try: text,pages=extract_pdf(data)
    except Exception as e: raise HTTPException(400,f"PDF tidak bisa dibaca: {e}")
    if len(text)<30: raise HTTPException(400,"PDF tidak memiliki teks yang bisa dibaca. PDF scan/gambar perlu OCR.")
    now=int(time.time()); mid=None
    with db() as c:
        cur=c.execute("INSERT INTO materials(user_id,title,source,filename,pdf_path,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(uid,safe_title(file.filename or "Materi PDF"),"pdf",file.filename,None,text,now,now))
        mid=cur.lastrowid
        path=PDF_DIR/f"{uid}_{mid}.pdf"; path.write_bytes(data)
        c.execute("UPDATE materials SET pdf_path=? WHERE id=?",(str(path),mid))
        c.execute("INSERT INTO progress(user_id,material_id,exp,level,streak,games,exams,best_score,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (uid,mid,1000000,2001,100000,0,0,100,now) if is_owner(uid) else (uid,mid,0,1,0,0,0,0,now))
    return {"id":mid,"title":safe_title(file.filename or "Materi PDF"),"pages":pages}

@app.get("/api/materials")
def list_materials(authorization: Optional[str]=Header(None)):
    uid=auth(authorization)
    with db() as c: rows=c.execute("SELECT id,title,source,filename,created_at,updated_at FROM materials WHERE user_id=? ORDER BY updated_at DESC",(uid,)).fetchall()
    return [dict(x) for x in rows]

@app.get("/api/materials/{mid}")
def get_material(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    p=progress_for(uid,mid)
    return {"id":r["id"],"title":r["title"],"source":r["source"],"filename":r["filename"],"text":r["text"],"explanation":json.loads(r["explanation"]) if r["explanation"] else None,"concepts":json.loads(r["concepts"]) if r["concepts"] else [],"games":json.loads(r["games"]) if r["games"] else None,"exam":json.loads(r["exam"]) if r["exam"] else None,"progress":p}

@app.delete("/api/materials/{mid}")
def delete_material(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    if r["pdf_path"]:
        try: Path(r["pdf_path"]).unlink(missing_ok=True)
        except Exception: pass
    with db() as c: c.execute("DELETE FROM materials WHERE id=? AND user_id=?",(mid,uid))
    return {"ok":True}

@app.get("/api/materials/{mid}/pdf")
def pdf(mid:int, token:str=Query(...)):
    uid=user_from_token(token); r=material_row(uid,mid)
    if not r["pdf_path"] or not Path(r["pdf_path"]).exists(): raise HTTPException(404,"PDF tidak tersedia.")
    return FileResponse(r["pdf_path"],media_type="application/pdf",filename=r["filename"] or "materi.pdf")

# ---------- AI ----------
def progress_for(uid,mid):
    if is_owner(uid):
        now=int(time.time())
        with db() as c:
            c.execute("INSERT INTO progress(user_id,material_id,exp,level,streak,games,exams,best_score,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,material_id) DO UPDATE SET exp=1000000,level=2001,streak=100000,best_score=100,updated_at=excluded.updated_at", (uid,mid,1000000,2001,100000,0,0,100,now))
            r=c.execute("SELECT * FROM progress WHERE user_id=? AND material_id=?",(uid,mid)).fetchone()
        return dict(r)
    with db() as c: r=c.execute("SELECT * FROM progress WHERE user_id=? AND material_id=?",(uid,mid)).fetchone()
    if not r:return {"exp":0,"level":1,"streak":0,"games":0,"exams":0,"best_score":0}
    return dict(r)

def save_ai(uid,mid,key,val):
    with db() as c:
        c.execute(f"UPDATE materials SET {key}=?,updated_at=? WHERE id=? AND user_id=?",(json.dumps(val,ensure_ascii=False),int(time.time()),mid,uid))

def text_of(uid,mid): return material_row(uid,mid)["text"]

@app.post("/api/materials/{mid}/explain")
def explain(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    try: d=jelaskan_materi(r["text"])
    except Exception as e: raise HTTPException(502,f"AI gagal menjelaskan materi: {e}")
    save_ai(uid,mid,"explanation",d); save_ai(uid,mid,"concepts",d.get("konsep",[]))
    return d
@app.post("/api/materials/{mid}/concepts")
def concepts(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    try:
        concepts=buat_konsep(r["text"])
    except Exception as e:
        raise HTTPException(502,f"AI gagal membuat konsep: {e}")
    save_ai(uid,mid,"concepts",concepts)
    return {"konsep": concepts}

@app.post("/api/materials/{mid}/ask")
def ask(mid:int, q:AskReq, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); text=text_of(uid,mid)
    try:return {"hasil":tanya_materi(text,q.question)}
    except Exception as e: raise HTTPException(502,f"AI gagal menjawab: {e}")
@app.post("/api/materials/{mid}/games")
def games(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    try:d=buat_data_game(r["text"])
    except Exception as e: raise HTTPException(502,f"AI gagal membuat game: {e}")
    save_ai(uid,mid,"games",d); return d
@app.post("/api/materials/{mid}/exam")
def exam(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); r=material_row(uid,mid)
    try:d=buat_paket_belajar(r["text"])
    except Exception as e: raise HTTPException(502,f"AI gagal membuat soal: {e}")
    save_ai(uid,mid,"exam",d); return d

@app.get("/api/materials/{mid}/progress")
def get_progress(mid:int, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); material_row(uid,mid); return progress_for(uid,mid)
@app.post("/api/materials/{mid}/progress")
def set_progress(mid:int, p:ProgressReq, authorization: Optional[str]=Header(None)):
    uid=auth(authorization); material_row(uid,mid)
    if is_owner(uid):
        p = ProgressReq(material_id=mid, exp=1000000, streak=100000, games=p.games, exams=p.exams, best_score=100)
    level=max(1,p.exp//500+1); now=int(time.time())
    with db() as c:
        c.execute("INSERT INTO progress(user_id,material_id,exp,level,streak,games,exams,best_score,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,material_id) DO UPDATE SET exp=excluded.exp,level=excluded.level,streak=excluded.streak,games=excluded.games,exams=excluded.exams,best_score=MAX(progress.best_score,excluded.best_score),updated_at=excluded.updated_at",(uid,mid,p.exp,level,p.streak,p.games,p.exams,p.best_score,now))
    return progress_for(uid,mid)
