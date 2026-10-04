"""
KATMAN A2 - NESINE BÜLTENİ: başlamamış futbol maçları + Nesine'de AÇILMIŞ tüm pazarlar/oranlar (kullanıcının oynadığı oran).
Ham JSON ekrana basılmaz; her çekim ayrıca arşive (data/arsiv/nesine_*.json) yazılır, böylece oran geçmişi biriker.

  python bwm.py nesine [--gun bugun|yarin|hepsi|YYYY-MM-DD|DD.MM] [--saat 20:00-23:59] [--takim "A,B"] [--lig L]
                       [--acik iyms,skor,...] [--n 40] [--tam | --pazar "ust,kg"] [--yenile] [--kayitsiz] [--sozluk-yenile]

Liste (varsayılan): gün saat lig maç | MS 1/X/2 | A/Ü 2.5 | KG | pazar sayısı | özel açık pazarlar.
Detay (--takim / --tam / --pazar): maç başına çekirdek pazarlar (MS, ÇŞ, A/Ü 1.5-4.5, KG, İY, İY/MS, Toplam Gol, Handikap, Skor) + marj;
--tam = tüm açık pazarlar; --pazar = yalnız adı/kısayolu eşleşenler. Kısayollar: iyms skor iyskor toplamgol ust45 kombine korner kart handikap.
Kaynak: bulten.nesine.com/api/bulten/getprebultenfull (anahtarsız; yalnız bugün ve ileri günler; biten maç yok). Pazar adları iddaa
pazar yapılandırmasından (Nesine MST = iddaa mst) alınıp data/nesine_pazar.json'a yazılır; oran-etiket sırası iddaa arşiviyle oran
oranı (~0.953) ve marjsız olasılık testleriyle doğrulanmıştır (21.09.2026).
"""
import os, sys, json, re, time, argparse, unicodedata
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
TR = timezone(timedelta(hours=3))
URLS = ["https://bulten.nesine.com/api/bulten/getprebultenfull", "https://cdnbulten.nesine.com/api/bulten/getprebultenfull"]
UA = "Mozilla/5.0 (compatible; BWM-Bulten/1.0; kisisel-kullanim)"
CACHE = os.path.join(SCRIPT_DIR, "data", "tmp", "nesine_son.json")
SOZLUK = os.path.join(SCRIPT_DIR, "data", "nesine_pazar.json")
CACHE_SN = 180          # aynı bülten 3 dk içinde tekrar indirilmez (2.4 MB)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _low(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()      # str.lower() 'İ'yi i + birleşik nokta yapar; Türkçe eşleşme bozulur


