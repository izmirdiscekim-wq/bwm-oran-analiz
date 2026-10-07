# -*- coding: utf-8 -*-
"""
BUGUNUN MACLARI ICIN COK-FIRMA ORANI  (The Odds API, ucretsiz katman)

Neden: arsivde yalniz 2 fiyat kaynagi var (iddaa + Nesine) ve bu ikisi yapisal
olarak ~%4,5 farkli, bagimsiz gorus degil. Capraz karsilastirma icin baska
firmalarin oranlari gerekiyor.

Kaynak: api.the-odds-api.com v4. Ucretsiz plan 500 kredi/ay, kart istemez.
  - maliyet = [pazar sayisi] x [bolge sayisi]  (gecmis veri 10x, KULLANILMIYOR)
  - TEK cagri o ligin TUM maclarini ve bolgedeki TUM firmalarini dondurur
  - eu bolgesi 25 firma (Pinnacle, 1xBet, William Hill, Unibet, Betclic ...)
  - h2h = 1X2, totals = Alt/Ust  -> 2 pazar x 1 bolge = lig basina 2 kredi

Anahtar: ortam degiskeni ODDS_API_KEY ya da agents/data/capraz/odds_api_key.txt
         Ucretsiz anahtar: https://the-odds-api.com/#get-access  (e-posta yeterli)

ACILIS ORANI: bu API gecmis acilis oranini ucretsiz vermez. Bu yuzden her cagri
  data/capraz/canli/<lig>_<gun>.json dosyasina yazilir ve **ilk cekim acilis
  olarak saklanir** (arsiv mantiginin aynisi). Yani acilis oranlari ileriye
  dogru birikir; gecmise donuk acilis yoktur.

Gunluk kota dostu: ayni gun ayni lig icin dosya varsa ve --tazele verilmediyse
  API'ye HIC gidilmez, diskten okunur (0 kredi).
"""
import os, json, time, datetime, urllib.request, urllib.parse, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(BASE, "data", "capraz", "canli")
ANAHTAR_DOSYA = os.path.join(BASE, "data", "capraz", "odds_api_key.txt")
KOK = "https://api.the-odds-api.com/v4"
KOTA_LOG = os.path.join(BASE, "data", "capraz", "odds_api_kota.json")

# Varsayilan lig listesi: Turkiye + iddaa bulteninde agirligi olan Avrupa ligleri.
# Lig basina 2 kredi (h2h+totals x eu) -> 12 lig = 24 kredi/gun ~ 500/ay sinirinda
# gunde bir tarama icin yeterli.
LIGLER = [
    # 21 lig = 42 kredi/tarama -> ayda ~11 gunluk tarama (500 kredi)
    "soccer_turkey_super_league", "soccer_epl", "soccer_spain_la_liga",
    "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league", "soccer_uefa_nations_league", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga", "soccer_efl_champ", "soccer_england_league1",
    "soccer_england_league2", "soccer_spl", "soccer_belgium_first_div",
    "soccer_austria_bundesliga", "soccer_switzerland_superleague",
    "soccer_greece_super_league", "soccer_germany_bundesliga2",
]


def anahtar():
    k = os.environ.get("ODDS_API_KEY")
    if k:
        return k.strip()
    if os.path.exists(ANAHTAR_DOSYA):
        # utf-8-sig: PowerShell'in -Encoding utf8 ile yazdigi BOM'u atar
        k = open(ANAHTAR_DOSYA, encoding="utf-8-sig").read().strip().strip("\"'")
        if k and not _yer_tutucu(k):
            return k
    return None


def _yer_tutucu(k):
    """Yer tutucu ya da bicimi bozuk anahtari yakalar ve nedenini soyler."""
    sorun = None
    if k.upper() in ("BURAYA_ANAHTAR", "YOUR_API_KEY", "APIKEY", "XXX"):
        sorun = "dosyada anahtar degil yer tutucu metin var: %r" % k
    elif len(k) != 32 or not all(c in "0123456789abcdefABCDEF" for c in k):
        sorun = ("anahtar bicimi beklenenden farkli (uzunluk %d, 32 onaltilik karakter "
                 "bekleniyor)" % len(k))
    if sorun:
        print("ANAHTAR SORUNU: %s" % sorun)
        print("  dosya: %s" % ANAHTAR_DOSYA)
        return True
    return False


def _kota_yaz(kalan, kullanilan):
    try:
        json.dump({"kalan": kalan, "kullanilan": kullanilan,
                   "zaman": datetime.datetime.now().isoformat(timespec="seconds")},
                  open(KOTA_LOG, "w", encoding="utf-8"))
    except Exception:
        pass


