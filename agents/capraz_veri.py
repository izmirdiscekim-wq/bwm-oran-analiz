# -*- coding: utf-8 -*-
"""
CAPRAZ MOTORU ICIN COK LIGLI VERI YUKLEYICI

Kaynak: football-data.co.uk ham CSV'lerinin GitHub aynasi
        (jokecamp/FootballData -> football-data.co.uk/<ulke>/...)
        Asil site TR'den DNS ile kapali; ayna erisilebilir.

Kapsam: 11 ulke (Turkiye dahil), 1993 -> 2020, ~460 dosya.
        1X2: 12+ gercek firma. 2,5 Alt/Ust: eski donemde yalniz BbMx/BbAv
        (~40 firma uzerinden maksimum/ortalama), 2019+ dosyalarda B365/Pinnacle
        ayri ayri + kapanis.

Cikti: capraz.py'nin bekledigi kolon semasina cevrilmis tek tablo:
       <firma>_1x2_home / _1x2_draw / _1x2_away / _over25 / _under25
       ve bunlarin _close varyantlari.  Boylece capraz.py'deki olcum, kirilim,
       mutabakat ve kesif kodu degismeden calisir.
"""
import os, re, io, json, csv, urllib.request, urllib.parse, concurrent.futures

BASE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(BASE, "data", "capraz")
REPO = "https://raw.githubusercontent.com/jokecamp/FootballData/master/"
AGAC = "https://api.github.com/repos/jokecamp/FootballData/git/trees/HEAD?recursive=1"
ONBELLEK = os.path.join(DDIR, "cokligli.csv.gz")
DOSYA_LISTE = os.path.join(DDIR, "cokligli_dosyalar.json")

# football-data.co.uk sutun oneki -> firma adi
FIRMA = {
    "B365": "bet365", "BS": "bluesquare", "BW": "bwin", "GB": "gamebookers",
    "IW": "interwetten", "LB": "ladbrokes", "PS": "pinnacle", "P": "pinnacle",
    "SB": "sportingbet", "SJ": "stanjames", "SO": "sportingodds", "VC": "vcbet",
    "WH": "williamhill", "1XB": "onexbet", "BF": "betfair", "BFE": "betfair_ex",
    "BM": "betmgm", "BV": "betvictor", "CL": "coral", "SY": "stanleybet",
    "LD": "ladbrokes", "SP": "sportingbet",
    # turetilmis (gercek firma degil): ~40 firma uzerinden maks/ortalama
    "Max": "market_max", "Avg": "market_avg",
    "BbMx": "market_max", "BbAv": "market_avg",
}
YON = {"H": "1x2_home", "D": "1x2_draw", "A": "1x2_away"}
TUT = ["date", "lig", "sezon", "home_team", "away_team", "fthg", "ftag"]


def _oku(url, deneme=3):
    for i in range(deneme):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
        except Exception:
            if i == deneme - 1:
                return None
    return None


def dosya_listesi(yenile=False):
    os.makedirs(DDIR, exist_ok=True)
    if os.path.exists(DOSYA_LISTE) and not yenile:
        return json.load(open(DOSYA_LISTE, encoding="utf-8"))
    t = _oku(AGAC)
    if not t:
        raise SystemExit("HATA GitHub agaci okunamadi (ag?)")
    yol = [x["path"] for x in json.loads(t)["tree"]
           if x["path"].endswith(".csv") and "football-data.co.uk/" in x["path"]]
    # mac dosyasi olmayanlari at (oyuncu listeleri, tek kolonlu dosyalar vb.)
    yol = [p for p in yol if not re.search(r"players|scorers|fixtures", p, re.I)]
    json.dump(yol, open(DOSYA_LISTE, "w", encoding="utf-8"))
    return yol


def _sutun_coz(ad):
    """'B365CH' -> ('bet365','1x2_home',True) | 'BbMx>2.5' -> ('market_max','over25',False)"""
    ad = ad.strip()
    m = re.fullmatch(r"([A-Za-z0-9]+?)(C?)([><])2\.5", ad)
    if m:
        onek, kap, yon = m.group(1), m.group(2) == "C", m.group(3)
        if onek in FIRMA:
            return FIRMA[onek], ("over25" if yon == ">" else "under25"), kap
        return None
    m = re.fullmatch(r"([A-Za-z0-9]+?)(C?)([HDA])", ad)
    if m:
        onek, kap, yon = m.group(1), m.group(2) == "C", m.group(3)
        # 'BbAvAHH' gibi handikap sutunlarini disla (AH icerir)
        if "AH" in ad:
            return None
        if onek in FIRMA:
            return FIRMA[onek], YON[yon], kap
    return None