def _norm(s):
    s = unicodedata.normalize("NFKD", _low(s).replace("ı", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


# ---------------------------------------------------------------- çekim (3 dk önbellek)
SURUM = os.path.join(SCRIPT_DIR, "data", "tmp", "nesine_surum.json")   # görülen en yüksek eventVersion (eski/CDN kopyasını ayıklamak için)
ESKI_SN = 6 * 3600      # bu süreden eski kayıt varsa "en yüksek sürüm" kuralı uygulanmaz (yeni gün / sürüm sıfırlanması)


def _oku(yol, varsayilan=None):
    try:
        return json.load(open(yol, encoding="utf-8"))
    except Exception:
        return varsayilan


def _coz(j):
    """Ham bülten -> (eventVersion, [olay]) yalnız futbol (TYPE 1, pazarlı), başlama saatine göre sıralı."""
    sg = j.get("sg", {})
    lig = {x.get("LID"): x.get("N") for x in sg.get("LA", []) if isinstance(x, dict)}
    olaylar = []
    for e in sg.get("EA", []):
        if e.get("TYPE") == 1 and e.get("ESD") and e.get("MA"):
            olaylar.append({"esd": e["ESD"] / 1000, "esd_ms": e["ESD"], "hn": e.get("HN"), "an": e.get("AN"), "lig": lig.get(e.get("LC")) or "?",
                            "mk": [{"t": m.get("MTID"), "sov": m.get("SOV") or 0, "st": m.get("MST"),
                                    "o": {str(o.get("N")): o.get("O") for o in m.get("OCA", [])}} for m in e["MA"]]})
    olaylar.sort(key=lambda x: x["esd"])
    return sg.get("eventVersion") or 0, olaylar


def _eski_mi(sv, olaylar, gecerli_mx):
    """Eski kopya: görülen en yüksek sürümden düşük VEYA saati geçmiş çok maç barındırıyor (taze bülten başlamış maçı içermez)."""
    gecmis = sum(1 for e in olaylar if e["esd"] <= time.time())
    return sv < gecerli_mx or gecmis > 3


def bulten(yenile=False):
    """-> (veri, taze_mi). veri = {'cekim','surum','olaylar':[{esd,esd_ms,hn,an,lig,mk:[{t,sov,st,o:{N:oran}}]}]} (yalnız TYPE 1 = futbol).
    bulten.nesine.com taze ama bazen zaman aşımına düşer; cdnbulten.nesine.com saatler öncesinin kopyasını döndürebilir (21.09'da v942.5M-942.7M,
    başlamış 47 maç dahil; taze v943.0M). Görülen en yüksek sürümden düşük ya da başlamış maç içeren kopya reddedilir; olmazsa önbellek
    kullanılır (taze_mi=False: arşive yazılmaz)."""
    onceki = _oku(CACHE)
    if not yenile and onceki and time.time() - onceki["cekim"] < CACHE_SN:
        return onceki, False
    mx = _oku(SURUM, {"max": 0, "ts": 0})
    gecerli_mx = mx["max"] if time.time() - mx["ts"] < ESKI_SN else 0
    secilen, en_iyi_eski, last = None, None, None
    for u, deneme in [(URLS[0], 1), (URLS[0], 2), (URLS[1], 1)]:        # origin iki kez, sonra CDN
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=45)
            r.raise_for_status()
            sv, olaylar = _coz(r.json())
        except Exception as ex:
            last = ex
            continue
        if not _eski_mi(sv, olaylar, gecerli_mx):
            secilen = (sv, olaylar)
            break
        if en_iyi_eski is None or sv > en_iyi_eski[0]:
            en_iyi_eski = (sv, olaylar)
    if secilen is None:
        neden = f"ULAŞILAMADI ({last})" if en_iyi_eski is None else f"ESKİ kopya döndü (v{en_iyi_eski[0]}, görülen en yüksek v{gecerli_mx})"
        if onceki:
            print(f"UYARI: Nesine {neden}; {(time.time() - onceki['cekim']) / 60:.0f} dk önceki önbellek kullanılıyor (arşive yazılmadı)")
            return onceki, False
        if en_iyi_eski:
            print(f"UYARI: Nesine {neden}; başka kaynak yok, ESKİ olabilir (arşive yazılmadı)")
            return {"cekim": time.time(), "surum": en_iyi_eski[0], "olaylar": en_iyi_eski[1]}, False
        sys.exit(f"Nesine {neden}")
    sv, olaylar = secilen
    veri = {"cekim": time.time(), "surum": sv, "olaylar": olaylar}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(veri, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"max": max(sv, gecerli_mx), "ts": time.time()}, open(SURUM, "w"))
    return veri, True


