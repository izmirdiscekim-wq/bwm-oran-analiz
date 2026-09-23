"""
KATMAN B - BASKETBOL BÜLTENİ: başlamamış basketbol maçları + Nesine'de AÇILMIŞ tüm pazarlar/oranlar.
nesine.py ile AYNI mimari (aynı bülten uç noktası, TYPE alanına göre süzülür): futbol TYPE==1, basketbol TYPE==2.
Ham JSON ekrana basılmaz; her çekim ayrıca arşive (data/arsiv/basketbol_*.json) yazılır.

  python bwm.py basketbol [--gun bugun|yarin|hepsi|YYYY-MM-DD|DD.MM] [--saat 20:00-23:59] [--takim "A,B"] [--lig L]
                          [--acik ms,handikap,...] [--n 40] [--tam | --pazar "handikap,toplam"] [--yenile] [--kayitsiz] [--sozluk-yenile]

Liste (varsayılan): gün saat lig maç | MS 1/2 | Handikap | Toplam Sayı A/Ü | pz.
Detay (--takim / --tam / --pazar): maç başına çekirdek pazarlar (MS, Handikap, Toplam Sayı, İY Handikap, İY Toplam, Ev/Deplasman Toplam).
Kaynak: bulten.nesine.com/api/bulten/getprebultenfull (nesine.py ile aynı bülten, TYPE==2 = basketbol). Pazar adları iddaa'nın basketbol
pazar yapılandırmasından (get_market_config, önce st=2 ile events çağrılarak oturum basketbola göre kapsam bulur) alınır.
Basketbolda MS'de beraberlik yoktur: 2 yönlü (1/2). Taktik motoru (taktik.py) bu modülü `spor: basketbol` etiketli taktiklerde kullanır.
"""
import os, sys, json, re, time, argparse, unicodedata
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
TR = timezone(timedelta(hours=3))
URLS = ["https://bulten.nesine.com/api/bulten/getprebultenfull", "https://cdnbulten.nesine.com/api/bulten/getprebultenfull"]
UA = "Mozilla/5.0 (compatible; BWM-Bulten/1.0; kisisel-kullanim)"
CACHE = os.path.join(SCRIPT_DIR, "data", "tmp", "basketbol_son.json")
SOZLUK = os.path.join(SCRIPT_DIR, "data", "basketbol_pazar.json")
CACHE_SN = 180

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _low(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def _norm(s):
    s = unicodedata.normalize("NFKD", _low(s).replace("ı", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


# ---------------------------------------------------------------- çekim (3 dk önbellek)
SURUM = os.path.join(SCRIPT_DIR, "data", "tmp", "basketbol_surum.json")
ESKI_SN = 6 * 3600


def _oku(yol, varsayilan=None):
    try:
        return json.load(open(yol, encoding="utf-8"))
    except Exception:
        return varsayilan


def _coz(j):
    """Ham bülten -> (eventVersion, [olay]) yalnız basketbol (TYPE 2, pazarlı), başlama saatine göre sıralı."""
    sg = j.get("sg", {})
    lig = {x.get("LID"): x.get("N") for x in sg.get("LA", []) if isinstance(x, dict)}
    olaylar = []
    for e in sg.get("EA", []):
        if e.get("TYPE") == 2 and e.get("ESD") and e.get("MA"):
            olaylar.append({"esd": e["ESD"] / 1000, "esd_ms": e["ESD"], "hn": e.get("HN"), "an": e.get("AN"), "lig": lig.get(e.get("LC")) or "?",
                            "mk": [{"t": m.get("MTID"), "sov": m.get("SOV") or 0, "st": m.get("MST"),
                                    "o": {str(o.get("N")): o.get("O") for o in m.get("OCA", [])}} for m in e["MA"]]})
    olaylar.sort(key=lambda x: x["esd"])
    return sg.get("eventVersion") or 0, olaylar


def _eski_mi(sv, olaylar, gecerli_mx):
    gecmis = sum(1 for e in olaylar if e["esd"] <= time.time())
    return sv < gecerli_mx or gecmis > 3


def bulten(yenile=False):
    """-> (veri, taze_mi). veri = {'cekim','surum','olaylar':[...]} (yalnız TYPE 2 = basketbol). nesine.bulten() ile aynı taze/eski
    kopya koruması (bkz. nesine.py docstring); önbellek ve sürüm dosyaları ayrı tutulur ki iki modül birbirini geçersiz kılmasın."""
    onceki = _oku(CACHE)
    if not yenile and onceki and time.time() - onceki["cekim"] < CACHE_SN:
        return onceki, False
    mx = _oku(SURUM, {"max": 0, "ts": 0})
    gecerli_mx = mx["max"] if time.time() - mx["ts"] < ESKI_SN else 0
    secilen, en_iyi_eski, last = None, None, None
    for u, deneme in [(URLS[0], 1), (URLS[0], 2), (URLS[1], 1)]:
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
    """Her taze çekimi kalıcı basketbol arşivine işler (kaynak='basketbol', futboldan ayrı dosyalar)."""
    import bulten_arsiv as ba
    groups, stat = {}, {"yeni": 0, "guncel": 0, "donuk": 0}
    for ev in veri["olaylar"]:
        ymd = datetime.fromtimestamp(ev["esd"], TR).strftime("%Y%m%d")
        store = groups.setdefault(ymd, ba._load(ba._day_file("basketbol", ymd), {"maclar": {}})["maclar"])
        mk = [{"t": m["t"], "sov": m["sov"], "o": m["o"]} for m in ev["mk"]]
        meta = {"home": ev["hn"], "away": ev["an"], "league": ev["lig"], "id": f"{ev['esd_ms']}_{ev['hn']}_{ev['an']}"}
        stat[ba._merge(store, meta["id"], meta, ev["esd"], veri["cekim"], mk)] += 1
    ba._write_groups("basketbol", groups)
    return stat


# ---------------------------------------------------------------- pazar adları (Nesine MST -> iddaa st, oturum basketbola kapsanır)
def _sozluk_yukle():
    try:
        return json.load(open(SOZLUK, encoding="utf-8"))
    except Exception:
        return {}


def sozluk_guncelle(veri, zorla=False):
    """Bültendeki (MTID, MST) çiftlerinden sözlükte olmayanları iddaa basketbol pazar yapılandırmasından adlandırır
    (fetch_iddaa._fetch_market_config(session, st=2): önce events?st=2 ile oturum basketbola göre kapsanır, sonra config çekilir)."""
    soz = {} if zorla else _sozluk_yukle()
    eksik = {(m["t"], m["st"]) for ev in veri["olaylar"] for m in ev["mk"] if str(m["t"]) not in soz}
    if not eksik:
        return soz
    try:
        import fetch_iddaa as fi
        mc = fi._fetch_market_config(fi._get_session(), st=2)
    except Exception as ex:
        print(f"UYARI: pazar adları alınamadı ({ex}); bilinmeyenler 'Pazar <MTID>' görünür")
        return soz
    for t, st in sorted(eksik):
        e = mc.get(f"1_{st}") or mc.get(f"2_{st}")
        soz[str(t)] = {"ad": e["n"] if e else f"Pazar {t}", "st": st}
    os.makedirs(os.path.dirname(SOZLUK), exist_ok=True)
    json.dump(soz, open(SOZLUK, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return soz


def _ad(soz, t, sov):
    ad = (soz.get(str(t)) or {}).get("ad") or f"Pazar {t}"
    ad = ad.replace("Altı/Üstü", "Alt/Üst")
    ad = ad.replace("{h}", "%+g" % sov if sov else "").replace("{0}", "%g" % sov if sov else "").replace("{1}", "%g" % sov if sov else "")
    return re.sub(r"\s+", " ", ad).strip()


def _etiket(ad, n):
    """Çıktı numarası (N) -> etiket. Basketbolda beraberlik yok: 2 yönlü pazarlar 1/2 (X yok)."""
    a = _low(ad)
    if n == 2 and ("alt/üst" in a or "altı/üstü" in a):
        return ["Alt", "Üst"]
    if n == 2 and ("evet" in a or "hayır" in a or "kazan" in a):
        return ["Evet", "Hayır"]
    if n == 2 and ("maç sonucu" in a or "yarı sonucu" in a or "handikap" in a or "çeyrek sonucu" in a or "seri" in a):
        return ["1", "2"]
    if n == 3 and ("maç sonucu" in a or "yarı sonucu" in a or "handikap" in a):
        return ["1", "X", "2"]      # bazı turnuva/uzatmalı pazarlarda berabere de olabilir
    if n == 3 and "hangi" in a and ("çeyrek" in a or "periyot" in a):
        return ["1.Ç/P", "2.Ç/P", "3.Ç/P"]
    return None


# ---------------------------------------------------------------- kısayollar / sıralama (MTID, taktikler.txt @kod ile de genişletilebilir)
KISAYOL = {
    "ms": lambda m: m["t"] == 142,
    "handikap": lambda m: m["t"] == 144,
    "toplam": lambda m: m["t"] == 149,
    "toplamsayi": lambda m: m["t"] == 149,
    "iyhandikap": lambda m: m["t"] == 148,
    "iytoplam": lambda m: m["t"] == 152,
    "evtoplam": lambda m: m["t"] == 150,
    "deptoplam": lambda m: m["t"] == 151,
    "iysonuc": lambda m: m["t"] == 147,
    "ceyrek": lambda m: m["t"] in (746, 812, 813, 231),
}
CEKIRDEK = {142, 144, 149, 148, 152, 150, 151, 147, 143}
SIRA = [142, 144, 149, 147, 148, 152, 150, 151, 143, 145, 231, 810, 811, 812, 813, 226, 583, 746, 747]

KODLAR = {   # taktik.py --oyna/--kural kodları (nesine.KODLAR ile aynı biçim: (MTID, SOV veya None, N, ad))
    "bms1": (142, None, 1, "Basketbol MS 1"), "bms2": (142, None, 2, "Basketbol MS 2"),
    "bhcp1": (144, None, 1, "Handikaplı MS 1"), "bhcp2": (144, None, 2, "Handikaplı MS 2"),
    "btsu": (149, None, 2, "Toplam Sayı Üst"), "btsa": (149, None, 1, "Toplam Sayı Alt"),
    "biyhcp1": (148, None, 1, "İlk Yarı Handikaplı Sonuç 1"), "biyhcp2": (148, None, 2, "İlk Yarı Handikaplı Sonuç 2"),
    "biytsu": (152, None, 2, "İlk Yarı Toplam Sayı Üst"), "biytsa": (152, None, 1, "İlk Yarı Toplam Sayı Alt"),
    "bevtsu": (150, None, 2, "Ev Sahibi Toplam Sayı Üst"), "bevtsa": (150, None, 1, "Ev Sahibi Toplam Sayı Alt"),
    "bdeptsu": (151, None, 2, "Deplasman Toplam Sayı Üst"), "bdeptsa": (151, None, 1, "Deplasman Toplam Sayı Alt"),
    "biysonuc1": (147, None, 1, "İlk Yarı Sonucu 1"), "biysonuc2": (147, None, 2, "İlk Yarı Sonucu 2"),
}


def _esles(soz, m, anahtar):
    k = _norm(anahtar).replace(" ", "")
    if k in KISAYOL:
        return KISAYOL[k](m)
    if anahtar.strip().lower() in KODLAR:
        t, sov, _n, _ = KODLAR[anahtar.strip().lower()]
        return m["t"] == t and (sov is None or abs(m["sov"] - sov) < 0.01)
    return _norm(anahtar) in _norm(_ad(soz, m["t"], m["sov"]))


def _mk(ev, t, sov=None):
    return next((m["o"] for m in ev["mk"] if m["t"] == t and (sov is None or abs(m["sov"] - sov) < 0.01)), None) or {}


def _fmt_oran(o):
    return ("%g" % o) if o is not None else "-"


def _satir(soz, m, tavan=10):
    ad = _ad(soz, m["t"], m["sov"])
    o = {int(k): v for k, v in m["o"].items() if v}
    n = len(o)
    et = _etiket(ad, n)
    kilit = any(v <= 1.01 for v in o.values())
    marj = 100 * (sum(1 / v for v in o.values()) - 1) if o else 0
    kalem = sorted(o.items(), key=lambda kv: kv[1]) if n > 12 else sorted(o.items())
    gizli = max(0, len(kalem) - tavan) if n > 12 else 0
    if gizli:
        kalem = kalem[:tavan]
    if n > 12 and et is None:
        return f"{ad}: {n} çıktı, etiket sırası çözülmedi (atlandı) | marj %{marj:.0f}"
    par = " · ".join(f"{(et[k - 1] if et and k <= len(et) else 'N' + str(k))} {_fmt_oran(v)}" for k, v in kalem)
    return f"{ad}: {par}" + (f" (+{gizli}, en düşük {tavan} oran)" if gizli else "") + (" | 1.00 taraf var (muhtemelen kilitli), marj yok" if kilit else f" | marj %{marj:.0f}")


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
        if diger:
            print(f"  diğer açık {len(diger)} pazar türü (hepsi: --tam | seçim: --pazar ceyrek,...): " + ", ".join(diger[:10]))


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
    p = argparse.ArgumentParser(prog="bwm.py basketbol")
    p.add_argument("--gun", default="varsayilan", help="bugun | yarin | hepsi | YYYY-MM-DD | DD.MM (varsayılan: bugün+yarın; --takim verilirse hepsi)")
    p.add_argument("--saat", help="20:00-23:59 (bitiş ≤ başlangıçsa ertesi güne taşar)")
    p.add_argument("--takim", help="virgüllü; ev veya deplasman adında geçen")
    p.add_argument("--lig")
    p.add_argument("--acik", help="şu pazarların AÇIK olduğu maçlar (virgüllü; ms handikap toplam iyhandikap iytoplam evtoplam deptoplam iysonuc ceyrek ya da pazar adı)")
    p.add_argument("--n", type=int, help="liste 40 maç, detay 3 maç")
    p.add_argument("--atla", type=int, default=0, help="ilk N maçı atla (sayfalama)")
    p.add_argument("--tam", action="store_true", help="tüm açık pazarlar")
    p.add_argument("--pazar", help="detayda yalnız bu pazarlar (kısayol veya ad parçası, virgüllü)")
    p.add_argument("--yenile", action="store_true", help="önbelleği yok say, Nesine'den yeniden çek")
    p.add_argument("--kayitsiz", action="store_true", help="taze çekimi arşive yazma")
    p.add_argument("--sozluk-yenile", action="store_true", help="pazar adı sözlüğünü iddaa yapılandırmasından yeniden kur")
    a = p.parse_args(argv)

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
    print(f"Nesine bülteni {datetime.fromtimestamp(veri['cekim'], TR):%d.%m %H:%M} (sürüm {veri.get('surum')}){kayit} | başlamamış basketbol {len(tum)}: "
          + ", ".join(f"{k} {v}" for k, v in gunler.items()) + f" | Handikap açık {sum(1 for e in tum if _mk(e, 144))}, Toplam Sayı açık {sum(1 for e in tum if _mk(e, 149))}"
          + " | Nesine oranı (iddaa API'den ~%4.5 düşük)")
    gun = "hepsi" if (a.gun == "varsayilan" and a.takim) else a.gun
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
        print("süzgeçe uyan başlamamış basketbol maçı yok (gün/takım adını kontrol et; bülten yalnız bugün ve ileri günleri içerir)")
        return
    if a.pazar:
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
    print("gün   saat lig                maç                                 MS 1/2      Handikap        Toplam A/Ü    pz")
    for e in sayfa:
        ms, hc, ts = _mk(e, 142), _mk(e, 144), _mk(e, 149)
        d = datetime.fromtimestamp(e["esd"], TR)
        print(f"{d:%d.%m %H:%M} {e['lig'][:18]:18} {(e['hn'][:17] + ' - ' + e['an'][:17]):36} "
              f"{_fmt_oran(ms.get('1'))}/{_fmt_oran(ms.get('2')):<9} {_fmt_oran(hc.get('1'))}/{_fmt_oran(hc.get('2')):<13} "
              f"{_fmt_oran(ts.get('1'))}/{_fmt_oran(ts.get('2')):<11} {len(e['mk']):3}")
    if devam:
        print(devam)


if __name__ == "__main__":
    main()
