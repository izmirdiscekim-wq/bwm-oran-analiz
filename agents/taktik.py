"""
KATMAN A3 - TAKTİK KAYDI + TARAMA: kullanıcının YouTube'dan derlediği "oran aralığı" taktiklerini kaydeder ve Nesine bülteninde
başlamamış maçlara uygular. Kurallar KOD DEĞİL, düzenlenebilir metin dosyasındadır: skills/bwm-taktik-tara/taktikler.txt
(kategori: "Oran Analizi"; dosyayı OKUMAYA gerek yok: --liste / --kurallar / --ekle / --degistir aynı işi kısa çıktıyla yapar).

  bwm.py taktik                          tüm aktif taktikleri bültende tara (TAM uyanlar + kural bazında geçen sayıları)
  bwm.py taktik --yakin | --ad "4,5"     tek kuralı kaçıranlar da | tek taktik   [--gun hepsi|bugun|yarin|YYYY-MM-DD|DD.MM] [--saat ..] [--lig ..] [--n 15]
  bwm.py taktik --liste | --kurallar | --kodlar
  bwm.py taktik --ekle "Ad" --hedef "..." --oyna u45 --basari "top>=5" --kural "u45 4,20-5,10" --kural "tg45 2,80-3,10" [--orijinal "video metni"] [--kategori K]
  bwm.py taktik --degistir "Ad" [aynı bayraklar; verilen alan değişir, --kural verilirse TÜM kurallar yenilenir]
  bwm.py taktik --kapat "Ad" | --ac "Ad" | --sil "Ad"
  bwm.py taktik --arsiv [--gun-geri 7]   arşivdeki geçmiş maçlarda isabeti ölç (önce sonuçları işler)
--ekle/--degistir/--ac sonrası o taktik hemen taranır (tek komut = kaydet + sonuç). Her yazma öncesi taktikler.txt.bak yedeği alınır.
Özel pazar kodu: dosyada `@kod ad = t<MTID>[/SOV]/<N>  # açıklama` satırı (kod değişikliği gerekmez).
Favoriye göre değişen kural (MS'de düşük oranlı taraf): `iygolfav <aralık>` = ev favoriyse İY1&1.5Ü, deplasman favoriyse İY2&1.5Ü; `msfav`/`msalt <aralık>` = favori/favori-olmayan takımın MS oranı (--kodlar).
Aralık VEYA: `4.45-4.55|4.70-4.80` (herhangi biri sağlanırsa kural geçer); `=X` ±0.05 tolerans alır (oranlar tam basılmayabilir).
Her taktik taraması otomatik 2 liste verir: KAPANIŞ (kickoff'a ≤20 dk kalan güncel oran) ve GÜNCEL (kapanış öncesi, oran değişebilir); ayrı bir bayrak/alan gerekmez, tüm taktikler için geçerlidir.
Spor: `spor: futbol|basketbol` (varsayılan futbol; --ekle/--degistir --spor basketbol). Basketbol pazar kodları "b" önekli (--kodlar; bms1, bhcp1, btsu ...),
basketbol.py bültenini tarar (nesine.py ile birebir aynı mimari). "Tüm maçları tara" isteğinde her iki spor da aktifse iki bülten de çekilir.
"""
import os, sys, re, json, time, argparse, shutil, io, contextlib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nesine as N
try:
    import basketbol as B
except Exception:
    B = None

SPOR = {"futbol": N, "basketbol": B}         # spor adı -> bülten modülü (nesine.py / basketbol.py; aynı arayüz)

SCRIPT_DIR = N.SCRIPT_DIR
DOSYA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "skills", "bwm-taktik-tara", "taktikler.txt"))
ARSIV = os.path.join(SCRIPT_DIR, "data", "arsiv")
KATEGORI = "Oran Analizi"
ANAHTAR = ("kategori", "spor", "eklendi", "hedef", "oyna", "basari", "not", "orijinal", "aktif")

