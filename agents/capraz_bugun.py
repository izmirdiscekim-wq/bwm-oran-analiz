# -*- coding: utf-8 -*-
"""
BUGUNUN MACLARI x GECMIS ORAN PROFILI  (birlesik kosullandirma)

Mantik: bir macin TEK bir orani degil, ORAN PROFILI sorgulanir.
  "MS 1 su bandda acildi VE 2,5 Ust su bandda acildi" olan gecmis maclarda
  sonuc nasil dagildi?  -> MS1 %x, MS X %y, MS 2 %z, 2,5 Ust %u, 2,5 Alt %a

Boylece iki pazar birlikte filtre olur ve cikti "bu oranlarla mac boyle bitebilir"
seklinde tum sonuc dagilimini verir. Her secenek icin bugunun GERCEK Nesine/iddaa
fiyatiyla EV hesaplanir.

Kaynak: bugun = arsiv kaydi (bwm.py arsiv kaydet); gecmis = 161.039 mac /
11 ulke / 1993-2021 (capraz_veri.py), >=N firma mutabakati sarti ile.

UYARI: hicbir bant cok-test duzeltmesini gecmedi. Tarihsel dagilim bir
       GOSTERGEDIR, dogrulanmis kenar degil. "Boyle bitebilir" demek
       "boyle biter" demek degildir; karar satirinda EV ve n birlikte okunur.
"""
import os, json, re, unicodedata, datetime, argparse, math

BASE = os.path.dirname(os.path.abspath(__file__))
ARSIV = os.path.join(BASE, "data", "arsiv")
PAZAR_JSON = os.path.join(BASE, "data", "nesine_pazar.json")

# cikti sirasi ve sonuc fonksiyonlari (fthg, ftag)
SONUC = [
    ("MS 1", "ms1", lambda h, a: h > a),
    ("MS X", "msx", lambda h, a: h == a),
    ("MS 2", "ms2", lambda h, a: h < a),
    ("2,5 Ust", "u25", lambda h, a: h + a >= 3),
    ("2,5 Alt", "a25", lambda h, a: h + a <= 2),
]
# bugunun kaydindan okunan secenek kodlari -> cikti adi
KOD_AD = {"MS1": "MS 1", "MSX": "MS X", "MS2": "MS 2", "2.5U": "2,5 Ust", "2.5A": "2,5 Alt"}
# capraz.py pazar kodu -> bugunun kayit kodu
BACAK_KOD = {"ms1": "MS1", "msx": "MSX", "ms2": "MS2", "u25": "2.5U", "a25": "2.5A"}

# Denenen profil kombinasyonlari. Tek bir (ms1,u25) profiline baglanmak yerine
# butun anlamli bacak ciftleri taranir:
#   - 1X2 bacagi x gol bacagi (6 kombinasyon): macin hem sonuc hem gol profili
#   - 1X2 ici ciftler (3): macin seklini iki yonden sabitler
#   - tek bacaklar (5): gol bacagi yoksa yalniz 1X2, ya da tersi
PROFILLER = [
    ("ms1", "u25"), ("ms1", "a25"), ("msx", "u25"), ("msx", "a25"),
    ("ms2", "u25"), ("ms2", "a25"),
    ("ms1", "ms2"), ("ms1", "msx"), ("msx", "ms2"),
    ("ms1",), ("msx",), ("ms2",), ("u25",), ("a25",),
]


def _fiyatlanabilir(kod, bacaklar):
    """Bir secenek ancak KENDI pazarinin orani profilde sabitlenmisse fiyatlanir.

    1X2 ucluSU toplami 1 oldugu icin iki bacak ucuncuyu de sabitler.
    2,5 Alt ve Ust birbirinin tumleyeni oldugu icin biri digerini sabitler.
    Bu kural olmadan "MS X bandina kosullanip MS 1'i fiyatlamak" gibi anlamsiz
    karsilastirmalar sahte yuzlerce yuzde EV uretir.
    """
    b = set(bacaklar)
    if kod in ("u25", "a25"):
        return bool(b & {"u25", "a25"})
    if kod in ("ms1", "msx", "ms2"):
        if kod in b:
            return True
        return len({"ms1", "msx", "ms2"} & b) >= 2
    return False


