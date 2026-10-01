import json, os, re, time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=True)

API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
TIMEOUT_MS = int(os.getenv('GEMINI_TIMEOUT_MS', '120000'))
MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite').strip() or 'gemini-3.5-flash-lite'

client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(timeout=TIMEOUT_MS),
) if API_KEY else None

BASE_INSTRUCTION = '''
Kamu adalah tutor pelajaran sekolah berbahasa Indonesia untuk siswa SMP dan SMA.

ATURAN BAHASA:
1. Gunakan bahasa Indonesia yang sangat sederhana dan mudah dipahami.
2. Gunakan kalimat pendek. Jelaskan satu ide pada satu waktu.
3. Jangan gunakan LaTeX, kode, backtick, atau format rumus seperti \frac dan \sqrt.
4. Untuk matematika, gunakan kata biasa: kali, dibagi, pangkat, akar, persen.
5. Jangan gunakan simbol aneh seperti @, #, $, &, *, {, }, atau backslash dalam jawaban siswa.
6. Tanda sederhana seperti +, -, =, <, >, dan ÷ boleh dipakai jika memang membantu.
7. Jika menjelaskan hitungan, tulis Langkah 1, Langkah 2, dan seterusnya.
8. Jelaskan dari mana setiap angka berasal dan mengapa langkah dilakukan.
9. Jangan mengarang fakta di luar materi.

ATURAN MATERI:
1. Materi pengguna adalah sumber utama.
2. Ulangan dan game harus sesuai dengan konsep yang diajarkan, tetapi tidak boleh menyalin kalimat materi mentah.
3. Ulangan harus menguji pemahaman, penerapan, alasan, urutan langkah, perbandingan, atau kesimpulan.
4. Jika materi tidak cukup, katakan dengan jujur bahwa materi belum cukup.
'''.strip()