def _arsive_yaz(veri):
    """Her taze çekimi kalıcı Nesine arşivine işler (başlamış maçlar dondurulur; bulten_arsiv.kaydet ile aynı kayıt biçimi)."""
    import bulten_arsiv as ba
    groups, stat = {}, {"yeni": 0, "guncel": 0, "donuk": 0}
    for ev in veri["olaylar"]:
        ymd = datetime.fromtimestamp(ev["esd"], TR).strftime("%Y%m%d")
        store = groups.setdefault(ymd, ba._load(ba._day_file("nesine", ymd), {"maclar": {}})["maclar"])
        mk = [{"t": m["t"], "sov": m["sov"], "o": m["o"]} for m in ev["mk"]]
        meta = {"home": ev["hn"], "away": ev["an"], "league": ev["lig"], "id": f"{ev['esd_ms']}_{ev['hn']}_{ev['an']}"}
        stat[ba._merge(store, meta["id"], meta, ev["esd"], veri["cekim"], mk)] += 1
    ba._write_groups("nesine", groups)
    return stat


# ---------------------------------------------------------------- pazar adları (Nesine MST = iddaa mst)
def _sozluk_yukle():
    try:
        return json.load(open(SOZLUK, encoding="utf-8"))
    except Exception:
        return {}


def sozluk_guncelle(veri, zorla=False):
    """Bültendeki (MTID, MST) çiftlerinden sözlükte olmayanları iddaa pazar yapılandırmasından adlandırır."""
    soz = {} if zorla else _sozluk_yukle()
    eksik = {(m["t"], m["st"]) for ev in veri["olaylar"] for m in ev["mk"] if str(m["t"]) not in soz}
    if not eksik:
        return soz
    try:
        import fetch_iddaa as fi
        mc = fi._fetch_market_config(fi._get_session())
    except Exception as ex:
        print(f"UYARI: pazar adları alınamadı ({ex}); bilinmeyenler 'Pazar <MTID>' görünür")
        return soz
    for t, st in sorted(eksik):
        e = mc.get(f"1_{st}") if t == 1 else (mc.get(f"2_{st}") or mc.get(f"1_{st}"))
        soz[str(t)] = {"ad": e["n"] if e else f"Pazar {t}", "st": st}
    os.makedirs(os.path.dirname(SOZLUK), exist_ok=True)
    json.dump(soz, open(SOZLUK, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return soz


def _ad(soz, t, sov):
    ad = (soz.get(str(t)) or {}).get("ad") or f"Pazar {t}"
    ad = ad.replace("Altı/Üstü", "Alt/Üst")
    ad = ad.replace("{h}", "%+g" % sov if sov else "").replace("{0}", "%g" % sov if sov else "")
    return re.sub(r"\s+", " ", ad).strip()


NESINE_SKOR = ["1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "6:0", "0:0", "1:1", "2:2", "3:3",
               "0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "0:6", "diğer"]
IY_SKOR = ["0:0", "1:1", "2:2", "1:0", "2:0", "2:1", "0:1", "0:2", "1:2", "diğer"]
IYMS = ["1/1", "1/X", "1/2", "X/1", "X/X", "X/2", "2/1", "2/X", "2/2"]


def _etiket(ad, n):
    """Çıktı numarası (N) -> etiket. Sıralar iddaa arşivi + marjsız olasılık testleriyle doğrulandı; bilinmeyen: None (N1..Nk)."""
    a = _low(ad)
    if n == 29 and "maç skoru" in a:
        return NESINE_SKOR
    if n == 10 and "1. yarı skoru" in a:
        return IY_SKOR
    if n == 9 and "yarı / maç sonucu" in a:
        return IYMS
    if n == 6 and " ve " in a and ("alt/üst" in a or "altı/üstü" in a):
        return ["1&Alt", "X&Alt", "2&Alt", "1&Üst", "X&Üst", "2&Üst"]
    if n == 6 and " ve " in a and "karşılıklı gol" in a:
        return ["1&Var", "1&Yok", "X&Var", "X&Yok", "2&Var", "2&Yok"]
    if n == 4 and " ve karşılıklı gol" in a:
        return ["Alt&Var", "Üst&Var", "Alt&Yok", "Üst&Yok"]
    if n == 4 and "1. yarı ve 2. yarıda karşılıklı gol" in a:
        return ["Yok/Yok", "Var/Yok", "Var/Var", "Yok/Var"]       # (İY KG / 2.Y KG); 41 maçta 452+599 marjsız olasılıklarıyla çözüldü (hata 0.012 vs 0.095)
    if n == 4 and a == "toplam gol":
        return ["0-1", "2-3", "4-5", "6+"]
    if n == 3 and "hangi yarıda daha fazla gol" in a:
        return ["1.Y", "Eşit", "2.Y"]
    if n == 3 and "çifte şans" in a:
        return ["1X", "12", "X2"]
    if n == 3 and ("kim daha çok korner" in a):
        return ["Ev", "Eşit", "Dep"]
    if n == 3 and ("ilk golü" in a or "ilk korneri" in a):
        return ["Ev", "Yok", "Dep"]
    if n == 3 and ("maç sonucu" in a or "yarı sonucu" in a or "handikap" in a):
        return ["1", "X", "2"]
    if n == 2 and "tek" in a and "çift" in a:
        return ["Tek", "Çift"]
    if n == 2 and "korner handikap" in a:
        return ["1", "2"]
    if n == 2 and "karşılıklı gol" in a:
        return ["Var", "Yok"]
    if n == 2 and ("alt/üst" in a or "altı/üstü" in a):
        return ["Alt", "Üst"]
    if n == 2 and any(w in a for w in ("kazanır", " atar", "her iki yarıda", "yemeden")):
        return ["Evet", "Hayır"]
    return None


# ---------------------------------------------------------------- kısayollar / sıralama
KORNER = {216, 218, 798, 799, 299, 340, 338, 224, 222, 220, 601, 602}
KISAYOL = {
    "iyms": lambda m: m["t"] == 5,
    "skor": lambda m: m["t"] == 777,
    "iyskor": lambda m: m["t"] == 779,
    "toplamgol": lambda m: m["t"] == 43,
    "ust45": lambda m: m["t"] == 155 and abs(m["sov"] - 4.5) < 0.01,
    "kombine": lambda m: m["t"] == 459,
    "korner": lambda m: m["t"] in KORNER,
    "kart": lambda m: m["t"] == 301,
    "handikap": lambda m: m["t"] == 268,
}
CEKIRDEK = {1, 3, 268, 11, 12, 13, 155, 38, 7, 5, 43, 777}
SIRA = [1, 3, 268, 207, 11, 12, 13, 155, 38, 49, 43, 7, 8, 9, 5, 209, 14, 15, 343, 342, 272, 414, 446, 459, 416, 801, 48, 777, 779]


def _esles(soz, m, anahtar):
    k = _norm(anahtar).replace(" ", "")
    if k in KISAYOL:
        return KISAYOL[k](m)
    try:
        import taktik                      # taktik pazar kodları (iykg, tg6, ciftu15 ...) da --pazar/--acik olarak çalışsın
        if anahtar.strip().lower() in taktik.KODLAR:
            t, sov, _n, _ = taktik.KODLAR[anahtar.strip().lower()]
            return m["t"] == t and (sov is None or abs(m["sov"] - sov) < 0.01)
    except ImportError:
        pass
    return _norm(anahtar) in _norm(_ad(soz, m["t"], m["sov"]))


def _mk(ev, t, sov=None):
    return next((m["o"] for m in ev["mk"] if m["t"] == t and (sov is None or abs(m["sov"] - sov) < 0.01)), None) or {}


def _fmt_oran(o):
    return ("%g" % o) if o is not None else "-"


def _satir(soz, m, tavan=10):
    ad = _ad(soz, m["t"], m["sov"])
    o = {int(k): v for k, v in m["o"].items() if v}
    n = len(o)
    et = _etiket(ad.replace("Alt/Üst", "Alt/Üst"), n)
    kilit = any(v <= 1.01 for v in o.values())          # 1.00 = tavan/kilit: o taraf pratikte oynanamaz, marj anlamsız
    katsayi = 2 if "çifte şans" in _low(ad) else 1     # çifte şansta olasılık toplamı 2'dir
    marj = 100 * (sum(1 / v for v in o.values()) / katsayi - 1) if o else 0
    kalem = sorted(o.items(), key=lambda kv: kv[1]) if n > 12 else sorted(o.items())
    gizli = max(0, len(kalem) - tavan) if n > 12 else 0
    if gizli:
        kalem = kalem[:tavan]
    if n > 12 and et is None:
        return f"{ad}: {n} çıktı, etiket sırası çözülmedi (atlandı) | marj %{marj:.0f}"
    par = " · ".join(f"{(et[k - 1] if et and k <= len(et) else 'N' + str(k))} {_fmt_oran(v)}" for k, v in kalem)
    return f"{ad}: {par}" + (f" (+{gizli} skor, en düşük {tavan} oran)" if gizli else "") + (" | 1.00 taraf var (muhtemelen kilitli), marj yok" if kilit else f" | marj %{marj:.0f}")


def _detay(soz, ev, a):
    gun = datetime.fromtimestamp(ev["esd"], TR)
    print(f"■ {gun:%d.%m %H:%M} | {ev['lig']} | {ev['hn']} - {ev['an']} | {len(ev['mk'])} pazar")
    mk = ev["mk"]
    if a.pazar:
        anah = [x.strip() for x in a.pazar.split(",") if x.strip()]
        sec = [m for m in mk if any(_esles(soz, m, k) for k in anah)]
    elif a.tam:
        sec = list(mk)
    else:
        sec = [m for m in mk if m["t"] in CEKIRDEK]
    sira = {t: i for i, t in enumerate(SIRA)}
    sec.sort(key=lambda m: (sira.get(m["t"], 999), m["t"], m["sov"]))
    for m in sec:
        print("  " + _satir(soz, m))
    if not a.tam and not a.pazar:
        diger = sorted({_ad(soz, m["t"], 0) for m in mk if m["t"] not in CEKIRDEK})
        grup = {"1.Yarı": 0, "2.Yarı": 0, "Korner": 0, "Kart": 0, "Takım bazlı": 0}
        kalan = []
        for ad in diger:
            k = ("Korner" if "korner" in _low(ad) else "Kart" if "kart" in _low(ad) else "1.Yarı" if re.match(r"1\. ?Yar", ad)
                 else "2.Yarı" if re.match(r"2\. ?Yar", ad) else "Takım bazlı" if re.match(r"(Ev Sahibi|Deplasman)", ad) else None)
            if k:
                grup[k] += 1
            else:
                kalan.append(ad)
        print(f"  diğer açık {len(diger)} pazar türü: " + ", ".join(f"{k} {v}" for k, v in grup.items() if v)
              + (", diğer: " + ", ".join(kalan[:10]) if kalan else "") + "  (hepsi: --tam | seçim: --pazar 1.yari,korner,...)")


def _arsiv_goster(a):
    """Canlı bülten çekmeden, data/arsiv/nesine_YYYYMMDD.json'daki KAYITLI son oranları gösterir (gerçek kapanış testi için)."""
    import bulten_arsiv as ba
    ymd = a.arsiv.replace("-", "")
    gun = ba._load(ba._day_file("nesine", ymd), {"maclar": {}})
    soz = _sozluk_yukle()
    recs = sorted(gun["maclar"].values(), key=lambda r: r["kickoff"])
    ks = [_norm(t) for t in a.takim.split(",")] if a.takim else []
    gosterilen = 0
    for r in recs:
        if ks and not any(k in _norm(r["home"]) or k in _norm(r["away"]) for k in ks):
            continue
        ev = {"esd": datetime.fromisoformat(r["kickoff"]).timestamp(), "lig": r["league"], "hn": r["home"], "an": r["away"], "mk": r["son"]}
        flag = "KAPANIŞA YAKIN" if r.get("cekim", 1) > 1 and not r.get("kapanis_oncesi", True) else ("son kayıt" if r.get("kapanis_oncesi", True) else "kapanış sonrası")
        print(f"[{flag}, son çekim {r['son_cekim'][11:16]}, {r.get('cekim', 1)}x, kickoff {r['kickoff'][11:16]}]")
        _detay(soz, ev, a)
        gosterilen += 1
    if not gosterilen:
        print(f"nesine_{ymd}.json: eşleşen maç yok ({len(recs)} kayıtlı maç var)")


# ---------------------------------------------------------------- komut
def _saat_ok(esd, aralik):
    m = datetime.fromtimestamp(esd, TR)
    dk = m.hour * 60 + m.minute
    bas, bit = (int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in aralik.split("-"))
    return bas <= dk <= bit if bas < bit else (dk >= bas or dk < bit)


def _gun_ok(esd, gun):
    d = datetime.fromtimestamp(esd, TR).date()
    bugun = datetime.now(TR).date()
    if gun == "hepsi":
        return True
    if gun in (None, "", "varsayilan"):
        return d <= bugun + timedelta(days=1)
    if gun == "bugun":
        return d == bugun
    if gun == "yarin":
        return d == bugun + timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", gun):
        return d.isoformat() == gun
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", gun)
    return bool(m) and (d.day, d.month) == (int(m[1]), int(m[2]))


def main(argv=None):
    p = argparse.ArgumentParser(prog="bwm.py nesine")
    p.add_argument("--gun", default="varsayilan", help="bugun | yarin | hepsi | YYYY-MM-DD | DD.MM (varsayılan: bugün+yarın; --takim verilirse hepsi)")
    p.add_argument("--saat", help="20:00-23:59 (bitiş ≤ başlangıçsa ertesi güne taşar)")
    p.add_argument("--takim", help="virgüllü; ev veya deplasman adında geçen")
    p.add_argument("--lig")
    p.add_argument("--acik", help="şu pazarların AÇIK olduğu maçlar (virgüllü; iyms skor iyskor toplamgol ust45 kombine korner kart handikap ya da pazar adı)")
    p.add_argument("--n", type=int, help="liste 40 maç, detay 3 maç")
    p.add_argument("--atla", type=int, default=0, help="ilk N maçı atla (sayfalama; çıktı sonunda ipucu verir)")
    p.add_argument("--tam", action="store_true", help="tüm açık pazarlar")
    p.add_argument("--pazar", help="detayda yalnız bu pazarlar (kısayol veya ad parçası, virgüllü)")
    p.add_argument("--yenile", action="store_true", help="önbelleği yok say, Nesine'den yeniden çek")
    p.add_argument("--kayitsiz", action="store_true", help="taze çekimi arşive yazma")
    p.add_argument("--sozluk-yenile", action="store_true", help="pazar adı sözlüğünü iddaa yapılandırmasından yeniden kur")
    p.add_argument("--arsiv", help="YYYY-MM-DD: canlı çekim YAPMADAN o günün Nesine arşivinden (kayıtlı son/kapanış oranı) göster; --takim ile filtrele")
    a = p.parse_args(argv)

    if a.arsiv:
        return _arsiv_goster(a)

    veri, taze = bulten(a.yenile)
    soz = sozluk_guncelle(veri, zorla=a.sozluk_yenile)
    kayit = ""
    if taze and not a.kayitsiz:
        s = _arsive_yaz(veri)
        kayit = f" | arşiv: yeni {s['yeni']}, güncel {s['guncel']}"
    now = time.time()
    tum = [e for e in veri["olaylar"] if e["esd"] > now]
    gunler = {}
    for e in tum:
        k = datetime.fromtimestamp(e["esd"], TR).strftime("%d.%m")
        gunler[k] = gunler.get(k, 0) + 1
    print(f"Nesine bülteni {datetime.fromtimestamp(veri['cekim'], TR):%d.%m %H:%M} (sürüm {veri.get('surum')}){kayit} | başlamamış futbol {len(tum)}: "
          + ", ".join(f"{k} {v}" for k, v in gunler.items()) + f" | İY/MS açık {sum(1 for e in tum if _mk(e, 5))}, skor {sum(1 for e in tum if _mk(e, 777))}"
          + " | Nesine oranı (iddaa API'den ~%4.5 düşük)")
    gun = "hepsi" if (a.gun == "varsayilan" and a.takim) else a.gun     # adı verilen maç hangi gün olursa olsun bulunsun
    sec = [e for e in tum if _gun_ok(e["esd"], gun)]
    if a.saat:
        sec = [e for e in sec if _saat_ok(e["esd"], a.saat)]
    if a.takim:
        ks = [_norm(x) for x in a.takim.split(",") if x.strip()]
        sec = [e for e in sec if any(k in _norm(e["hn"]) or k in _norm(e["an"]) for k in ks)]
    if a.lig:
        sec = [e for e in sec if _norm(a.lig) in _norm(e["lig"])]
    if a.acik:
        anah = [x.strip() for x in a.acik.split(",") if x.strip()]
        sec = [e for e in sec if all(any(_esles(soz, m, k) for m in e["mk"]) for k in anah)]
    if not sec:
        print("süzgeçe uyan başlamamış maç yok (gün/takım adını kontrol et; bülten yalnız bugün ve ileri günleri içerir)")
        return
    if a.pazar:     # yalnız istenen pazarı açık olan maçlar (başlık satırı boşa yer kaplamasın)
        anah = [x.strip() for x in a.pazar.split(",") if x.strip()]
        sec = [e for e in sec if any(_esles(soz, m, k) for m in e["mk"] for k in anah)]
        if not sec:
            print("bu pazar süzgeçteki hiçbir maçta açık değil")
            return
    detay = bool(a.takim or a.tam or a.pazar)
    n = a.n or (3 if detay else 40)
    sayfa = sec[a.atla:a.atla + n]
    kalan = len(sec) - a.atla - len(sayfa)
    devam = f"  [+{kalan} maç daha: --atla {a.atla + n}]" if kalan > 0 else ""
    print(f"süzgeç sonrası {len(sec)} maç" + (f" (gösterilen {a.atla + 1}-{a.atla + len(sayfa)})" if len(sec) > len(sayfa) else ""))
    if detay:
        for e in sayfa:
            _detay(soz, e, a)
        if devam:
            print(devam)
        return
    print("gün   saat lig                maç                                 MS 1/X/2         A/Ü 2.5      KG Var/Yok  pz  açık")
    for e in sayfa:
        ms, au, kg = _mk(e, 1), _mk(e, 12, 2.5), _mk(e, 38)
        ozel = " ".join(x for x, t in (("İYMS", 5), ("SKOR", 777), ("İYSKOR", 779), ("TG", 43), ("4.5Ü", 155)) if _mk(e, t)) + (" KORNER" if any(m["t"] in KORNER for m in e["mk"]) else "")
        d = datetime.fromtimestamp(e["esd"], TR)
        print(f"{d:%d.%m %H:%M} {e['lig'][:18]:18} {(e['hn'][:17] + ' - ' + e['an'][:17]):36} "
              f"{_fmt_oran(ms.get('1'))}/{_fmt_oran(ms.get('2'))}/{_fmt_oran(ms.get('3')):<7} {_fmt_oran(au.get('1'))}/{_fmt_oran(au.get('2')):<9} "
              f"{_fmt_oran(kg.get('1'))}/{_fmt_oran(kg.get('2')):<8} {len(e['mk']):3} {ozel}")
    if devam:
        print(devam)


if __name__ == "__main__":
    main()
