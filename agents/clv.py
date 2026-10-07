# -*- coding: utf-8 -*-
"""
CLV - Closing Line Value otomasyonu (denetimin EN YUKSEK ONCELIKLI bulgusu).

SORUN: kapanis_bekle.py yazilmisti ama hicbir yerden CAGRILMIYORDU (sonsuz dongu + elle
dosya yolu gerektiriyor); --iz-kaydet/--iz-kontrol de elle isletiliyordu. Sonuc: 15 aktif
taktigin ve balina katmaninin tek olculebilir basari metrigi (CLV) hic uretilmedi.

COZUM: bu modul uc isi TEK TURDA, BLOKLAMADAN yapar; telegram_bot'un 5 dakikalik
dongusunden cagrilir (Render'da 7/24):
  1) iz kaydi  - uretilen her sinyal data/iz/iz_YYYYMMDD.json'a yazilir (oynanmasa bile)
  2) kapanis   - kickoff'a <= 5 dk kalan izlerin oranlari yakalanir (kapanis_bekle'nin isi)
  3) CLV       - kickoff gectikten sonra CLV = (bahis_orani / kapanis_orani - 1) hesaplanir

CLV neden boyle: bahis yaptigimiz kitabin KENDI kapanis orani referanstir (Buchdahl).
Pozitif CLV = piyasadan once dogru tarafta olduk. Isabet orani sansa duyarli, CLV degil;
~50 kayitta anlamli hale gelir.

    import clv
    clv.tur(veri)                 # 5 dk dongusunden: kapanis yakala + CLV hesapla
    clv.taktik_iz(maclar)         # bot'un telegram_adaylari ciktisini iz'e yaz
    clv.balina_iz(sinyaller)      # balina sinyallerini iz'e yaz
    python clv.py --rapor [--gun YYYYMMDD]
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

TR = timezone(timedelta(hours=3))
BASE = os.path.dirname(os.path.abspath(__file__))
IZ_DIR = os.path.join(BASE, "data", "iz")
PENCERE_DK = 5          # kickoff'a bu kadar kala kapanis orani yakalanir (kapanis_bekle ile ayni)
GECIKME_SN = 90 * 60    # kickoff + bu sureden sonra kapanis yakalanamadi sayilir
CLV_DOSYA = "clv_log.jsonl"
MIN_KARAR_N = 30        # bu sayinin altinda hicbir taktik hakkinda karar verilmez

# Nesine Mac Sonucu (t=1) secenek kodlari: '1'=1, '2'=X, '3'=2
MS_N = {"1": "1", "X": "2", "2": "3"}
# Alt/Ust 2.5: Nesine t=12 sov=2.5, '1'=Alt '2'=Ust (taktik.KODLAR a25/u25 ile ayni)
AU_HEDEF = {"Alt": [12, 2.5, "1"], "Üst": [12, 2.5, "2"]}


def _gun(ts=None):
    return datetime.fromtimestamp(ts or time.time(), TR).strftime("%Y%m%d")


def _iz_yol(gun=None):
    return os.path.join(IZ_DIR, "iz_%s.json" % (gun or _gun()))


def _oku(yol):
    try:
        with io.open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _yaz(yol, kayitlar):
    try:
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        with io.open(yol, "w", encoding="utf-8") as f:
            json.dump(kayitlar, f, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


def _ak(r):
    return (r.get("taktik"), r.get("esd_ms"), r.get("ev"), r.get("dep"))


def iz_kaydet(satirlar, gun=None):
    """Yeni iz satirlarini gunun dosyasina ekler (ayni taktik+mac bir kez). -> eklenen sayi."""
    if not satirlar:
        return 0
    yol = _iz_yol(gun)
    mevcut = _oku(yol)
    var = {_ak(r) for r in mevcut}
    yeni = [r for r in satirlar if _ak(r) not in var]
    if not yeni:
        return 0
    _yaz(yol, mevcut + yeni)
    try:
        import history_log as HL
        HL.sinyal_coklu_yaz([{
            "kaynak": r.get("kaynak", "taktik"), "ts": r.get("tarandi"),
            "esd_ms": r.get("esd_ms"), "ev": r.get("ev"), "dep": r.get("dep"),
            "lig": r.get("lig"), "pazar": r.get("taktik"),
            "secenek": ",".join(o.get("kod", "") for o in (r.get("oyna") or [])),
            "oran": next((o.get("oran") for o in (r.get("oyna") or []) if o.get("oran")), None),
            "karar": r.get("karar", "TAM"), "gonderildi": True,
            "balina": r.get("balina")} for r in yeni])
    except Exception:
        pass
    return len(yeni)


def taktik_iz(maclar, karar="TAM"):
    """telegram_bot.otomatik_tara'nin maclar sozlugu -> iz satirlari. -> eklenen sayi."""
    import taktik as T
    satirlar = []
    for e, uyanlar in (maclar or {}).values():
        for t, s, hedefler in uyanlar:
            oyna = []
            for o in (hedefler or t.get("oyna") or []):
                kod, deger = T.oyna_oran(e, o)
                if kod:
                    oyna.append({"kod": kod, "oran": deger})
            satirlar.append({
                "kaynak": "taktik", "taktik": t["ad"], "spor": t.get("spor", "futbol"),
                "ev": e["hn"], "dep": e["an"], "lig": e["lig"], "esd": e["esd"],
                "esd_ms": e["esd_ms"], "tarandi": time.time(), "karar": karar,
                "kurallar": [{"kod": k.ad(), "deger": v}
                             for k, (v, _) in zip(t.get("kurallar") or [], s or [])],
                "oyna": oyna})
    return iz_kaydet(satirlar)