def _plain(text: str) -> str:
    """Ubah keluaran AI menjadi bahasa biasa yang nyaman dibaca siswa."""
    if not isinstance(text, str):
        return str(text)
    x=text.replace("\r\n","\n").replace("\r","\n")
    x=re.sub(r"```(?:json|text|markdown|latex|tex)?", "", x, flags=re.I)
    x=x.replace("```", "").replace("`", "")
    x=re.sub(r"\\\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"\1 dibagi \2", x)
    x=re.sub(r"\\\\sqrt\s*\{([^{}]+)\}", r"akar dari \1", x)
    x=x.replace("\\\\times"," kali ").replace("\\\\cdot"," kali ").replace("\\\\div"," dibagi ")
    x=x.replace("\\\\leq"," kurang dari atau sama dengan ").replace("\\\\geq"," lebih dari atau sama dengan ")
    x=x.replace("\\\\neq"," tidak sama dengan ").replace("\\\\pm"," plus atau minus ")
    x=x.replace("\\\\rightarrow"," menjadi ").replace("\\\\to"," menjadi ")
    x=re.sub(r"\\\\text\s*\{([^{}]+)\}", r"\1", x)
    x=re.sub(r"\$([^$]+)\$", r"\1", x)
    x=re.sub(r"([A-Za-z0-9]+)\^\{?([A-Za-z0-9]+)\}?", r"\1 pangkat \2", x)
    x=re.sub(r"(\d+(?:[.,]\d+)?)\s*%", r"\1 persen", x)
    x=x.replace("*"," kali ").replace("@"," ").replace("#"," ").replace("$"," ").replace("&"," dan ")
    x=x.replace("\\"," ").replace("{"," ").replace("}"," ").replace("("," ").replace(")"," ")
    x=re.sub(r"[ \t]+"," ",x)
    x=re.sub(r"\n[ \t]+","\n",x)
    return x.strip()



def _text(response: Any) -> str:
    value = getattr(response, 'text', None)
    return value.strip() if isinstance(value, str) else ''


def _json_text(text: str) -> str:
    text = text.strip().lstrip('\ufeff')
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*```$', '', text)
    a, b = text.find('{'), text.rfind('}')
    return text[a:b + 1] if a >= 0 and b > a else text


def _call(prompt: str, json_mode: bool = False, attempts: int = 3) -> str:
    if not client:
        raise RuntimeError('GEMINI_API_KEY belum ditemukan. Periksa file .env.')

    last_error = 'AI tidak mengembalikan jawaban.'

    for attempt in range(attempts):
        try:
            # Gemini 3.5 Flash-Lite/3.6 no longer needs the old temperature setting.
            # Keeping the config minimal avoids API errors caused by deprecated sampling fields.
            config_kwargs = {
                'system_instruction': BASE_INSTRUCTION,
                'max_output_tokens': 12000,
            }
            if json_mode:
                config_kwargs['response_mime_type'] = 'application/json'

            config = types.GenerateContentConfig(**config_kwargs)
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config,
            )
            output = _text(response)
            if output:
                return output
            last_error = 'AI mengembalikan jawaban kosong.'
        except Exception as exc:
            last_error = str(exc)
            # Retry with a small delay. This also handles temporary 429/5xx/network errors.
            if attempt < attempts - 1:
                time.sleep(1.0 + attempt)

    raise RuntimeError(last_error)


def _json_call(prompt: str):
    raw = _call(prompt, json_mode=True)
    try:
        return json.loads(_json_text(raw))
    except Exception as first_error:
        repair_prompt = f'''Perbaiki JSON berikut menjadi JSON VALID.
Jangan mengubah isi, angka, rumus, atau fakta. Jangan menambahkan penjelasan di luar JSON.

JSON RUSAK:
{raw}'''
        try:
            fixed = _call(repair_prompt, json_mode=True, attempts=2)
            return json.loads(_json_text(fixed))
        except Exception as second_error:
            raise RuntimeError(f'Format respons AI tidak valid: {first_error}; percobaan perbaikan: {second_error}')


def _clean(material: str) -> str:
    material = material.strip()
    if not material:
        raise ValueError('Materi kosong.')
    return material[:120000]


def _sentences(material: str):
    return [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+', material) if x.strip()]


def fallback_explain(material: str):
    """Fallback yang jujur. Tidak berpura-pura sebagai penjelasan AI jika API gagal."""
    sentences = _sentences(material)[:10] or [material]
    joined = '\n'.join(f'{i + 1}. {x}' for i, x in enumerate(sentences))
    return {
        'hasil': (
            '## Penjelasan sementara\n\n'
            'AI belum berhasil terhubung, jadi saya tidak akan mengarang penjelasan. '
            'Berikut bagian materi yang berhasil dibaca oleh aplikasi:\n\n'
            + joined
            + '\n\n## Penting\n\n'
            'Coba tekan "Buat penjelasan" lagi setelah koneksi AI/API diperbaiki.'
        ),
        'konsep': [
            {'judul': f'Bagian {i + 1}', 'isi': x, 'contoh': ''}
            for i, x in enumerate(sentences[:8])
        ],
        'ai_error': True,
    }


def jelaskan_materi(material: str):
    material = _clean(material)
    prompt = f'''
Jelaskan materi berikut untuk siswa yang ingin benar-benar paham sebelum ulangan.

WAJIB:
- Mulai dengan Gambaran Besar yang sangat mudah dipahami.
- Jelaskan istilah penting dengan kata sehari-hari.
- Pecah menjadi 5 sampai 8 konsep inti.
- Jelaskan hubungan antar konsep.
- Untuk matematika, jelaskan angka dan simbol dengan bahasa biasa. Jangan gunakan LaTeX atau kode. Gunakan kata kali, dibagi, pangkat, akar, dan persen.
- Untuk setiap metode atau hitungan, tulis Langkah 1, Langkah 2, dan seterusnya, serta jelaskan alasan setiap langkah.
- Jika materi memuat contoh soal, bedah contoh tersebut langkah demi langkah.
- Tambahkan Kesalahan yang Sering Terjadi jika memang berguna.
- Akhiri dengan Ringkasan Hafalan.
- Jangan menyalin paragraf materi mentah.
- Jangan membuat fakta baru di luar materi.

Kembalikan JSON persis dengan bentuk:
{{
  "hasil": "penjelasan markdown lengkap",
  "konsep": [
    {{"judul": "...", "isi": "...", "contoh": "..."}}
  ]
}}

MATERI:
{material}
'''
    try:
        data = _json_call(prompt)
        concepts = data.get('konsep') if isinstance(data, dict) else None
        if not isinstance(data, dict) or not str(data.get('hasil', '')).strip() or not isinstance(concepts, list):
            raise ValueError('Respons penjelasan tidak lengkap.')
        clean_concepts = [
            x for x in concepts[:8]
            if isinstance(x, dict) and str(x.get('judul', '')).strip() and str(x.get('isi', '')).strip()
        ]
        if not clean_concepts:
            raise ValueError('Konsep kosong.')
        return {'hasil': _plain(str(data['hasil'])), 'konsep': [{**x, 'judul': _plain(str(x.get('judul',''))), 'isi': _plain(str(x.get('isi',''))), 'contoh': _plain(str(x.get('contoh','')))} for x in clean_concepts]}
    except Exception as exc:
        fallback = fallback_explain(material)
        fallback['error_detail'] = str(exc)[:500]
        return fallback



def buat_konsep(material: str):
    """Generate a dedicated concept map for the Konsep tab."""
    material = _clean(material)
    prompt = f"""
Buat peta konsep belajar dari materi berikut.

ATURAN:
- Semua konsep HARUS bersumber dari materi.
- Pilih 5 sampai 8 konsep yang paling penting untuk dipahami sebelum mengerjakan soal.
- Untuk matematika, prioritaskan definisi, rumus, arti simbol, hubungan rumus, langkah penyelesaian, dan contoh yang memang ada di materi.
- Jangan membuat rumus, angka, fakta, atau istilah yang tidak didukung materi.
- Setiap konsep harus berdiri sendiri dan mudah dipelajari.
- Jika ada hubungan sebab-akibat/proses, jelaskan hubungan tersebut.
- Jika contoh ada di materi, gunakan contoh itu; jangan mengarang contoh baru.

JSON WAJIB:
{{
  "konsep": [
    {{"judul":"...","isi":"...","contoh":"...","kunci":"..."}}
  ]
}}

MATERI:
{material}
"""
    try:
        data = _json_call(prompt)
        raw = data.get('konsep') if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise ValueError('AI tidak mengembalikan daftar konsep.')
        out=[]
        for x in raw[:8]:
            if not isinstance(x, dict):
                continue
            title=str(x.get('judul','')).strip()
            body=str(x.get('isi','')).strip()
            if title and body:
                out.append({
                    'judul': _plain(title),
                    'isi': _plain(body),
                    'contoh': _plain(str(x.get('contoh','')).strip()),
                    'kunci': _plain(str(x.get('kunci','')).strip()),
                })
        if len(out) < 3:
            raise ValueError('Konsep yang valid terlalu sedikit.')
        return out
    except Exception as exc:
        parts = _sentences(material)
        out=[]
        for i, part in enumerate(parts[:8]):
            out.append({'judul': f'Konsep {i+1}', 'isi': part, 'contoh': '', 'kunci': part[:140]})
        if len(out) < 3:
            raise RuntimeError(f'Konsep gagal dibuat: {exc}')
        return out

def tanya_materi(material: str, question: str):
    material = _clean(material)
    question = question.strip()
    if not question:
        raise ValueError('Pertanyaan kosong.')

    return _plain(_call(f'''
Jawab pertanyaan pelajar berdasarkan materi berikut.

Untuk pertanyaan matematika:
- tulis rumus yang dipakai,
- masukkan angka/simbol ke rumus,
- hitung langkah demi langkah,
- beri hasil akhir,
- jelaskan kenapa langkah tersebut benar.

Untuk pertanyaan konsep:
- beri jawaban langsung,
- lalu alasan dan hubungan dengan konsep dalam materi.

Jika jawaban tidak dapat dibuktikan dari materi, katakan dengan jelas bahwa materi belum cukup.

MATERI:
{material}

PERTANYAAN:
{question}
'''))


def _normalize_words(text: str):
    return re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text.lower())


def _too_similar_to_material(question: str, material: str) -> bool:
    from difflib import SequenceMatcher
    q=' '.join(_normalize_words(question))
    if len(q.split()) < 7: return False
    for sentence in _sentences(material):
        a=' '.join(_normalize_words(sentence))
        if len(a.split()) < 7: continue
        if SequenceMatcher(None,q,a).ratio() >= 0.82: return True
    return False


def norm_questions(items, count=5, material=''):
    out=[]
    for item in items if isinstance(items,list) else []:
        if not isinstance(item,dict): continue
        options=item.get('pilihan'); answer=str(item.get('jawaban','')).upper().strip()[:1]
        question=str(item.get('pertanyaan','')).strip(); explanation=str(item.get('penjelasan','')).strip(); evidence=str(item.get('bukti','')).strip()
        if len(options or [])!=4 or answer not in 'ABCD': continue
        if not question or any(not str(x).strip() for x in options): continue
        if len({str(x).strip().lower() for x in options})!=4: continue
        if not explanation or not evidence: continue
        if material and _too_similar_to_material(question,material): continue
        out.append({'pertanyaan':_plain(question),'pilihan':[_plain(str(x).strip()) for x in options],'jawaban':answer,'penjelasan':_plain(explanation),'bukti':_plain(evidence),'konsep':_plain(str(item.get('konsep','')).strip()),'tingkat':_plain(str(item.get('tingkat','sedang')).strip().lower())})
        if len(out)>=count: break
    return out


def verify(material, questions, target_count=None):
    if not questions: return []
    target_count=target_count or len(questions)
    try:
        data=_json_call(f"""
Kamu adalah pemeriksa soal ujian yang sangat ketat.
Tujuan: soal menguji PEMAHAMAN materi, bukan menyalin kalimat materi.

ATURAN WAJIB:
1. Jawaban harus dapat dibuktikan hanya dari materi.
2. Jangan menjadikan kalimat pertama/paragraf pertama sebagai soal dengan mengganti sedikit kata.
3. Utamakan mengapa, bagaimana, membedakan konsep, menerapkan aturan, memilih langkah, atau menyimpulkan akibat.
4. Istilah materi boleh dipakai, tetapi struktur kalimat materi jangan disalin hampir persis.
5. Tepat satu jawaban benar dan pengecoh harus masuk akal.
6. Untuk matematika/fisika/kimia, angka baru boleh dipakai hanya jika rumus/metode yang diperlukan ada di materi; cek aritmetika.
7. Penjelasan harus menerangkan cara berpikir dan alasan.
8. Bukti harus menunjuk konsep/bagian materi yang mendukung.
9. Hapus soal ambigu, copy-paste, atau membutuhkan pengetahuan di luar materi.
10. Sebarkan soal ke konsep berbeda.

JSON:
{{"soal":[{{"pertanyaan":"...","pilihan":["A. ...","B. ...","C. ...","D. ..."],"jawaban":"A","penjelasan":"...","bukti":"...","konsep":"...","tingkat":"mudah|sedang|sulit"}}]}}

MATERI:
{material}

SOAL:
{json.dumps(questions,ensure_ascii=False)}
""")
        return norm_questions(data.get('soal'),target_count,material)
    except Exception:
        return [q for q in questions if not _too_similar_to_material(q.get('pertanyaan',''),material)][:target_count]


def _fallback_questions(material,count=10):
    sentences=_sentences(material)
    if not sentences: return []
    indexes=[]; n=min(count,len(sentences))
    for i in range(n):
        idx=round(i*(len(sentences)-1)/max(1,n-1))
        if idx not in indexes: indexes.append(idx)
    out=[]
    for idx in indexes:
        src=sentences[idx]
        out.append({'pertanyaan':'Bagian materi manakah yang paling tepat menjelaskan gagasan yang sedang diuji?','pilihan':[f'A. {src[:150]}','B. Gagasan tersebut tidak dibahas dalam materi.','C. Gagasan tersebut bertentangan dengan materi.','D. Gagasan tersebut hanya berlaku untuk topik lain.'],'jawaban':'A','penjelasan':'Pilihan A paling sesuai dengan bagian materi yang menjadi dasar soal.','bukti':src,'konsep':'Bagian materi terkait','tingkat':'mudah'})
    return out[:count]


def buat_paket_belajar(material):
    material=_clean(material)
    try:
        data=_json_call(f"""
Buat 10 soal ulangan berdasarkan materi berikut.
INI BUKAN TUGAS MERINGKAS MATERI. Soal harus membuat siswa menggunakan pemahaman.

ATURAN:
- Jangan menyalin kalimat materi atau menjadikan paragraf pertama sebagai soal.
- Sebar soal ke konsep berbeda.
- Minimal 6 soal berbentuk pemahaman/aplikasi/analisis ringan, bukan sekadar definisi.
- Untuk proses: tanyakan urutan, alasan, atau akibat.
- Untuk perbandingan: minta siswa membedakan kondisi/hasil.
- Untuk matematika/fisika/kimia: boleh membuat angka baru jika metode/rumusnya ada di materi dan perhitungannya benar.
- Tepat satu jawaban benar; pengecoh masuk akal.
- Penjelasan mengajarkan cara berpikir dengan bahasa yang mudah.
- Jangan gunakan LaTeX, backtick, kode, atau simbol aneh. Untuk pecahan gunakan kata dibagi. Untuk perkalian gunakan kata kali.
- Bukti menyebut konsep/bagian materi yang menjadi dasar.
- Campurkan tingkat mudah, sedang, sulit.

JSON:
{{"ringkasan":"...","soal":[{{"pertanyaan":"...","pilihan":["A. ...","B. ...","C. ...","D. ..."],"jawaban":"A","penjelasan":"...","bukti":"...","konsep":"...","tingkat":"mudah|sedang|sulit"}}]}}

MATERI:
{material}
""")
        raw=norm_questions(data.get('soal'),10,material); questions=verify(material,raw,10)
        if len(questions)<6: raise ValueError(f'Hanya {len(questions)} soal yang lolos pemeriksaan.')
        return {'ringkasan':str(data.get('ringkasan','Ulangan untuk menguji pemahaman materi.')),'soal':questions}
    except Exception as exc:
        questions=_fallback_questions(material,10)
        if len(questions)<3: raise RuntimeError(f'AI gagal membuat ulangan: {exc}')
        return {'ringkasan':'Ulangan sementara dibuat dari beberapa bagian materi yang berbeda.','soal':questions}


def _clean_game_data(data,material):
    if not isinstance(data,dict): return {}
    out={}
    mem=[]
    for x in data.get('memory',[]) if isinstance(data.get('memory'),list) else []:
        if isinstance(x,dict) and str(x.get('term','')).strip() and str(x.get('definition','')).strip():
            mem.append({'term':_plain(str(x['term']).strip())[:90],'definition':_plain(str(x['definition']).strip())[:180]})
    out['memory']=mem[:8]
    cards=[]
    for x in data.get('flashcards',[]) if isinstance(data.get('flashcards'),list) else []:
        if isinstance(x,dict) and str(x.get('front','')).strip() and str(x.get('back','')).strip():
            cards.append({'front':_plain(str(x['front']).strip())[:150],'back':_plain(str(x['back']).strip())[:260]})
    out['flashcards']=cards[:12]
    puzzles=[]
    for x in data.get('puzzle',[]) if isinstance(data.get('puzzle'),list) else []:
        if isinstance(x,dict):
            sent=_plain(str(x.get('sentence','')).strip())
            words=x.get('words') if isinstance(x.get('words'),list) else sent.split()
            words=[_plain(str(w).strip()) for w in words if str(w).strip()]
            if 4<=len(words)<=10 and sent: puzzles.append({'sentence':sent,'words':words})
    out['puzzle']=puzzles[:8]
    out['quick_quiz']=verify(material,norm_questions(data.get('quick_quiz'),8,material),8)
    tf=[]
    for x in data.get('true_false',[]) if isinstance(data.get('true_false'),list) else []:
        if isinstance(x,dict) and str(x.get('statement','')).strip() and isinstance(x.get('answer'),bool):
            tf.append({'statement':_plain(str(x['statement']).strip())[:220],'answer':x['answer'],'explanation':_plain(str(x.get('explanation','')).strip())[:300]})
    out['true_false']=tf[:8]
    out['survival']=verify(material,norm_questions(data.get('survival'),10,material),10)
    return out


def _fallback_games(material):
    concepts=fallback_explain(material)['konsep']
    memory=[{'term':_plain(c['judul']),'definition':_plain(c['isi'][:180])} for c in concepts[:8]]
    flash=[{'front':_plain(f'Apa inti dari {c["judul"]}?'),'back':_plain(c['isi'][:240])} for c in concepts[:8]]
    truefalse=[{'statement':_plain(c['isi'][:200]),'answer':True,'explanation':'Pernyataan ini sesuai dengan materi yang tersedia.'} for c in concepts[:8]]
    puzzles=[]
    for c in concepts[:6]:
        ws=_plain(c['isi']).split()
        if 4<=len(ws)<=10: puzzles.append({'sentence':_plain(c['isi']),'words':ws})
    q=_fallback_questions(material,8)
    return {'memory':memory,'flashcards':flash,'puzzle':puzzles,'quick_quiz':q,'true_false':truefalse,'survival':q[:8]}


def buat_data_game(material):
    material=_clean(material)
    try:
        data=_json_call(f"""
Buat paket game belajar berdasarkan materi berikut. Game harus membantu siswa memahami materi, bukan sekadar menghafal kalimat.

ATURAN:
- Jangan menyalin paragraf materi.
- Setiap pertanyaan harus berasal dari konsep yang benar-benar diajarkan.
- Gunakan bahasa Indonesia yang sederhana.
- Jangan gunakan LaTeX, kode, backtick, atau simbol aneh.
- Untuk matematika, gunakan kata kali, dibagi, pangkat, akar, dan persen.
- Angka baru hanya boleh digunakan jika metode yang diperlukan memang ada di materi dan hitungannya benar.

JSON WAJIB:
{{
"memory":[{{"term":"nama konsep atau istilah","definition":"penjelasan singkat dengan bahasa mudah"}}],
"flashcards":[{{"front":"pertanyaan pemahaman","back":"jawaban mudah dipahami"}}],
"puzzle":[{{"sentence":"kalimat pendek 4 sampai 10 kata","words":["kata1","kata2"]}}],
"quick_quiz":[{{"pertanyaan":"soal pemahaman atau penerapan","pilihan":["A. ...","B. ...","C. ...","D. ..."],"jawaban":"A","penjelasan":"alasan dengan bahasa mudah","bukti":"konsep materi yang menjadi dasar"}}],
"true_false":[{{"statement":"pernyataan yang dapat dinilai benar atau salah","answer":true,"explanation":"alasan sederhana"}}],
"survival":[{{"pertanyaan":"soal yang sedikit lebih menantang","pilihan":["A. ...","B. ...","C. ...","D. ..."],"jawaban":"A","penjelasan":"alasan sederhana","bukti":"konsep materi"}}]
}}

BATASAN:
memory 6 sampai 8 pasangan; flashcards 8 sampai 12; puzzle 5 sampai 8; quick_quiz 8; true_false 8 dengan campuran benar dan salah; survival 8 sampai 10.

MATERI:
{material}
""")
        cleaned=_clean_game_data(data,material)
        required={'memory':4,'flashcards':5,'puzzle':3,'quick_quiz':5,'true_false':5,'survival':5}
        if any(len(cleaned.get(k,[]))<n for k,n in required.items()): raise ValueError('Paket game belum memenuhi jumlah minimum.')
        return cleaned
    except Exception:
        return _fallback_games(material)