def _tarih(s):
    s = (s or "").strip()
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            import datetime
            return datetime.datetime.strptime(s, f).date().isoformat()
        except Exception:
            pass
    return None


def _dosya_isle(yol):
    txt = _oku(REPO + urllib.parse.quote(yol))
    if not txt or "," not in txt.split("\n", 1)[0]:
        return []
    try:
        rows = list(csv.DictReader(io.StringIO(txt)))
    except Exception:
        return []
    if not rows:
        return []
    ulke = yol.split("football-data.co.uk/")[1].split("/")[0]
    sezon = next((p for p in yol.split("/") if re.fullmatch(r"\d{4}-\d{4}", p)), "")
    lig = os.path.splitext(os.path.basename(yol))[0]
    harita = {}
    for ad in rows[0].keys():
        if not ad:
            continue
        c = _sutun_coz(ad)
        if c:
            firma, ek, kap = c
            harita[ad] = "%s_%s%s" % (firma, ek, "_close" if kap else "")
    cikti = []
    for r in rows:
        t = _tarih(r.get("Date") or r.get("date"))
        hg, ag = r.get("FTHG") or r.get("HG"), r.get("FTAG") or r.get("AG")
        ev, dep = r.get("HomeTeam") or r.get("Home"), r.get("AwayTeam") or r.get("Away")
        if not (t and ev and dep) or hg in (None, "") or ag in (None, ""):
            continue
        try:
            d = {"date": t, "lig": "%s/%s" % (ulke, lig), "sezon": sezon,
                 "home_team": ev.strip(), "away_team": dep.strip(),
                 "fthg": int(float(hg)), "ftag": int(float(ag))}
        except Exception:
            continue
        for ad, hedef in harita.items():
            v = r.get(ad)
            if v in (None, "", "-"):
                continue
            try:
                o = float(str(v).replace(",", "."))
            except Exception:
                continue
            if 1.01 <= o <= 1000:
                # ayni hedefe birden fazla kaynak sutun gelirse (PS ve P) ilkini koru
                d.setdefault(hedef, o)
        cikti.append(d)
    return cikti


def yukle(yenile=False, is_sayisi=12, ilerleme=True):
    """Tum ligleri indirir/onbellekten okur ve tek DataFrame dondurur."""
    import pandas as pd
    os.makedirs(DDIR, exist_ok=True)
    if os.path.exists(ONBELLEK) and not yenile:
        return pd.read_csv(ONBELLEK, low_memory=False)
    yollar = dosya_listesi(yenile)
    if ilerleme:
        print("cok ligli veri indiriliyor: %d dosya ..." % len(yollar))
    kayitlar, bitti = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=is_sayisi) as ex:
        for sonuc in ex.map(_dosya_isle, yollar):
            kayitlar.extend(sonuc)
            bitti += 1
            if ilerleme and bitti % 60 == 0:
                print("  %d/%d dosya, %d mac" % (bitti, len(yollar), len(kayitlar)))
    if not kayitlar:
        raise SystemExit("HATA hic mac okunamadi")
    df = pd.DataFrame(kayitlar)
    df = df.drop_duplicates(subset=["date", "home_team", "away_team"]).reset_index(drop=True)
    df["match_id"] = df.index.astype(str)
    df.to_csv(ONBELLEK, index=False, compression="gzip")
    if ilerleme:
        print("tamam: %d mac, %d kolon -> %s" % (len(df), len(df.columns), ONBELLEK))
    return df


def ozet(df):
    import pandas as pd
    print("COK LIGLI VERI: %d mac | %s .. %s" % (len(df), df["date"].min(), df["date"].max()))
    ulke = df["lig"].str.split("/").str[0].value_counts()
    print("ulkeler: " + ", ".join("%s %d" % (k, v) for k, v in ulke.items()))
    for ek in ("1x2_home", "over25"):
        for son in ("", "_close"):
            kol = [c for c in df.columns if c.endswith(ek + son)
                   and not (son == "" and c.endswith("_close"))]
            gercek = [c for c in kol if not c.startswith("market_")]
            if not kol:
                continue
            print("\n%s%s -> %d gercek firma, %d turetilmis"
                  % (ek, son, len(gercek), len(kol) - len(gercek)))
            for c in sorted(kol, key=lambda x: -int(df[x].notna().sum()))[:24]:
                print("   %-26s %6d mac" % (c, int(df[c].notna().sum())))


if __name__ == "__main__":
    import sys
    df = yukle("--yenile" in sys.argv)
    ozet(df)
