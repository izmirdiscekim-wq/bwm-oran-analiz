# -*- coding: utf-8 -*-
"""
ACILIS - kompakt, depoya commit edilebilir acilis-orani taban cizgisi (+ bellek yedegi).

SORUN (denetim bulgusu): Render free plan'da kalici disk YOK. telegram_bot'un 5 dakikalik
dongusu her taze cekimde nesine._arsive_yaz() cagiriyor, ama o arsiv (data/arsiv/, mac basina
~91 pazar) her deploy'da sifirlaniyor -> "acilis orani" referansi bulutta kayboluyor.

COZUM: bu modul YALNIZ uc kritik pazari saklar (Mac Sonucu, Alt/Ust 2.5, Karsilikli Gol),
boylece dosya kucuk kalir ve git'e commit edilebilir; Render her deploy'da depodaki
dosyayla yeniden baslar. Disk yazilamazsa (salt-okunur/efemeral) kayit BELLEKTE tutulur,
surec yasadigi sure boyunca acilis referansi korunur.

Kayit bicimi  data/acilis/acilis_YYYYMMDD.json:
  {"<esd_ms>_<ev>_<dep>": {"ev","dep","lig","esd","ilk":{"1:0":{"1":1.39,...}},
                           "son":{...}, "ilk_ts","son_ts","n"}}
"ilk" ASLA uzerine yazilmaz = acilis orani. "son" her taramada guncellenir = o anki oran.

    import acilis
    acilis.kaydet(veri)                       # nesine.bulten() cikti sozlugu
    acilis.oran(esd_ms, ev, dep, 1)           # -> ({'1':1.39,'2':3.77,'3':5.14}, ts) acilis
    acilis.hareket(esd_ms, ev, dep, 1)        # -> {'1': (acilis, son), ...}
    python acilis.py --durum
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

TR = timezone(timedelta(hours=3))
DIZIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "acilis")
PAZARLAR = (1, 12, 38)          # Mac Sonucu | Alt/Ust 2.5 | Karsilikli Gol (Nesine t kodlari)
_BELLEK = {}                    # gun -> kayitlar (disk yazilamazsa tek kaynak)
_DISK_UYARI = {"verildi": False}


def _gun(ts=None):
    return datetime.fromtimestamp(ts or time.time(), TR).strftime("%Y%m%d")


def _yol(gun):
    return os.path.join(DIZIN, "acilis_%s.json" % gun)


def _oku(gun):
    """Disk + bellek birlesimi (bellek daha yeni sayilir)."""
    kayit = {}
    try:
        with io.open(_yol(gun), encoding="utf-8") as f:
            kayit = json.load(f)
    except (OSError, ValueError):
        pass
    kayit.update(_BELLEK.get(gun) or {})
    return kayit


def _yaz(gun, kayit):
    """-> True disk, False yalniz bellek."""
    _BELLEK[gun] = kayit
    try:
        os.makedirs(DIZIN, exist_ok=True)
        with io.open(_yol(gun), "w", encoding="utf-8") as f:
            json.dump(kayit, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return True
    except OSError as ex:
        if not _DISK_UYARI["verildi"]:
            print("ACILIS: disk yazilamadi (%s) -> bellek yedegine gecildi" % str(ex)[:60],
                  file=sys.stderr, flush=True)
            _DISK_UYARI["verildi"] = True
        return False


def _anahtar(esd_ms, ev, dep):
    return "%s_%s_%s" % (esd_ms, ev, dep)


def _pazarlar(mk):
    """Nesine mk listesi -> {"t:sov": {secenek: oran}} (yalniz PAZARLAR)."""
    out = {}
    for m in mk or []:
        if m.get("t") not in PAZARLAR:
            continue
        o = {k: v for k, v in (m.get("o") or {}).items() if v and v > 1.0}
        if o:
            out["%s:%s" % (m["t"], m.get("sov") or 0)] = o
    return out


def kaydet(veri, sessiz=True):
    """nesine.bulten() ciktisini isler. -> {"yeni","guncel","gun","disk"}."""
    cekim = veri.get("cekim") or time.time()
    gruplar, stat = {}, {"yeni": 0, "guncel": 0}
    for ev in veri.get("olaylar") or []:
        pz = _pazarlar(ev.get("mk"))
        if not pz:
            continue
        g = _gun(ev["esd"])
        kayit = gruplar.setdefault(g, _oku(g))
        ak = _anahtar(ev["esd_ms"], ev["hn"], ev["an"])
        mevcut = kayit.get(ak)
        if mevcut is None:
            kayit[ak] = {"ev": ev["hn"], "dep": ev["an"], "lig": ev["lig"], "esd": ev["esd"],
                         "ilk": pz, "son": pz, "ilk_ts": cekim, "son_ts": cekim, "n": 1}
            stat["yeni"] += 1
        else:
            for anah, o in pz.items():
                mevcut["ilk"].setdefault(anah, o)      # ACILIS asla uzerine yazilmaz
            mevcut["son"] = pz
            mevcut["son_ts"] = cekim
            mevcut["n"] = (mevcut.get("n") or 1) + 1
            stat["guncel"] += 1
    # DIKKAT: all(generator) ilk False'ta kisa devre yapar -> sonraki gunler bellege bile
    # yazilmazdi. Once tum yazimlar yapilir, sonra sonuc birlestirilir.
    sonuclar = [_yaz(g, k) for g, k in gruplar.items()]
    disk = all(sonuclar) if sonuclar else True
    stat["gun"], stat["disk"] = sorted(gruplar), disk
    if not sessiz:
        print("acilis: %d yeni, %d guncel | gun: %s | kalici disk: %s"
              % (stat["yeni"], stat["guncel"], ",".join(stat["gun"]) or "-", "evet" if disk else "HAYIR (bellek)"))
    return stat


def oran(esd_ms, ev, dep, t, sov=0, gun=None):
    """-> (acilis_oranlari, ilk_gorulme_ts) ya da (None, None)."""
    k = _oku(gun or _gun()).get(_anahtar(esd_ms, ev, dep))
    if not k:
        return None, None
    return (k["ilk"].get("%s:%s" % (t, sov or 0)), k.get("ilk_ts"))


def hareket(esd_ms, ev, dep, t, sov=0, gun=None):
    """-> {secenek: (acilis, son)} - acilis-kapanis degisimi hesaplamak icin."""
    k = _oku(gun or _gun()).get(_anahtar(esd_ms, ev, dep))
    if not k:
        return {}
    anah = "%s:%s" % (t, sov or 0)
    ilk, son = k["ilk"].get(anah) or {}, (k.get("son") or {}).get(anah) or {}
    return {s: (ilk[s], son.get(s)) for s in ilk}


def durum(gun=None):
    gun = gun or _gun()
    kayit = _oku(gun)
    disk = os.path.exists(_yol(gun))
    print("gun %s | mac %d | dosya: %s | bellekte: %d"
          % (gun, len(kayit), "var" if disk else "YOK", len(_BELLEK.get(gun) or {})))
    if kayit:
        ornek = sorted(kayit.items(), key=lambda x: x[1]["esd"])[:3]
        for ak, k in ornek:
            ms_i, ms_s = k["ilk"].get("1:0") or {}, (k.get("son") or {}).get("1:0") or {}
            print("  %-34s %s | MS acilis %s -> son %s | %d cekim"
                  % ((k["ev"] + " - " + k["dep"])[:34],
                     datetime.fromtimestamp(k["esd"], TR).strftime("%d.%m %H:%M"),
                     "/".join(str(ms_i.get(x, "-")) for x in ("1", "2", "3")),
                     "/".join(str(ms_s.get(x, "-")) for x in ("1", "2", "3")), k.get("n") or 1))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--durum" in argv:
        g = None
        if "--gun" in argv:
            g = argv[argv.index("--gun") + 1]
        durum(g)
        return
    import nesine as N
    veri, _ = N.bulten()
    kaydet(veri, sessiz=False)


if __name__ == "__main__":
    main()