# kod -> (MTID, SOV|None, çıktı N, açıklama). Ham biçim: t<MTID>[/<SOV>]/<N>   ör. t43/4 = Toplam Gol 6+
KODLAR = {
    "ms1": (1, None, 1, "MS 1"), "msx": (1, None, 2, "MS X"), "ms2": (1, None, 3, "MS 2"),
    "u25": (12, 2.5, 2, "2.5 Üst"), "a25": (12, 2.5, 1, "2.5 Alt"), "u35": (13, 3.5, 2, "3.5 Üst"), "a35": (13, 3.5, 1, "3.5 Alt"),
    "u45": (155, 4.5, 2, "4.5 Üst"), "a45": (155, 4.5, 1, "4.5 Alt"), "u55": (155, 5.5, 2, "5.5 Üst"),
    "kgvar": (38, None, 1, "KG Var"), "kgyok": (38, None, 2, "KG Yok"),
    "tg01": (43, None, 1, "Toplam Gol 0-1"), "tg23": (43, None, 2, "Toplam Gol 2-3"), "tg45": (43, None, 3, "Toplam Gol 4-5"), "tg6": (43, None, 4, "Toplam Gol 6+"),
    "iyu15": (14, 1.5, 2, "İY 1.5 Üst"), "iya15": (14, 1.5, 1, "İY 1.5 Alt"), "iyu05": (209, 0.5, 2, "İY 0.5 Üst"),
    "iy1u15": (459, 1.5, 4, "İY sonucu 1 ve İY 1.5 Üst"), "iy2u15": (459, 1.5, 6, "İY sonucu 2 ve İY 1.5 Üst"),
    "iykg": (452, None, 1, "İY Karşılıklı Gol Var"), "iykgyok": (452, None, 2, "İY KG Yok"),
    "ykg2": (599, None, 1, "2. Yarı KG Var"), "iy2ykg": (801, None, 3, "İY ve 2.Y KG ikisi de Var"),
    "ciftu15": (529, None, 1, "İki yarıda da 1.5 Üst (Evet)"), "ciftu15hayir": (529, None, 2, "İki yarıda da 1.5 Üst (Hayır)"),
    "ciftalt15": (528, None, 1, "İki yarıda da 1.5 Alt (Evet)"),
    "eviyu05": (455, 0.5, 2, "Ev sahibi İY 0.5 Üst"), "eviya05": (455, 0.5, 1, "Ev sahibi İY 0.5 Alt"),
    "depiyu05": (457, 0.5, 2, "Deplasman İY 0.5 Üst"), "depiya05": (457, 0.5, 1, "Deplasman İY 0.5 Alt"),
    "iyskor11": (779, None, 2, "İY skoru 1-1"), "iyskor00": (779, None, 1, "İY skoru 0-0"),
    "evyari1": (586, None, 1, "Ev sahibi en çok gol: 1. yarı"), "evyarix": (586, None, 2, "Ev sahibi en çok gol: eşit"), "evyari2": (586, None, 3, "Ev sahibi en çok gol: 2. yarı"),
    "depyari1": (587, None, 1, "Deplasman en çok gol: 1. yarı"), "depyari2": (587, None, 3, "Deplasman en çok gol: 2. yarı"),
}
OZEL = {}      # taktikler.txt içindeki @kod tanımları (dosya okunurken dolar)

# favoriye göre değişen kod: (ev favoriyse kullanılacak kod, deplasman favoriyse kullanılacak kod). Favori = MS'de düşük oranlı taraf.
FAV_KODLAR = {"iygolfav": ("iy1u15", "iy2u15"), "msfav": ("ms1", "ms2"), "msalt": ("ms2", "ms1")}
FAV_ACIKLAMA = {"iygolfav": "İY Gol Durumu: ev favori→İY sonucu 1 ve İY 1.5 Üst, deplasman favori→İY sonucu 2 ve İY 1.5 Üst",
                "msfav": "Favori takımın MS oranı (düşük olan taraf)", "msalt": "Favori olmayan takımın MS oranı (yüksek olan taraf)"}


# ---------------------------------------------------------------- kural + pazar kodu + aralık
def _ham_kod(tok):
    m = re.fullmatch(r"t(\d+)(?:/(-?[\d.]+))?/(\d+)", tok)
    return (int(m[1]), float(m[2]) if m[2] else None, int(m[3])) if m else None


def _kod_coz(kod):
    kod = kod.lower()
    if kod in KODLAR:
        return KODLAR[kod][:3]
    if B and kod in B.KODLAR:                 # basketbol kodları ("b" önekli: bms1, bhcp1, btsu ...), çakışma olmadığı için tek sözlükmüş gibi çalışır
        return B.KODLAR[kod][:3]
    if kod in OZEL:
        return OZEL[kod][:3]
    if (h := _ham_kod(kod)):
        return h
    raise ValueError(f"bilinmeyen pazar kodu '{kod}' (--kodlar; ham biçim t<MTID>[/SOV]/<N>; kalıcı ad için dosyaya '@kod ad = t..')")


def _kod_dogrula(kod):
    """_kod_coz gibi ama favoriye göre değişen kodları da (FAV_KODLAR) geçerli sayar (oyna: alanı için)."""
    if kod.lower() not in FAV_KODLAR:
        _kod_coz(kod)


def _aralik(s):
    s = s.replace(",", ".")
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s):
        if float(m[1]) > float(m[2]):
            raise ValueError(f"aralık ters '{s}'")
        return float(m[1]), float(m[2])
    if m := re.fullmatch(r">=?(\d+(?:\.\d+)?)", s):
        return float(m[1]), float("inf")
    if m := re.fullmatch(r"<=?(\d+(?:\.\d+)?)", s):
        return 0.0, float(m[1])
    if m := re.fullmatch(r"=(\d+(?:\.\d+)?)", s):
        return float(m[1]), float(m[1])
    raise ValueError(f"aralık anlaşılmadı '{s}' (biçim: 4.20-5.10 | >=4.2 | <=5.1 | =7.00)")