def _jeton(s):
    import unicodedata as _u
    t = _u.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()


def _benzer(a, b):
    import difflib
    ja, jb = " ".join(_jeton(a)), " ".join(_jeton(b))
    if not ja or not jb:
        return 0.0
    return difflib.SequenceMatcher(None, ja, jb).ratio()


def _tutarli(i_ac, dis_ac, tolerans=0.12):
    """Eslesmenin GERCEKTEN ayni mac olup olmadigini oranlarla dogrular.

    Isim benzerligi yanilabilir (ligde olmayan bir mac baska bir fikstur ile
    eslesebilir). Ayni macin 1X2 ima ettigi olasiliklar iki kaynakta birbirine
    yakin olmak zorundadir; degilse eslesme REDDEDILIR. Bu kontrol olmadan
    "pinnacle MS1 1.76 / iddaa 2.79" gibi imkansiz ciftler sahte +%58 EV uretir.
    """
    ortak = 0
    for kod in ("MS1", "MSX", "MS2"):
        a1 = i_ac.get(kod)
        d = dis_ac.get(kod) or {}
        if not a1 or not d:
            continue
        # dis kaynagin medyan orani
        v = sorted(d.values())
        a2 = v[len(v) // 2]
        if abs(1.0 / a1 - 1.0 / a2) > tolerans:
            return False
        ortak += 1
    return ortak >= 2


def _bulanik_es(ev, dep, aday, esik=0.58, kickoff=None):
    """Isim benzerligi ADAY URETIR (esik gevsek: Turkce-Ingilizce ad farki buyuk,
    'Iskocya'-'Scotland' 0.64 verir). Eslesmenin dogrulugunu _tutarli() karara
    baglar; tek basina isim benzerligine GUVENILMEZ."""
    import datetime as _dt
    en, skor = None, 0.0
    ko = None
    if kickoff:
        try:
            ko = _dt.datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        except Exception:
            ko = None
    for r in aday:
        if ko is not None and r.get("kickoff"):
            try:
                k2 = _dt.datetime.fromisoformat(str(r["kickoff"]).replace("Z", "+00:00"))
                if abs((k2 - ko).total_seconds()) > 6 * 3600:
                    continue
            except Exception:
                pass
        s2 = (_benzer(ev, r.get("home")) + _benzer(dep, r.get("away"))) / 2
        if s2 > skor:
            skor, en = s2, r
    return (en, skor) if skor >= esik else (None, skor)


def _sade(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _yukle(tur, ymd):
    p = os.path.join(ARSIV, "%s_%s.json" % (tur, ymd))
    if not os.path.exists(p):
        return {}
    return json.load(open(p, encoding="utf-8")).get("maclar", {})


def _nesine_secenekler(m, faz, pz):
    out = {}
    for x in (m.get(faz) or []):
        t, sov, o = str(x.get("t")), x.get("sov"), (x.get("o") or {})
        if t == "1" and o:
            for k, kod in (("1", "MS1"), ("2", "MSX"), ("3", "MS2")):
                if o.get(k):
                    out.setdefault(kod, float(o[k]))
        elif pz.get(t, {}).get("ad", "").startswith("Alt/") and sov is not None:
            try:
                if abs(float(sov) - 2.5) < 0.01:
                    if o.get("1"):
                        out.setdefault("2.5A", float(o["1"]))
                    if o.get("2"):
                        out.setdefault("2.5U", float(o["2"]))
            except Exception:
                pass
    return out


def _iddaa_secenekler(m, faz):
    out = {}
    son = (m or {}).get(faz) or {}
    mo = son.get("match_outcome") or {}
    for k, kod in (("1", "MS1"), ("X", "MSX"), ("2", "MS2")):
        if mo.get(k):
            out[kod] = float(mo[k])
    for blok in (son.get("totals_goals") or []):
        try:
            if abs(float(blok.get("line")) - 2.5) > 0.01:
                continue
        except Exception:
            continue
        for ad, v in (blok.get("outcomes") or {}).items():
            a = _sade(ad)
            if a.startswith("alt") and v:
                out.setdefault("2.5A", float(v))
            elif a.startswith("ust") and v:
                out.setdefault("2.5U", float(v))
    return out


class Gecmis:
    """161 bin maclik veriyi BIR KEZ hazirlar, sonra bant sorgularini vektorel yapar."""

    def __init__(self, df):
        import numpy as np, capraz
        self.df = df
        self.n = len(df)
        h = df["fthg"].to_numpy(int)
        a = df["ftag"].to_numpy(int)
        self.tut = {kod: fn(h, a) for _, kod, fn in SONUC}
        self.M, self.firma = {}, {}
        for _, kod, _fn in SONUC:
            gercek, turetilmis = capraz.firma_sutunlari(df, kod, "ac")
            # 2,5 Alt/Ust'te gercek firma kapsami ince; ~40 firma uzerinden turetilmis
            # market_avg/market_max sutunlari da kosullandirmaya katilir (83 bin mac).
            kaynak = dict(gercek)
            if kod in ("u25", "a25"):
                kaynak.update(turetilmis)
            if not kaynak:
                self.M[kod] = None
                continue
            self.firma[kod] = list(kaynak)
            self.M[kod] = df[list(kaynak.values())].to_numpy(float)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            self.en_iyi = {kod: (np.nanmax(self.M[kod], axis=1) if self.M[kod] is not None else None)
                           for _, kod, _f in SONUC}

    def maske(self, kod, oran, pencere, onay):
        """Bu pazarda >=onay firma [oran-p, oran+p] bandinda mi."""
        import numpy as np
        M = self.M.get(kod)
        if M is None:
            return None
        with np.errstate(invalid="ignore"):
            icinde = (M >= oran - pencere) & (M <= oran + pencere)
        kota = ~np.isnan(M)
        return (icinde.sum(axis=1) >= onay) & (kota.sum(axis=1) >= onay)

    def maske_firma(self, kod, firma, oran, pencere):
        """SADECE o adli firmanin kendi gecmis sutununa gore bant maskesi.

        Kullanicinin istedigi sorgu bu: "Betfair'de 9.60'la acilmis; gecmiste
        Betfair 9.60'la acanlar nasil bitmis?" -> konsensus degil, o firmanin
        kendi tarihsel orani.
        """
        import numpy as np
        M = self.M.get(kod)
        if M is None or firma not in (self.firma.get(kod) or []):
            return None
        kol = self.firma[kod].index(firma)
        v = M[:, kol]
        with np.errstate(invalid="ignore"):
            return (v >= oran - pencere) & (v <= oran + pencere)

    def firma_var(self, kod):
        return set(self.firma.get(kod) or [])

    def dagilim(self, maske):
        """Bu alt kumede her sonucun gercek frekansi."""
        import numpy as np
        n = int(maske.sum())
        if not n:
            return n, {}
        return n, {kod: float(self.tut[kod][maske].mean()) for _, kod, _f in SONUC}

    def roi_t(self, kod, maske):
        """Bu alt kumede bu secenegin gecmis karliligi (en iyi Avrupa fiyatiyla)."""
        import numpy as np
        o = self.en_iyi.get(kod)
        if o is None:
            return float("nan"), float("nan")
        ok = maske & ~np.isnan(o)
        k = int(ok.sum())
        if k < 2:
            return float("nan"), float("nan")
        kar = np.where(self.tut[kod][ok], o[ok] - 1.0, -1.0)
        sd = float(np.std(kar, ddof=1))
        roi = float(kar.mean()) * 100
        return roi, (float(kar.mean()) / (sd / math.sqrt(k)) if sd else float("nan"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bwm.py capraz --bugun",
        description="Bugunun maclarini gecmis ORAN PROFILI ile eslestir (MS + 2,5 birlikte)")
    ap.add_argument("--gun", help="YYYY-MM-DD (varsayilan bugun)")
    ap.add_argument("--pencere", type=float, default=0.10, help="bant yarim genisligi (vars. 0.10)")
    ap.add_argument("--onay", type=int, default=3, help="MS bandinda en az kac firma mutabakati (vars. 3)")
    ap.add_argument("--onay-ou", type=int, default=1,
                    help="2,5 bandinda en az kac kaynak (vars. 1; bu pazarda firma kapsami ince)")
    ap.add_argument("--min-n", type=int, default=200, help="gecmiste en az kac benzer mac (vars. 200)")
    ap.add_argument("--min-oran", type=float, default=0, help="yalniz tarihsel frekans >= %% olanlar")
    ap.add_argument("--ev", type=float, default=None, help="yalniz EV >= bu deger (orn. 0)")
    ap.add_argument("--n", type=int, default=20, help="kac mac basilacak")
    ap.add_argument("--sirala", default="ev", choices=["ev", "oran", "t"],
                    help="oran = gecmiste EN COK GELMIS olana gore sirala; ev = deger; t = anlamlilik")
    ap.add_argument("--profil", help="yalniz bu profil, virgulle: ms1,u25 | ms2,a25 | msx,u25 | "
                                     "ms1,ms2 | ms2 ... (bos = TUM kombinasyonlar taranir)")
    ap.add_argument("--firma", help="ADLI FIRMA SORGUSU: bu firmanin bugunku orani ile "
                                    "gecmiste AYNI firmanin ayni bandda actigi maclari "
                                    "karsilastir (orn. pinnacle, williamhill). "
                                    "--kaynak epl ile betfair_ex ve onexbet de kullanilabilir")
    ap.add_argument("--kaynak", default="cok", choices=["cok", "epl"],
                    help="gecmis havuz: cok = 161.039 mac/11 ulke (pinnacle, williamhill); "
                         "epl = 12.705 mac Premier Lig (betfair_ex, onexbet de var)")
    ap.add_argument("--cok-firma", action="store_true",
                    help="The Odds API'den 25 Avrupa firmasinin bugunku oranini da kat "
                         "(anahtar gerekir: bwm.py capraz --canli-durum)")
    ap.add_argument("--canli-durum", action="store_true",
                    help="cok-firma kaynaginin anahtar/kota durumunu goster ve cik")
    ap.add_argument("--kaydet", action="store_true",
                    help="adaylari data/capraz/adaylar.json'a yaz (sonradan sonuc olcmek icin)")
    ap.add_argument("--fiyat", default="iddaa", choices=["iddaa", "nesine", "eniyi"],
                    help="EV hangi fiyatla hesaplanacak. VARSAYILAN iddaa (kullanici iddaa'da oynuyor)")
    ap.add_argument("--en-iyi", action="store_true",
                    help="her mac icin yalniz en iyi EV'li satiri bas (kisa ozet)")
    a = ap.parse_args(argv or [])
    if a.profil:
        a.profil = [x.strip() for x in a.profil.split(",") if x.strip()]
        for x in a.profil:
            if x not in BACAK_KOD:
                raise SystemExit("HATA bilinmeyen bacak '%s'. Gecerli: %s"
                                 % (x, ", ".join(BACAK_KOD)))
        if tuple(sorted(a.profil)) not in [tuple(sorted(p)) for p in PROFILLER]:
            raise SystemExit("HATA bu bacak birlesimi tanimli degil. Tanimlilar: %s"
                             % " | ".join(",".join(p) for p in PROFILLER))

    if a.canli_durum:
        import capraz_canli
        capraz_canli.durum()
        return

    ymd = (a.gun or datetime.date.today().isoformat()).replace("-", "")
    nes, idd = _yukle("nesine", ymd), _yukle("iddaa", ymd)
    if not nes and not idd:
        raise SystemExit("HATA %s icin arsiv kaydi yok. Once: bwm.py arsiv kaydet" % ymd)
    pz = json.load(open(PAZAR_JSON, encoding="utf-8")) if os.path.exists(PAZAR_JSON) else {}

    if a.kaynak == "epl":
        import capraz as _c
        df = _c.yukle()
    else:
        import capraz_veri
        df = capraz_veri.yukle()
    G = Gecmis(df)
    if a.firma:
        var = G.firma_var("ms1")
        if a.firma not in var:
            raise SystemExit("HATA '%s' bu gecmis havuzda yok.\n  Mevcut: %s\n"
                             "  Ipucu: betfair_ex / onexbet icin --kaynak epl kullanin."
                             % (a.firma, ", ".join(sorted(var))))
        if not a.cok_firma:
            print("NOT --firma kullanildi, --cok-firma otomatik aciliyor "
                  "(bugunun %s orani dis kaynaktan geliyor)\n" % a.firma)
            a.cok_firma = True

    idx = {}
    for m in idd.values():
        idx[(_sade(m.get("home"))[:9], _sade(m.get("away"))[:9])] = m

    cok = []
    es_sayisi = 0
    red = [0]
    if a.cok_firma:
        import capraz_canli
        if not capraz_canli.anahtar():
            print("UYARI --cok-firma istendi ama API anahtari yok; yalniz iddaa+Nesine "
                  "ile devam ediliyor. Anahtar icin: bwm.py capraz --canli-durum\n")
        else:
            cok = list(capraz_canli.topla().values())

    maclar, eslesen, atlanan = [], 0, 0
    for m in nes.values():
        im = idx.get((_sade(m.get("home"))[:9], _sade(m.get("away"))[:9]))
        if im:
            eslesen += 1
        n_ac = _nesine_secenekler(m, "ilk", pz)
        n_son = _nesine_secenekler(m, "son", pz)
        i_ac = _iddaa_secenekler(im, "ilk")
        i_son = _iddaa_secenekler(im, "son")
        ac = dict(i_ac); ac.update(n_ac)          # acilis: Nesine onde
        if a.fiyat == "nesine":
            son = dict(n_son)
        elif a.fiyat == "iddaa":
            son = dict(i_son)
        else:
            son = {k: max([x for x in (n_son.get(k), i_son.get(k)) if x] or [0]) or None
                   for k in KOD_AD}
        # diger firmalarin bugunku oranlari (varsa)
        ck = None
        if cok:
            ck, _sk = _bulanik_es(m.get("home"), m.get("away"), cok, kickoff=m.get("kickoff"))
            if ck and not _tutarli(i_ac or n_ac, ck.get("ilk") or ck.get("son") or {}):
                ck = None          # isim benzedi ama oranlar ayni maci gostermiyor
                red[0] += 1
            if ck:
                es_sayisi += 1
        dis_son = (ck or {}).get("son") or {}
        dis_ac = (ck or {}).get("ilk") or {}
        kaynak = {}
        for k in KOD_AD:
            nv, iv = n_son.get(k), i_son.get(k)
            d = {kk: vv for kk, vv in (dis_son.get(k) or {}).items() if 1.05 <= vv <= 30}
            if son.get(k) is None and not d:
                continue
            p = []
            if nv:
                p.append("N %.2f" % nv)
            if iv:
                p.append("I %.2f" % iv)
            if d:
                en = max(d, key=d.get)
                p.append("%d firma, en iyi %s %.2f" % (len(d), en[:9], d[en]))
                lo = min(d.values())
                p.append("fark %.2f" % (d[en] / lo) if lo else "")
            kaynak[k] = " | ".join(x for x in p if x)
            # EV icin kullanilan fiyat: kullanicinin oynadigi yer (--fiyat) degismez;
            # diger firmalar KARSILASTIRMA ve mutabakat icin raporlanir.
        son = {k: v for k, v in son.items() if v and 1.05 <= v <= 15.0}
        ac = {k: v for k, v in ac.items() if v and 1.05 <= v <= 30.0}
        # --- TUM PROFIL KOMBINASYONLARI ---
        # Tek bir (MS1 + 2,5 Ust) profiline baglanmak yerine her mac icin butun
        # anlamli bacak ciftleri denenir. MS1 bandina kosullanmak MSX/MS2'yi serbest
        # birakir; MS2 bandina kosullanmak GERCEKTEN farkli bir alt kume verir.
        satir = dict(mac="%s - %s" % (m.get("home"), m.get("away")),
                     saat=(m.get("kickoff") or "")[11:16], lig=(m.get("league") or "")[:16],
                     sec=[])
        for bacaklar in PROFILLER:
            if a.profil and sorted(bacaklar) != sorted(a.profil):
                continue
            oranlar, mk, tamam = [], None, True
            for bkod in bacaklar:
                if a.firma:
                    # bugunun orani: dis kaynaktaki O FIRMANIN acilis orani
                    o_ac = (dis_ac.get(BACAK_KOD[bkod]) or {}).get(a.firma)
                    if not o_ac:
                        tamam = False
                        break
                    mm = G.maske_firma(bkod, a.firma, o_ac, a.pencere)
                else:
                    o_ac = ac.get(BACAK_KOD[bkod])
                    if not o_ac:
                        tamam = False
                        break
                    esik = a.onay_ou if bkod in ("u25", "a25") else a.onay
                    mm = G.maske(bkod, o_ac, a.pencere, esik)
                if mm is None:
                    tamam = False
                    break
                mk = mm if mk is None else (mk & mm)
                oranlar.append("%s %s %.2f" % ((a.firma[:9] + ":") if a.firma else "",
                                               KOD_AD[BACAK_KOD[bkod]], o_ac))
            if not tamam or mk is None:
                continue
            n, dag = G.dagilim(mk)
            if n < a.min_n:
                continue
            gol_bacagi = any(b in ("u25", "a25") for b in bacaklar)
            profil_ad = " + ".join(oranlar)
            for ad, kod, _fn in SONUC:
                frek = dag.get(kod)
                if frek is None:
                    continue
                if not _fiyatlanabilir(kod, bacaklar):
                    continue
                fiyat = son.get(next(k for k, v in KOD_AD.items() if v == ad))
                roi, rt = G.roi_t(kod, mk)
                satir["sec"].append(dict(ad=ad, kod=kod, frek=frek, fiyat=fiyat,
                                         ev=(frek * fiyat - 1.0) if fiyat else None,
                                         basabas=(1 / frek if frek else float("inf")),
                                         roi=roi, roi_t=rt, profil=profil_ad, n=n,
                                         kaynak=kaynak.get(
                                             next(k for k, v in KOD_AD.items() if v == ad), "-")))
        if not satir["sec"]:
            atlanan += 1
            continue
        maclar.append(satir)

    print("=" * 104)
    print("BUGUNUN MACLARI x GECMIS ORAN PROFILI   gun=%s" % ymd)
    print("Nesine %d mac, iddaa %d mac, isimle eslesen %d, olcute uymayan %d"
          % (len(nes), len(idd), eslesen, atlanan))
    if a.cok_firma:
        print("dis kaynak (The Odds API): %d mac havuzu, eslesen %d mac, "
              "oran tutarsizligi nedeniyle REDDEDILEN %d" % (len(cok), es_sayisi, red[0]))
    print("gecmis havuz %d mac (11 ulke, 1993-2021) | bant +/-%.2f | >=%d firma mutabakati | en az %d mac"
          % (len(df), a.pencere, a.onay, a.min_n))
    print("=" * 104)
    if not maclar:
        print("Olcute uyan mac yok. --min-n dusurun veya --pencere buyutun.")
        return

    def _deger(s2):
        if a.sirala == "oran":
            return s2["frek"]
        if a.sirala == "t":
            return s2["roi_t"] if s2["roi_t"] == s2["roi_t"] else -9
        return s2["ev"] if s2["ev"] is not None else -9

    def enb(r):
        v = [_deger(s2) for s2 in r["sec"] if s2["ev"] is not None]
        return max(v) if v else -9
    maclar.sort(key=lambda r: -enb(r))

    basildi = 0
    for r in maclar:
        sec = [s for s in r["sec"] if s["fiyat"]]
        if a.min_oran:
            sec = [s for s in sec if s["frek"] * 100 >= a.min_oran]
        if a.ev is not None:
            sec = [s for s in sec if s["ev"] is not None and s["ev"] >= a.ev]
        if not sec:
            continue
        basildi += 1
        if basildi > a.n:
            break
        print("\n%s  %s  [%s]" % (r["saat"], r["mac"], r["lig"]))
        print("   %-9s %7s %7s %7s %8s %7s %6s  %s"
              % ("secenek", "gecmis", "basbs", "fiyat", "EV", "ROI_t", "n",
                 "acilis profili" + " " * 14 + "fiyat kaynagi"))
        sira = sorted(sec, key=lambda x: -_deger(x))
        if a.en_iyi:
            sira = sira[:1]
        for s in sira:
            print("   %-9s %6.1f%% %7.2f %7.2f %+7.1f%% %+7.2f %6d  %-28s %s"
                  % (s["ad"], s["frek"] * 100, s["basabas"], s["fiyat"],
                     s["ev"] * 100, s["roi_t"] if s["roi_t"] == s["roi_t"] else 0.0,
                     s["n"], s["profil"], s.get("kaynak", "-")))
    print("\n" + "-" * 104)
    tum = [s for r in maclar for s in r["sec"] if s["ev"] is not None]
    poz = [s for s in tum if s["ev"] > 0]
    aday = [s for s in poz if s["roi_t"] == s["roi_t"] and s["roi_t"] >= 2]
    print("%d mac olculdu, %d (mac x profil x secenek) fiyatlandi, EV > 0 olan: %d (%%%.1f)"
          % (len(maclar), len(tum), len(poz), 100.0 * len(poz) / max(len(tum), 1)))
    print("bunlardan ROI_t >= 2 olan ADAY: %d" % len(aday))
    if tum:
        import math as _m
        bekl = len(tum) * 0.0228
        print("COK-TEST UYARISI: %d test yapildi; yalniz sans eseri ~%.0f tanesinin ROI_t>=2"
              % (len(tum), bekl))
        print("vermesi beklenir. %d aday bu sayinin %s -> %s"
              % (len(aday), "UZERINDE" if len(aday) > bekl else "ALTINDA/ESITINDE",
                 "sinyal olabilir, ileriye dogrulanmali" if len(aday) > bekl * 1.5
                 else "gurultuyle ayirt edilemez"))
    print("gecmis = bu oran profiline benzeyen gecmis maclarda o sonucun gerceklesme yuzdesi")
    print("basbs  = basabas oran (1/gecmis). Bugunun fiyati bunun UZERINDE ise EV pozitif")
    print("EV     = gecmis frekans x bugunun en iyi Nesine/iddaa fiyati - 1")
    print("ROI_t  = o secenegin bu alt kumedeki gecmis karliliginin t degeri; |t|<2 = dogrulanmamis")
    print("UYARI  : hicbir bant cok-test duzeltmesini gecmedi; bu tablo gosterge, kanit degil.")
    if a.kaydet:
        yol = os.path.join(BASE, "data", "capraz", "adaylar.json")
        kayit = json.load(open(yol, encoding="utf-8")) if os.path.exists(yol) else {}
        kayit[ymd] = [dict(mac=r["mac"], saat=r["saat"], lig=r["lig"], sec=s2["ad"],
                           kod=s2["kod"], frek=round(s2["frek"], 4), fiyat=s2["fiyat"],
                           ev=round(s2["ev"], 4), roi_t=round(s2["roi_t"], 3), n=s2["n"],
                           profil=s2["profil"], kaynak=s2.get("kaynak", "-"),
                           fiyat_kaynagi=a.fiyat, sonuc=None)
                      for r in maclar for s2 in r["sec"]
                      if s2["ev"] is not None and s2["ev"] > 0
                      and s2["roi_t"] == s2["roi_t"] and s2["roi_t"] >= 2]
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        json.dump(kayit, open(yol, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("KAYDEDILDI: %d aday -> data/capraz/adaylar.json [%s]" % (len(kayit[ymd]), ymd))


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