def _cek(lig, key, bolge="eu", pazarlar="h2h,totals"):
    url = "%s/sports/%s/odds/?%s" % (KOK, lig, urllib.parse.urlencode(
        {"apiKey": key, "regions": bolge, "markets": pazarlar,
         "oddsFormat": "decimal", "dateFormat": "iso"}))
    req = urllib.request.Request(url, headers={"User-Agent": "bwm-capraz/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            veri = json.loads(r.read().decode("utf-8"))
            _kota_yaz(r.headers.get("x-requests-remaining"),
                      r.headers.get("x-requests-used"))
            return veri, None
    except urllib.error.HTTPError as e:
        govde = ""
        try:
            govde = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return None, "HTTP %s %s" % (e.code, govde)
    except Exception as e:
        return None, str(e)[:120]


def _dosya(lig, ymd):
    return os.path.join(DDIR, "%s_%s.json" % (lig, ymd))


def _birlestir(eski, yeni):
    """Ilk cekimi 'acilis' olarak korur, her cekimde 'son'u gunceller."""
    simdi = datetime.datetime.now().isoformat(timespec="seconds")
    store = eski.get("maclar", {}) if eski else {}
    for ev in yeni:
        mid = ev.get("id")
        if not mid:
            continue
        fiyat = {}
        for bk in ev.get("bookmakers") or []:
            ad = bk.get("key")
            for mk in bk.get("markets") or []:
                anahtar_mk = mk.get("key")
                for oc in mk.get("outcomes") or []:
                    isim, deger, nokta = oc.get("name"), oc.get("price"), oc.get("point")
                    if anahtar_mk == "h2h":
                        if isim == ev.get("home_team"):
                            kod = "MS1"
                        elif isim == ev.get("away_team"):
                            kod = "MS2"
                        elif str(isim).lower() == "draw":
                            kod = "MSX"
                        else:
                            continue
                    elif anahtar_mk == "totals":
                        try:
                            if abs(float(nokta) - 2.5) > 0.01:
                                continue
                        except Exception:
                            continue
                        kod = "2.5U" if str(isim).lower() == "over" else "2.5A"
                    else:
                        continue
                    if deger:
                        fiyat.setdefault(kod, {})[ad] = float(deger)
        if not fiyat:
            continue
        k = store.setdefault(mid, {"home": ev.get("home_team"), "away": ev.get("away_team"),
                                   "kickoff": ev.get("commence_time")})
        if "ilk" not in k:
            k["ilk"], k["ilk_cekim"] = fiyat, simdi
        k["son"], k["son_cekim"] = fiyat, simdi
        k["cekim"] = k.get("cekim", 0) + 1
    return {"guncelleme": simdi, "maclar": store}


def topla(ligler=None, ymd=None, tazele=False, sessiz=False):
    """Ligleri cek (ya da diskten oku) ve {mac_id: kayit} dondur."""
    key = anahtar()
    ymd = ymd or datetime.date.today().strftime("%Y%m%d")
    os.makedirs(DDIR, exist_ok=True)
    ligler = ligler or LIGLER
    tum, kredi, hata = {}, 0, []
    for lig in ligler:
        yol = _dosya(lig, ymd)
        eski = json.load(open(yol, encoding="utf-8")) if os.path.exists(yol) else None
        if eski and not tazele:
            tum.update(eski.get("maclar", {}))
            continue
        if not key:
            hata.append("%s: anahtar yok" % lig)
            continue
        veri, err = _cek(lig, key)
        if err:
            hata.append("%s: %s" % (lig, err))
            continue
        kredi += 2
        birlesik = _birlestir(eski, veri)
        json.dump(birlesik, open(yol, "w", encoding="utf-8"), ensure_ascii=False)
        tum.update(birlesik.get("maclar", {}))
        time.sleep(0.3)
    if not sessiz:
        kota = json.load(open(KOTA_LOG, encoding="utf-8")) if os.path.exists(KOTA_LOG) else {}
        print("cok-firma oran: %d mac | bu calismada ~%d kredi | API kalan kredi: %s"
              % (len(tum), kredi, kota.get("kalan", "?")))
        if hata:
            print("  uyari: " + "; ".join(hata[:4]) + (" ..." if len(hata) > 4 else ""))
    return tum


def durum():
    key = anahtar()
    print("ODDS API anahtari: %s" % ("VAR" if key else "YOK"))
    if not key:
        print("\nUcretsiz anahtar almak icin (kart istemez, e-posta yeterli):")
        print("  1) https://the-odds-api.com/#get-access adresinden kaydol")
        print("  2) Gelen anahtari su dosyaya yaz:")
        print("     %s" % ANAHTAR_DOSYA)
        print("     (ya da ODDS_API_KEY ortam degiskenine koy)")
        print("\nUcretsiz plan: 500 kredi/ay. Lig basina 2 kredi (h2h+totals x eu bolgesi).")
        print("eu bolgesi 25 firma dondurur (Pinnacle, 1xBet, William Hill, Unibet, ...).")
        print("Varsayilan %d lig = %d kredi/tarama -> ayda ~%d tarama."
              % (len(LIGLER), len(LIGLER) * 2, 500 // (len(LIGLER) * 2)))
        return
    if os.path.exists(KOTA_LOG):
        k = json.load(open(KOTA_LOG, encoding="utf-8"))
        print("son cagri: %s | kullanilan %s | kalan %s"
              % (k.get("zaman"), k.get("kullanilan"), k.get("kalan")))
    ymd = datetime.date.today().strftime("%Y%m%d")
    var = [l for l in LIGLER if os.path.exists(_dosya(l, ymd))]
    print("bugun diskte olan lig: %d/%d" % (len(var), len(LIGLER)))
    if var:
        t = topla(sessiz=True)
        firmalar = set()
        for m in t.values():
            for kod, d in (m.get("son") or {}).items():
                firmalar.update(d)
        print("toplam %d mac, %d ayri firma: %s" % (len(t), len(firmalar),
                                                    ", ".join(sorted(firmalar)[:12])))


if __name__ == "__main__":
    import sys
    if "--durum" in sys.argv:
        durum()
    else:
        topla(tazele="--tazele" in sys.argv)