def _araliklar(s):
    """'4.45-4.55|4.70-4.80' -> [(lo,hi), ...] (VEYA; herhangi biri sağlanırsa kural geçer). '=X' etrafına ±0.05 tolerans (oranlar tam 'X.X0' basılmayabilir)."""
    out = []
    for parca in s.split("|"):
        lo, hi = _aralik(parca.strip())
        if lo == hi:                                       # '=X' -> yalın eşitlik yerine küçük tolerans
            lo, hi = lo - 0.05, hi + 0.05
        out.append((lo, hi))
    return out


def _num(x):
    s = f"{x:.3f}".rstrip("0")
    return s if len(s.split(".")[1]) >= 2 else f"{x:.2f}"


class Kural:
    def __init__(self, kod, araliklar, ilk=False, yorum=""):
        self.kod, self.ilk, self.yorum = kod.lower(), ilk, yorum
        self.araliklar = araliklar                          # [(lo,hi), ...] VEYA
        self.fav = self.kod in FAV_KODLAR
        if self.fav:
            self.fav_ev, self.fav_dep = FAV_KODLAR[self.kod]
            self.t = self.sov = self.n = None
        else:
            self.t, self.sov, self.n = _kod_coz(self.kod)

    def ad(self):
        return self.kod + ("@ilk" if self.ilk else "")

    def aralik_txt(self):
        # ayraç boşluksuz "|": _blok_txt bunu dosyaya yazar, kural_coz boşluklu "|" varsa satırı >2 parçaya böler ve reddeder
        return "|".join(f"{_num(lo)}-{_num(hi)}" if hi != float("inf") else f">={_num(lo)}" for lo, hi in self.araliklar)

    def gecer(self, v):
        return v is not None and any(lo - 1e-9 <= v <= hi + 1e-9 for lo, hi in self.araliklar)


def kural_coz(txt):
    """'u45 4,20-5,10 # yorum' | 'iyskor11@ilk 8.00-8.92' | 'altfav 4.45-4.55|4.70-4.80' (VEYA) -> Kural"""
    yorum = txt.split("#", 1)[1].strip() if "#" in txt else ""
    p = txt.split("#", 1)[0].split()
    if len(p) != 2:
        raise ValueError(f"kural biçimi '<kod> <aralık>': '{txt}'")
    ilk = p[0].lower().endswith("@ilk")
    return Kural(p[0][:-4] if ilk else p[0], _araliklar(p[1]), ilk, yorum)


# ---------------------------------------------------------------- kural dosyası (okuma / yazma)
def dosya_oku(yol=DOSYA):
    """-> (ust_satirlar, taktikler). @kod satırları önce işlenir (kural sırasından bağımsız). Hatalı satırlar uyarıyla atlanır."""
    if not os.path.exists(yol):
        sys.exit(f"kural dosyası yok: {yol}")
    with open(yol, encoding="utf-8") as f:
        satirlar = f.read().splitlines()
    OZEL.clear()
    for ham in satirlar:
        if m := re.match(r"@kod\s+(\w+)\s*=\s*(t\S+)\s*(?:#\s*(.*))?$", ham.strip()):
            if h := _ham_kod(m[2]):
                OZEL[m[1].lower()] = (*h, m[3] or "")
    ust, out, cur = [], [], None
    for i, ham in enumerate(satirlar, 1):
        satir = ham.split("#", 1)[0].strip()
        if not satir.startswith("[") and cur is None:
            ust.append(ham)                                  # başlık yorumları + @kod satırları
            continue
        if not satir:
            continue
        try:
            if satir.startswith("["):
                cur = {"ad": satir.strip("[] "), "kategori": KATEGORI, "spor": "futbol", "eklendi": "", "hedef": "", "oyna": [], "basari": None, "not": "",
                       "orijinal": "", "aktif": True, "kurallar": []}
                out.append(cur)
            elif m := re.match(r"(" + "|".join(ANAHTAR) + r")\s*:\s*(.*)$", satir):
                k, v = m[1], m[2].strip()
                if k in ("orijinal", "not"):
                    v = ham.split(":", 1)[1].strip()          # bu alanlarda '#' de içerik olabilir (yazarken temizlenir)
                if k == "oyna":
                    cur["oyna"] = [x for x in re.split(r"[ ,]+", v) if x]
                    for x in cur["oyna"]:
                        _kod_dogrula(x)
                elif k == "aktif":
                    cur["aktif"] = N._norm(v) not in ("hayir", "yok", "kapali", "false", "0", "pasif")
                elif k == "spor":
                    cur["spor"] = v.strip().lower() if v.strip().lower() in SPOR else "futbol"
                else:
                    cur[k] = v
            else:
                cur["kurallar"].append(kural_coz(ham.strip()))
        except ValueError as ex:
            print(f"UYARI kural dosyası satır {i}: {ex}")
    return ust, out