def balina_iz(sinyaller):
    """balina.kaydet() ciktisi -> iz satirlari (pazar = Mac Sonucu, hedef t=1)."""
    satirlar = []
    for k in (sinyaller or []):
        pazar = k.get("pazar", "1X2")
        if pazar == "AU25":
            hedef = AU_HEDEF.get(k.get("sec"))
            kod = "a25" if k.get("sec") == "Alt" else "u25"
        else:
            n = MS_N.get(k.get("sec"))
            hedef = [1, 0, n] if n else None
            kod = "ms%s" % str(k.get("sec", "")).lower()
        if not hedef:
            continue
        satirlar.append({
            "kaynak": "balina", "taktik": "BALINA %s %s" % (pazar, k.get("tip", "AKIS")),
            "spor": "futbol", "ev": k["hn"], "dep": k["an"], "lig": k.get("lig"),
            "esd": k["esd_ms"] / 1000.0, "esd_ms": k["esd_ms"],
            "tarandi": k.get("ts") or time.time(), "karar": k.get("kalite") or k.get("tip"),
            "oyna": [{"kod": kod, "hedef": hedef, "oran": k.get("oran_n")}],
            "balina": {"p_w": k.get("p_w"), "ev": k.get("ev"), "dp_w": k.get("dp_w"),
                       "hucre": k.get("hucre"), "kademe": k.get("kademe"),
                       "marj_n": k.get("marj_n"), "n_kitap": k.get("n_kitap")}})
    return iz_kaydet(satirlar)


# ---------------------------------------------------------------- kapanis yakalama
def _mk_bul(veri, esd_ms, ev, dep):
    for e in veri.get("olaylar") or []:
        if e["esd_ms"] == esd_ms and e["hn"] == ev and e["an"] == dep:
            return e["mk"]
    return None


def kapanis_yakala(veri=None, pencere_dk=PENCERE_DK, gunler=2):
    """Kickoff'a <= pencere_dk kalan izlerin oranlarini yakalar (TEK TUR, bloklamaz).

    kapanis_bekle.py'nin sonsuz dongusunun yerine gecer: zamanlama artik 5 dakikalik
    otomatik taramanin isi. veri verilirse (ayni turda zaten cekilmis bulten) yeni ag
    cagrisi yapilmaz. -> {"yakalanan", "bekleyen", "gecti"}"""
    now = time.time()
    stat = {"yakalanan": 0, "bekleyen": 0, "gecti": 0}
    for i in range(gunler):
        gun = datetime.fromtimestamp(now - i * 86400, TR).strftime("%Y%m%d")
        yol = _iz_yol(gun)
        kayitlar = _oku(yol)
        if not kayitlar:
            continue
        hazir, degisti = [], False
        for r in kayitlar:
            if r.get("kapanis_orani") or not r.get("esd"):
                continue
            kalan = r["esd"] - now
            if kalan > pencere_dk * 60:
                stat["bekleyen"] += 1
            elif kalan < -GECIKME_SN:
                r["kapanis_orani"] = {"cekildi": now, "mk": None, "not": "pencere kacirildi"}
                stat["gecti"] += 1
                degisti = True
            else:
                hazir.append(r)
        if hazir and veri is None:
            import nesine as N
            veri, _ = N.bulten(True)
        for r in hazir:
            mk = _mk_bul(veri, r["esd_ms"], r["ev"], r["dep"]) if veri else None
            r["kapanis_orani"] = {"cekildi": now, "mk": mk,
                                  "not": None if mk else "mac bultende bulunamadi"}
            stat["yakalanan"] += 1
            degisti = True
        if degisti:
            _yaz(yol, kayitlar)
    return stat


# ---------------------------------------------------------------- CLV hesabi
def _kapanis_orani(mk, o):
    """iz kaydindaki oyna girdisinin kapanistaki orani (yoksa None)."""
    if not mk:
        return None
    hedef = o.get("hedef")
    try:
        import taktik as T
        if not hedef:
            hedef = T._kod_coz(o["kod"])
        return T._deger(mk, *hedef)
    except Exception:
        return None


