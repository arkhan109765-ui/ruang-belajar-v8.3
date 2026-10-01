from pathlib import Path
import getpass, hashlib, secrets, sqlite3, time

BASE_DIR=Path(__file__).resolve().parent.parent
DATA_DIR=BASE_DIR/"data"
DB=DATA_DIR/"ruang_belajar.db"
DATA_DIR.mkdir(exist_ok=True)

def pw_hash(password):
    salt=secrets.token_bytes(16)
    key=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return salt.hex()+":"+key.hex()

username=input("Username siswa: ").strip().lower()
password=getpass.getpass("Password siswa: ")
confirm=getpass.getpass("Ulangi password: ")
if len(username)<3 or not username.replace("_","").isalnum(): raise SystemExit("Username harus 3-32 karakter dan hanya huruf, angka, atau underscore.")
if len(password)<6: raise SystemExit("Password minimal 6 karakter.")
if password!=confirm: raise SystemExit("Password tidak sama.")
with sqlite3.connect(DB) as c:
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, created_at INTEGER NOT NULL)")
    try: c.execute("INSERT INTO users(username,password,created_at) VALUES(?,?,?)",(username,pw_hash(password),int(time.time())))
    except sqlite3.IntegrityError: raise SystemExit("Username sudah digunakan.")
print(f"Akun {username} berhasil dibuat.")