def _blok_txt(t):
    L = [f"[{t['ad']}]"]
    for k in ("kategori", "eklendi", "hedef"):
        if t.get(k):
            L.append(f"{k}: {t[k]}")
    if t.get("spor", "futbol") != "futbol":            # varsayılan futbol basılmaz (dosya kısa kalsın)
        L.append(f"spor: {t['spor']}")
    if t["oyna"]:
        L.append("oyna: " + " ".join(t["oyna"]))
    if t["basari"]:
        L.append(f"basari: {t['basari']}")
    if not t["aktif"]:
        L.append("aktif: hayir")
    for k in ("not", "orijinal"):
        if t.get(k):
            L.append(f"{k}: {t[k].replace('#', '').replace(chr(10), ' / ')}")
    for k in t["kurallar"]:
        L.append(f"{k.ad():<14} {k.aralik_txt():<11}" + (f" # {k.yorum}" if k.yorum else ""))
    return "\n".join(L)


def dosya_yaz(ust, taktikler, yol=DOSYA):
    if os.path.exists(yol):
        shutil.copy2(yol, yol + ".bak")
    while ust and not ust[-1].strip():
        ust.pop()
    with open(yol, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ust) + "\n\n" + "\n\n".join(_blok_txt(t) for t in taktikler) + "\n")


def _bul(taktikler, ad):
    """Tam ad eşleşmesi; yoksa tekil içerme. Belirsizse ValueError."""
    n = N._norm(ad)
    tam = [t for t in taktikler if N._norm(t["ad"]) == n]
    aday = tam or [t for t in taktikler if n and n in N._norm(t["ad"])]
    if len(aday) > 1:
        raise ValueError("belirsiz ad: " + " | ".join(t["ad"] for t in aday))
    return aday[0] if aday else None


def duzenle(a, yol=DOSYA):
    """--ekle / --degistir / --kapat / --ac / --sil. -> (mesaj, taranacak taktik adı | None)"""
    ust, tk = dosya_oku(yol)
    bugun = datetime.now(N.TR).strftime("%Y-%m-%d")
    if a.ekle:
        if any(N._norm(x["ad"]) == N._norm(a.ekle) for x in tk):
            raise ValueError(f"'{a.ekle}' zaten kayıtlı: değiştirmek için --degistir")
        if not a.kural:
            raise ValueError("--ekle en az bir --kural ister (ör. --kural \"u45 4,20-5,10\")")
        spor = (a.spor or "futbol").strip().lower()
        if spor not in SPOR:
            raise ValueError(f"bilinmeyen spor '{spor}' (futbol | basketbol)")
        t = {"ad": a.ekle, "kategori": a.kategori or KATEGORI, "spor": spor, "eklendi": bugun, "hedef": a.hedef or "", "oyna": (a.oyna or "").split(),
             "basari": a.basari, "not": a.not_ or "", "orijinal": a.orijinal or "", "aktif": True,
             "kurallar": [kural_coz(x) for x in a.kural]}
        for x in t["oyna"]:
            _kod_dogrula(x)
        tk.append(t)
        msg, hedef = f"KAYDEDİLDİ: {t['ad']} ({len(t['kurallar'])} kural, kategori {t['kategori']})", t["ad"]
    else:
        ad = a.degistir or a.kapat or a.ac or a.sil
        t = _bul(tk, ad)
        if not t:
            raise ValueError(f"'{ad}' bulunamadı (--liste)")
        if a.sil:
            tk.remove(t)
            msg, hedef = f"SİLİNDİ: {t['ad']} (yedek: taktikler.txt.bak)", None
        elif a.kapat or a.ac:
            t["aktif"] = bool(a.ac)
            msg, hedef = f"{'AÇILDI' if a.ac else 'KAPATILDI'}: {t['ad']}", (t["ad"] if a.ac else None)
        else:
            for k, v in (("hedef", a.hedef), ("basari", a.basari), ("not", a.not_), ("orijinal", a.orijinal), ("kategori", a.kategori)):
                if v is not None:
                    t[k] = v
            if a.spor is not None:
                spor = a.spor.strip().lower()
                if spor not in SPOR:
                    raise ValueError(f"bilinmeyen spor '{spor}' (futbol | basketbol)")
                t["spor"] = spor
            if a.oyna is not None:
                t["oyna"] = a.oyna.split()
                for x in t["oyna"]:
                    _kod_dogrula(x)
            if a.kural:
                t["kurallar"] = [kural_coz(x) for x in a.kural]
            msg, hedef = f"GÜNCELLENDİ: {t['ad']} ({len(t['kurallar'])} kural)", t["ad"]
    dosya_yaz(ust, tk, yol)
    return msg, hedef


# ---------------------------------------------------------------- değer okuma / değerlendirme
def _deger(mk, t, sov, n):
    for m in mk:
        if m["t"] == t and (sov is None or abs(m["sov"] - sov) < 0.01):
            v = m["o"].get(str(n))
            if v:
                return v
    return None