def clv_hesapla(gunler=3):
    """Kapanisi yakalanmis + kickoff'u gecmis izler icin CLV yazar.
    -> {"hesaplanan", "atlanan"}"""
    now = time.time()
    stat = {"hesaplanan": 0, "atlanan": 0}
    try:
        import history_log as HL
    except Exception:
        return stat
    for i in range(gunler):
        gun = datetime.fromtimestamp(now - i * 86400, TR).strftime("%Y%m%d")
        yol = _iz_yol(gun)
        kayitlar = _oku(yol)
        degisti = False
        for r in kayitlar:
            if r.get("clv") is not None or not r.get("kapanis_orani") or (r.get("esd") or 0) > now:
                continue
            ko = r["kapanis_orani"]
            satirlar = []
            for o in (r.get("oyna") or []):
                bahis = o.get("oran")
                kapanis = _kapanis_orani(ko.get("mk"), o)
                if not bahis or not kapanis:
                    continue
                satirlar.append({"kod": o["kod"], "bahis_orani": bahis, "kapanis_orani": kapanis,
                                 "clv_pct": round((bahis / kapanis - 1) * 100, 2)})
            if not satirlar:
                r["clv"] = {"durum": "hesaplanamadi",
                            "sebep": ko.get("not") or "oyna orani ya da kapanis orani yok"}
                stat["atlanan"] += 1
            else:
                ort = sum(x["clv_pct"] for x in satirlar) / len(satirlar)
                r["clv"] = {"durum": "tamam", "ortalama_clv_pct": round(ort, 2),
                            "detay": satirlar, "pozitif": ort > 0}
                HL.sinyal_yaz({"kaynak": r.get("kaynak", "taktik"), "esd_ms": r.get("esd_ms"),
                               "ev": r.get("ev"), "dep": r.get("dep"), "lig": r.get("lig"),
                               "pazar": r.get("taktik"), "clv_pct": round(ort, 2),
                               "pozitif_clv": ort > 0, "detay": satirlar,
                               "balina": r.get("balina")}, dosya=CLV_DOSYA)
                stat["hesaplanan"] += 1
            degisti = True
        if degisti:
            _yaz(yol, kayitlar)
    return stat


def tur(veri=None, sessiz=True):
    """5 dakikalik dongunun cagirdigi tek giris: kapanis yakala + CLV hesapla."""
    k = kapanis_yakala(veri)
    c = clv_hesapla()
    if not sessiz or k["yakalanan"] or c["hesaplanan"]:
        print("[CLV] kapanis yakalandi %d (bekleyen %d, kacan %d) | CLV hesaplandi %d (atlanan %d)"
              % (k["yakalanan"], k["bekleyen"], k["gecti"], c["hesaplanan"], c["atlanan"]),
              flush=True)
    return {"kapanis": k, "clv": c}


def rapor(gun=None):
    try:
        import history_log as HL
    except Exception:
        print("history_log okunamadi")
        return
    kayit = HL.sinyal_oku(gun=gun, dosya=CLV_DOSYA)
    if not kayit:
        print("CLV kaydi yok. Once sinyal uretilmeli, kapanis yakalanmali ve kickoff gecmeli.")
        return
    gruplar = {}
    for k in kayit:
        gruplar.setdefault(k.get("pazar") or "?", []).append(k.get("clv_pct") or 0.0)
    print("| Taktik / Katman | n | ort. CLV | pozitif | karar |")
    print("|---|---:|---:|---:|---|")
    for ad, arr in sorted(gruplar.items(), key=lambda x: -sum(x[1]) / len(x[1])):
        ort, poz = sum(arr) / len(arr), sum(1 for v in arr if v > 0)
        karar = ("KANIT YOK (n<%d)" % MIN_KARAR_N if len(arr) < MIN_KARAR_N
                 else ("TUT" if ort > 0 else "KAPAT (CLV <= 0)"))
        print("| %s | %d | %+.2f%% | %d/%d | %s |" % (ad[:38], len(arr), ort, poz, len(arr), karar))
    tum = [v for arr in gruplar.values() for v in arr]
    print("")
    print("TOPLAM: n=%d | ortalama CLV %+.2f%% | pozitif %d"
          % (len(tum), sum(tum) / len(tum), sum(1 for v in tum if v > 0)))
    if len(tum) < MIN_KARAR_N:
        print("NOT: n < %d -> hicbir taktik hakkinda karar verilmez (Buchdahl: ~50 kayit)."
              % MIN_KARAR_N)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    g = argv[argv.index("--gun") + 1] if "--gun" in argv else None
    if "--rapor" in argv:
        rapor(g)
    elif "--kapanis" in argv:
        print(kapanis_yakala())
    else:
        tur(sessiz=False)


if __name__ == "__main__":
    main()