_ILK = {}
KAYNAK = {"futbol": "nesine", "basketbol": "basketbol"}      # spor -> arşiv dosya öneki (data/arsiv/<kaynak>_YYYYMMDD.json)


def _ilk_mk(ev, kaynak="nesine"):
    """Arşivdeki ilk kayıt (gerçek açılış DEĞİL: arşiv 21.09.2026'da başladı)."""
    ymd = datetime.fromtimestamp(ev["esd"], N.TR).strftime("%Y%m%d")
    anahtar = f"{kaynak}:{ymd}"
    if anahtar not in _ILK:
        yol = os.path.join(ARSIV, f"{kaynak}_{ymd}.json")
        _ILK[anahtar] = json.load(open(yol, encoding="utf-8"))["maclar"] if os.path.exists(yol) else {}
    r = _ILK[anahtar].get(f"{ev['esd_ms']}_{ev['hn']}_{ev['an']}")
    return r["ilk"] if r else None


def _favori(mk):
    """MS'de düşük oranlı taraf: 'ev' | 'dep' | None (pazar yok ya da oranlar eşit -> favori belirsiz)."""
    o1, o2 = _deger(mk, 1, None, 1), _deger(mk, 1, None, 3)
    if o1 is None or o2 is None or o1 == o2:
        return None
    return "ev" if o1 < o2 else "dep"


def _mk_icin(ev, k, kaynak="nesine"):
    return (_ilk_mk(ev, kaynak) or ev["mk"]) if k.ilk else ev["mk"]


def _kural_hedef(k, mk):
    """Kuralın (t, sov, n) hedefi; favoriye göre değişen kuralda favori yoksa None döner."""
    if not k.fav:
        return k.t, k.sov, k.n
    fav = _favori(mk)
    if fav is None:
        return None
    return _kod_coz(k.fav_ev if fav == "ev" else k.fav_dep)


def _kural_etiket(k, mk):
    """Çıktıda gösterilecek etiket; favoriye göre değişen kuralda hangi alt-koda gidildiğini de yazar."""
    if not k.fav:
        return k.ad()
    fav = _favori(mk)
    alt = (k.fav_ev if fav == "ev" else k.fav_dep) if fav else "favori belirsiz"
    return f"{k.kod}→{alt}" + ("@ilk" if k.ilk else "")


def degerlendir(ev, kurallar, kaynak="nesine"):
    """-> [(değer|None, geçti_mi)] her kural için."""
    sonuc = []
    for k in kurallar:
        mk = _mk_icin(ev, k, kaynak)
        hedef = _kural_hedef(k, mk)
        v = _deger(mk, *hedef) if hedef else None
        sonuc.append((v, k.gecer(v)))
    return sonuc


def basari(ifade, sonuc):
    """Sonuç ifadesi: top ev dep iyev iydep iytop kg iykg ykg2 (kg/iykg/ykg2 = iki takım da attı). Sonuç eksikse None."""
    if not ifade or not sonuc or not sonuc.get("ms") or None in sonuc["ms"]:
        return None
    ev, dep = sonuc["ms"]
    iy = sonuc.get("iy") if sonuc.get("iy") and None not in sonuc["iy"] else None
    d = {"ev": ev, "dep": dep, "top": ev + dep, "kg": ev > 0 and dep > 0}
    if iy:
        d.update(iyev=iy[0], iydep=iy[1], iytop=sum(iy), iykg=iy[0] > 0 and iy[1] > 0, ykg2=(ev - iy[0]) > 0 and (dep - iy[1]) > 0)
    try:
        return bool(eval(ifade, {"__builtins__": {}}, d))
    except Exception:
        return None


def _fmt(v):
    return "-" if v is None else f"{v:g}"


# ---------------------------------------------------------------- tarama (token dostu: taktik başına 1 satır + uyan maçlar)
def _oyna_deger(ev, o):
    if o.lower() in FAV_KODLAR:
        fav = _favori(ev["mk"])
        if fav is None:
            return "favori belirsiz (MS eşit/yok)"
        kod = FAV_KODLAR[o.lower()][0 if fav == "ev" else 1]
        v = _deger(ev["mk"], *_kod_coz(kod))
        return f"({kod}) " + (_fmt(v) if v else "pazar Nesine'de AÇIK DEĞİL")
    v = _deger(ev["mk"], *_kod_coz(o))
    return _fmt(v) if v else "pazar Nesine'de AÇIK DEĞİL"


def _satir(ev, kurallar, sonuclar, oyna, etiket="", spor="futbol"):
    d = datetime.fromtimestamp(ev["esd"], N.TR)
    if spor == "basketbol" and B:
        ms = B._mk(ev, 142)
        ms_txt = f"MS {_fmt(ms.get('1'))}/{_fmt(ms.get('2'))}"        # basketbolda beraberlik yok: 2 yönlü
    else:
        ms = N._mk(ev, 1)
        ms_txt = f"MS {_fmt(ms.get('1'))}/{_fmt(ms.get('2'))}/{_fmt(ms.get('3'))}"
    kaynak = KAYNAK.get(spor, "nesine")
    par = " ".join(f"{_kural_etiket(k, _mk_icin(ev, k, kaynak))} {_fmt(v)}{'' if ok else '✗'}" for k, (v, ok) in zip(kurallar, sonuclar))
    oy = "".join(f" | OYNA {o} " + _oyna_deger(ev, o) for o in oyna)
    return f"  {d:%d.%m %H:%M} {ev['lig'][:22]} | {ev['hn'][:18]} - {ev['an'][:18]} | {ms_txt} | {par}{oy}{etiket}"


# Araştırma (22.09.2026, 211 arşiv kaydı): son çekim kickoff'tan medyan 103 dk önce (yalnız %4'ü ≤20 dk); MS1 oranı ilk-son arası
# medyan 0.05, %51'i >0.05 kaymış (maks 4.75). Kapanış (kickoff'a yakın) ile güncel oran gerçekten farklı olabiliyor; TÜM taktikler
# her taramada iki etiketle raporlanır (pencereyle dışlama YAPILMAZ, çünkü ≤20 dk'ya nadiren denk gelinir — dışlarsak çoğu tarama boş kalır).
KAPANIS_ESIK_DK = 20   # hedef "5-10 dk" kullanıcı ifadesinden geniş tutuldu (bkz. yukarı: nadiren yakalanıyor)


def _kapanis_mi(esd, referans_ts):
    """kickoff (esd) referans_ts'e göre kapanışa yakın mı: canlıda referans=şimdi, arşivde referans=son_cekim."""
    dk = (esd - referans_ts) / 60
    return 0 <= dk <= KAPANIS_ESIK_DK


def _tara_canli(a, taktikler):
    sporlar = sorted({t.get("spor", "futbol") for t in taktikler}, key=lambda s: s != "futbol")   # futbol önce (varsayılan görünüm sabit kalsın)
    kaynak, basliklar, cekim_ts = {}, [], None
    for sp in sporlar:
        mod = SPOR.get(sp) or N
        veri, taze = mod.bulten(a.yenile)
        if taze and not a.kayitsiz:
            mod._arsive_yaz(veri)
        cekim_ts = veri["cekim"]
        tum = [e for e in veri["olaylar"] if e["esd"] > time.time()]
        sec = [e for e in tum if mod._gun_ok(e["esd"], a.gun)]
        if a.saat:
            sec = [e for e in sec if mod._saat_ok(e["esd"], a.saat)]
        if a.lig:
            sec = [e for e in sec if mod._norm(a.lig) in mod._norm(e["lig"])]
        kaynak[sp] = sec
        basliklar.append(f"taranan başlamamış {sp} {len(sec)}/{len(tum)} (gün: {a.gun})")
    print(f"Nesine {datetime.fromtimestamp(cekim_ts, N.TR):%d.%m %H:%M} | " + " | ".join(basliklar) + f" | {len(taktikler)} taktik "
          f"| her taktik 2 liste halinde: KAPANIŞ (kickoff'a ≤{KAPANIS_ESIK_DK} dk, hedef 5-10 dk, nadir) + GÜNCEL (kapanış öncesi, oran değişebilir)")
    ilk_var = False
    now = time.time()
    for t in taktikler:
        ks = t["kurallar"]
        if not ks:
            continue
        sp = t.get("spor", "futbol")
        sec = kaynak.get(sp, [])
        ilk_var |= any(k.ilk for k in ks)
        ts = [(e, degerlendir(e, ks, KAYNAK.get(sp, "nesine"))) for e in sec]
        tam = [(e, s) for e, s in ts if all(ok for _, ok in s)]
        yakin = [(e, s) for e, s in ts if len(ks) > 1 and sum(ok for _, ok in s) == len(ks) - 1]
        kap = [(e, s) for e, s in tam if _kapanis_mi(e["esd"], now)]
        gun = [(e, s) for e, s in tam if not _kapanis_mi(e["esd"], now)]
        gec = " · ".join(f"{k.ad()} {sum(1 for _, s in ts if s[i][1])}/{sum(1 for _, s in ts if s[i][0] is not None)}" for i, k in enumerate(ks))
        print(f"■ {t['ad']}{' [' + sp + ']' if sp != 'futbol' else ''} → {t['hedef'] or '?'} | TAM {len(tam)} (KAPANIŞ {len(kap)} + GÜNCEL {len(gun)})"
              + (f" · yakın {len(yakin)}" if len(ks) > 1 else "") + f" | geçen/açık: {gec}")
        if kap:
            print(f"  ⏱ KAPANIŞ oranıyla uyuyor ({len(kap)}):")
            for e, s in kap[:a.n]:
                print(_satir(e, ks, s, t["oyna"], spor=sp))
        if gun:
            print(f"  GÜNCEL oranla (kapanış değil) uyuyor ({len(gun)}):")
            for e, s in gun[:a.n]:
                print(_satir(e, ks, s, t["oyna"], spor=sp))
        if a.yakin and yakin:
            print(f"  yakın (tek kural kaçırdı, {len(yakin)}):")
            for e, s in yakin[:a.n]:
                print(_satir(e, ks, s, [], spor=sp))
        if tam and t["not"]:
            print(f"  not: {t['not']}")
    if ilk_var:
        print("@ilk = arşivdeki ilk kayıtlı oran (gerçek açılış değil; arşiv 21.09.2026'da başladı)")


def _sonuc_isle(gun):
    try:
        import bulten_arsiv as ba
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            ba.cmd_sonuc(argparse.Namespace(gun=gun))
        satir = [x for x in tampon.getvalue().splitlines() if x.strip()][-1:]
        print("sonuç işleme: " + (satir[0] if satir else "-"))
    except Exception as ex:
        print(f"UYARI sonuçlar işlenemedi ({ex}); arşivdeki mevcut sonuçlarla devam")


def _tara_arsiv(a, taktikler):
    sporlar = sorted({t.get("spor", "futbol") for t in taktikler}, key=lambda s: s != "futbol")
    if "futbol" in sporlar:
        _sonuc_isle(a.gun_geri)          # basketbol sonuç işleme henüz yok (yalnız futbolda sonuc.py entegrasyonu var)
    bugun = datetime.now(N.TR).date()
    kayit_sp = {}
    for sp in sporlar:
        kaynak = KAYNAK.get(sp, "nesine")
        onek = kaynak + "_"
        kayit = []
        for f in sorted(os.listdir(ARSIV)):
            if not (f.startswith(onek) and f.endswith(".json")):
                continue
            gunluk = f[len(onek):len(onek) + 8]
            if not re.fullmatch(r"\d{8}", gunluk):
                continue
            if not (-a.gun_geri <= (datetime.strptime(gunluk, "%Y%m%d").date() - bugun).days <= 0):
                continue
            for r in json.load(open(os.path.join(ARSIV, f), encoding="utf-8"))["maclar"].values():
                if r.get("kapanis_oncesi", True):
                    esd = datetime.fromisoformat(r["kickoff"]).timestamp()
                    kayit.append({"esd": esd, "esd_ms": int(esd * 1000), "hn": r["home"], "an": r["away"], "lig": r.get("league") or "?",
                                  "mk": r["son"], "sonuc": r.get("sonuc"), "ilk": r.get("ilk"),
                                  "son_cekim": datetime.fromisoformat(r["son_cekim"]).timestamp() if r.get("son_cekim") else esd})
        kayit_sp[sp] = kayit
        sonuclu = sum(1 for r in kayit if r["sonuc"] and r["sonuc"].get("ms"))
        print(f"{kaynak} arşivi son {a.gun_geri} gün: {len(kayit)} maç, sonuçlu {sonuclu} (oran = arşivdeki son kayıt; kapanış olduğu garanti değil)")
    for t in taktikler:
        ks = t["kurallar"]
        if not ks:
            continue
        sp = t.get("spor", "futbol")
        kayit = kayit_sp.get(sp, [])
        secili = []
        for r in kayit:
            s = []
            for k in ks:
                mk = r["ilk"] if (k.ilk and r["ilk"]) else r["mk"]
                hedef = _kural_hedef(k, mk)
                v = _deger(mk, *hedef) if hedef else None
                s.append((v, k.gecer(v)))
            if all(ok for _, ok in s):
                secili.append((r, s, basari(t["basari"], r["sonuc"])))
        bil = [x for x in secili if x[2] is not None]
        tutan = sum(1 for x in bil if x[2])
        kap = [x for x in secili if _kapanis_mi(x[0]["esd"], x[0]["son_cekim"])]
        gun = [x for x in secili if not _kapanis_mi(x[0]["esd"], x[0]["son_cekim"])]
        print(f"■ {t['ad']}{' [' + sp + ']' if sp != 'futbol' else ''} | kuralları sağlayan {len(secili)} (KAPANIŞ {len(kap)} + GÜNCEL {len(gun)}) | sonuçlu {len(bil)}, tuttu {tutan}"
              + (f" (%{100 * tutan / len(bil):.0f}; n<30: kanıt değil)" if bil and len(bil) < 30 else ""))

        def _yaz(grup, baslik):
            if not grup:
                return
            print(f"  {baslik} ({len(grup)}):")
            for r, s, b in grup[:a.n]:
                etiket = f" | {r['sonuc']['ms'][0]}-{r['sonuc']['ms'][1]} {'TUTTU' if b else 'tutmadı'}" if b is not None else " | sonuç yok"
                print(_satir(r, ks, s, [], etiket, spor=sp))
        _yaz(kap, "⏱ KAPANIŞA yakın kaydedilmiş (son kayıt kickoff'a ≤20 dk kala)")
        _yaz(gun, "GÜNCEL kaydedilmiş (kapanış değil)")
    print(f"KAPANIŞ = son kayıt kickoff'a ≤{KAPANIS_ESIK_DK} dk kalaydı (gerçek kapanışa yakın; arşivde nadir, son çekimlerin ~%4'ü) | GÜNCEL = son kayıt daha erken alınmış")


def _liste(taktikler):
    print(f"{DOSYA}\n{len(taktikler)} taktik" + (f", {len(OZEL)} özel kod" if OZEL else ""))
    for kat in dict.fromkeys(t["kategori"] for t in taktikler):
        print(f"[{kat}]")
        for t in (x for x in taktikler if x["kategori"] == kat):
            sp = f" [{t['spor']}]" if t.get("spor", "futbol") != "futbol" else ""
            print(f"  {'✓' if t['aktif'] else '✗'} {t['ad']}{sp} → {t['hedef'] or '?'} | {len(t['kurallar'])} kural | eklendi {t['eklendi'] or '?'}")


def _kurallar(taktikler):
    for t in taktikler:
        sp = f" [{t['spor']}]" if t.get("spor", "futbol") != "futbol" else ""
        print(f"■ {t['ad']}{sp} [{t['kategori']}] {'' if t['aktif'] else '(KAPALI) '}| hedef: {t['hedef']} | oyna: {' '.join(t['oyna'])} | basari: {t['basari']}")
        for k in t["kurallar"]:
            if k.fav:
                print(f"   {k.ad():14} {k.aralik_txt():11} favori: ev→{k.fav_ev}, dep→{k.fav_dep}  {k.yorum}")
            else:
                print(f"   {k.ad():14} {k.aralik_txt():11} t{k.t}/{'' if k.sov is None else k.sov}/{k.n}  {k.yorum}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bwm.py taktik")
    p.add_argument("--ad", help="yalnız adında bu geçen taktikler")
    p.add_argument("--gun", default="hepsi")
    p.add_argument("--saat")
    p.add_argument("--lig")
    p.add_argument("--n", type=int, default=15, help="taktik başına en fazla satır")
    p.add_argument("--yakin", action="store_true")
    p.add_argument("--arsiv", action="store_true")
    p.add_argument("--gun-geri", type=int, default=7)
    p.add_argument("--liste", action="store_true")
    p.add_argument("--kurallar", action="store_true")
    p.add_argument("--kodlar", action="store_true")
    p.add_argument("--yenile", action="store_true")
    p.add_argument("--kayitsiz", action="store_true")
    for k in ("ekle", "degistir", "kapat", "ac", "sil"):
        p.add_argument(f"--{k}", metavar="AD")
    p.add_argument("--hedef")
    p.add_argument("--oyna")
    p.add_argument("--basari")
    p.add_argument("--not", dest="not_")
    p.add_argument("--orijinal")
    p.add_argument("--kategori")
    p.add_argument("--spor", help="futbol (varsayılan) | basketbol")
    p.add_argument("--kural", action="append")
    a = p.parse_args(argv)
    if a.kodlar:
        dosya_oku()
        print("kod: MTID/SOV/N açıklama | ham: t<MTID>[/SOV]/<N> | aralık: 4.20-5.10 (virgül olur) >=4.2 <=5.1 =7.00 | kod@ilk = arşivdeki ilk oran")
        print("FUTBOL: " + "; ".join(f"{k}: {v[0]}/{'' if v[1] is None else v[1]}/{v[2]} {v[3]}" for k, v in {**KODLAR, **OZEL}.items()))
        if B:
            print("BASKETBOL (spor: basketbol; 'b' önekli): " + "; ".join(f"{k}: {v[0]}/{'' if v[1] is None else v[1]}/{v[2]} {v[3]}" for k, v in B.KODLAR.items()))
        print("favoriye göre değişen: " + "; ".join(f"{k} (ev→{va}, dep→{vb}): {FAV_ACIKLAMA.get(k, '')}" for k, (va, vb) in FAV_KODLAR.items()))
        return
    if any((a.ekle, a.degistir, a.kapat, a.ac, a.sil)):
        try:
            msg, hedef = duzenle(a)
        except ValueError as ex:
            sys.exit(f"HATA: {ex}")
        print(msg)
        if not hedef:
            return
        a.ad = hedef
    ust, tum = dosya_oku()
    if a.liste:
        _liste(tum)
        return
    tk = [t for t in tum if t["aktif"] and (not a.ad or N._norm(a.ad) in N._norm(t["ad"]))]
    if a.kurallar:
        _kurallar([t for t in tum if not a.ad or N._norm(a.ad) in N._norm(t["ad"])])
        return
    if not tk:
        print("aktif taktik yok (--liste; --ad süzgeci ya da 'aktif: hayir' satırı)")
        return
    (_tara_arsiv if a.arsiv else _tara_canli)(a, tk)


if __name__ == "__main__":
    main()
