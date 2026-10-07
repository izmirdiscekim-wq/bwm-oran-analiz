# -*- coding: utf-8 -*-
"""
CAPRAZ ORAN TAKTIK MOTORU  (cross-bookmaker odds tactic backtest)

Amac: bir pazarin oranini TEK bir bahis firmasina bakarak degil, ayni mac icin
birden fazla firmanin actigi oranlara birlikte bakarak filtrelemek; her firma
icin ayri ayri "bu oranla kac mac acilmis, kaci gelmis" olcup firmalari
karsilastirmak.

Veri: football-data.co.uk verisinin GitHub aynasi (TR'den erisilebilir; asil site
      DNS ile kapali). 12.705 Premier Lig maci, 1993-94 -> 2025-26,
      acilis + kapanis oranlari, 20 gercek bahis firmasi.
      DIKKAT: bu veride Iddaa/Nesine YOK. Yontem burada dogrulanir, Iddaa/Nesine
      ancak kendi ileriye donuk arsivimizle (bulten_arsiv.py) eklenebilir.

ONEMLI OLCUM KURALI: "15 macta 14'u geldi" tek basina kanit DEGILDIR. Iki sebep:
  1) Bant zaten olasiligi ima eder. 1.60-1.95 bandindaki 2,5 Ust ~%55-62
     olasilik demektir; %58 tutmasi SIFIR avantajdir.
  2) n=15'te %93 isabet rahatlikla sanstir. Bu yuzden her satirda n, piyasa
     imasi, fark (edge), basabas oran ve z degeri birlikte basilir.
"""
import sys, os, math, argparse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(BASE, "data", "capraz")
RAW = ("https://raw.githubusercontent.com/AnishKhetani/"
       "premier-league-data/main/data/processed/")

# market_* ve betbrain_* turetilmis sutunlardir, gercek firma degil
SENTETIK = ("market_", "betbrain_")

PAZAR = {
    # kod:     (sutun_eki,   sonuc_fonksiyonu,          karsit_kod)
    "ms1": ("1x2_home", lambda h, a: h > a, None),
    "msx": ("1x2_draw", lambda h, a: h == a, None),
    "ms2": ("1x2_away", lambda h, a: h < a, None),
    "u25": ("over25", lambda h, a: h + a >= 3, "a25"),
    "a25": ("under25", lambda h, a: h + a <= 2, "u25"),
}
UCLU = ("ms1", "msx", "ms2")
AGG = ("min", "max", "avg", "med", "n", "spread")
BANTLAR = [(1.30, 1.55), (1.55, 1.70), (1.70, 1.85), (1.85, 2.00),
           (2.00, 2.30), (2.30, 2.80), (2.80, 4.00), (4.00, 15.0)]


# ---------------------------------------------------------------- veri

def _indir(dosya):
    os.makedirs(DDIR, exist_ok=True)
    yol = os.path.join(DDIR, dosya)
    if not os.path.exists(yol):
        req = urllib.request.Request(RAW + dosya, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as r, open(yol, "wb") as f:
            f.write(r.read())
    return yol


def yukle(yenile=False):
    import pandas as pd
    if yenile:
        for d in ("results.csv", "results_with_odds.csv"):
            p = os.path.join(DDIR, d)
            if os.path.exists(p):
                os.remove(p)
    res = pd.read_csv(_indir("results.csv"), low_memory=False)
    odds = pd.read_csv(_indir("results_with_odds.csv"), low_memory=False)
    odds = odds.drop(columns=["season", "date", "home_team", "away_team"], errors="ignore")
    df = res[["match_id", "season", "date", "home_team", "away_team", "fthg", "ftag"]] \
        .merge(odds, on="match_id", how="inner")
    return df.dropna(subset=["fthg", "ftag"]).reset_index(drop=True)


def firma_sutunlari(df, kod, faz):
    """Pazar+faz icin {firma: sutun} -- gercek firmalar ve turetilmisler ayri."""
    ek = PAZAR[kod][0]
    son = "_close" if faz == "kap" else ""
    gercek, turetilmis = {}, {}
    for c in df.columns:
        if not c.endswith(ek + son):
            continue
        if son == "" and c.endswith("_close"):
            continue
        firma = c[: len(c) - len(ek) - len(son) - 1]
        (turetilmis if firma.startswith(SENTETIK) else gercek)[firma] = c
    return gercek, turetilmis


def olcumler(df, kod, faz):
    """Mac basina capraz-firma olcumleri + firma bazli oranlar."""
    import pandas as pd
    gercek, turetilmis = firma_sutunlari(df, kod, faz)
    if not gercek:
        return None
    M = df[list(gercek.values())].astype(float)
    M.columns = list(gercek.keys())
    out = pd.DataFrame(index=df.index)
    out["n"] = M.notna().sum(axis=1)
    out["min"] = M.min(axis=1)
    out["max"] = M.max(axis=1)
    out["avg"] = M.mean(axis=1)
    out["med"] = M.median(axis=1)
    out["spread"] = out["max"] / out["min"]
    for f in gercek:
        out[f] = M[f]
    for f, c in turetilmis.items():
        out[f] = df[c].astype(float)
    return out


def ima_olasilik(df, kod, faz="kap"):
    """Komisyon arindirilmis piyasa olasiligi (market_avg sutunlarindan)."""
    import numpy as np, pandas as pd
    son = "_close" if faz == "kap" else ""
    kodlar = list(UCLU) if kod in UCLU else [kod, PAZAR[kod][2]]
    sut = {k: "market_avg_" + PAZAR[k][0] + son for k in kodlar}
    if not all(s in df.columns for s in sut.values()):
        return pd.Series(np.nan, index=df.index)
    ters = {k: 1.0 / df[s].astype(float) for k, s in sut.items()}
    toplam = sum(ters.values())
    p = ters[kod] / toplam
    if faz == "kap":
        # eski donem dosyalarinda kapanis ortalamasi yok -> acilis ortalamasina dus
        sut_ac = {k: "market_avg_" + PAZAR[k][0] for k in kodlar}
        if all(x in df.columns for x in sut_ac.values()):
            t2 = {k: 1.0 / df[x].astype(float) for k, x in sut_ac.items()}
            p = p.fillna(t2[kod] / sum(t2.values()))
    return p


def tuttu_mu(df, kod):
    fn = PAZAR[kod][1]
    return (df["fthg"].astype(int).values, df["ftag"].astype(int).values, fn)


def tut_serisi(df, kod):
    import pandas as pd
    h, a, fn = tuttu_mu(df, kod)
    return pd.Series([fn(x, y) for x, y in zip(h, a)], index=df.index)


# ---------------------------------------------------------------- kural motoru

def kural_coz(metin):
    """'u25 ac min 1,55-1,70'  ->  (kod, faz, secici, alt, ust)"""
    p = metin.replace(",", ".").split()
    if len(p) != 4:
        raise SystemExit('HATA kural bicimi: "<pazar> <ac|kap> <secici> <alt-ust>"'
                         '  ornek: "u25 ac min 1.55-1.70"')
    kod, faz, secici, aralik = p
    if kod not in PAZAR:
        raise SystemExit("HATA bilinmeyen pazar '%s'. Gecerli: %s" % (kod, ", ".join(PAZAR)))
    if faz not in ("ac", "kap"):
        raise SystemExit("HATA faz 'ac' veya 'kap' olmali, '%s' degil" % faz)
    if "-" not in aralik:
        raise SystemExit("HATA aralik 'alt-ust' olmali: '%s'" % aralik)
    alt, ust = aralik.split("-", 1)
    return kod, faz, secici, float(alt), float(ust)


def uygula(df, kurallar):
    import pandas as pd
    maske = pd.Series(True, index=df.index)
    onbellek, detay = {}, []
    for metin in kurallar:
        kod, faz, secici, alt, ust = kural_coz(metin)
        anahtar = (kod, faz)
        if anahtar not in onbellek:
            o = olcumler(df, kod, faz)
            if o is None:
                raise SystemExit("HATA '%s' pazari '%s' fazinda veride yok" % (kod, faz))
            onbellek[anahtar] = o
        o = onbellek[anahtar]
        if secici not in o.columns:
            firmalar = sorted(c for c in o.columns if c not in AGG)
            raise SystemExit("HATA secici '%s' yok.\n  Toplulastirmalar: %s\n  Firmalar: %s"
                             % (secici, ", ".join(AGG), ", ".join(firmalar)))
        maske &= o[secici].between(alt, ust)
        detay.append(metin)
    return maske, detay


# ---------------------------------------------------------------- olcum/rapor

def olc(tut, oranlar, ima):
    """Bir mac kumesi icin n/isabet/edge/basabas/ROI/z."""
    import numpy as np
    n = int(len(tut))
    if n == 0:
        return None
    h = int(tut.sum())
    isabet = h / n
    basabas = (1.0 / isabet) if isabet > 0 else float("inf")
    o = oranlar.values.astype(float)
    ok = ~np.isnan(o)
    roi = roi_t = float("nan")
    if ok.sum():
        kar = np.where(tut.values[ok], o[ok] - 1.0, -1.0)
        roi = kar.sum() / ok.sum() * 100.0
        # ROI'nin anlamliligi: getirilerin kendi dagilimindan (de-vig varsayimi yok).
        # Tek guvenilir kar olcusu bu; "edge" oransal de-vig yanliligi tasir.
        sd = float(np.std(kar, ddof=1)) if ok.sum() > 1 else float("nan")
        if sd and sd == sd:
            roi_t = (kar.mean()) / (sd / math.sqrt(ok.sum()))
    # edge/z YALNIZ piyasa imasinin bilindigi maclar uzerinde hesaplanir.
    # (Aksi halde isabet A kumesinden, ima B alt kumesinden gelir ve karsilastirma sahte olur.)
    ok_ima = ima.notna().values if len(ima) else np.zeros(n, bool)
    n_ima = int(ok_ima.sum())
    imo = edge = z = float("nan")
    if n_ima:
        imo = float(np.nanmean(ima.values[ok_ima]))
        isabet_ima = float(tut.values[ok_ima].mean())
        edge = isabet_ima - imo
        if 0 < imo < 1:
            se = math.sqrt(imo * (1 - imo) / n_ima)
            if se:
                z = edge / se
    return dict(n=n, h=h, isabet=isabet, imo=imo, edge=edge, n_ima=n_ima,
                basabas=basabas, roi=roi, roi_t=roi_t, z=z,
                ort_oran=float(np.nanmean(o)) if ok.sum() else float("nan"))


def _fmt(d, esik=None):
    if d is None:
        return None
    yildiz = ""
    if esik is not None and d["isabet"] * 100 >= esik:
        yildiz = " <<<" if (d["z"] == d["z"] and d["z"] >= 2) else " <"
    edge = ("%+6.1f" % (d["edge"] * 100)) if d["edge"] == d["edge"] else "     -"
    rt = d.get("roi_t", float("nan"))
    return ("%5d %5d  %5.1f%%  %6.2f  %7.2f  %s/%-5d %+7.1f%% %s  %s%s"
            % (d["n"], d["h"], d["isabet"] * 100, d["basabas"], d["ort_oran"],
               edge, d.get("n_ima", 0), d["roi"],
               ("%+5.2f" % rt) if rt == rt else "    -",
               "%+.1f" % d["z"] if d["z"] == d["z"] else "  -  ", yildiz))


BASLIK = ("    n  tutan  isabet  basbs  ort.oran   edge/nEdge    ROI  ROI_t      z")


def firma_kirilim(df, kod, faz, alt, ust, esik, ek_kurallar, goster):
    """Ayni pazar+bant icin HER FIRMA ayri: kac mac acmis, kaci gelmis."""
    o = olcumler(df, kod, faz)
    if o is None:
        raise SystemExit("HATA '%s' pazari '%s' fazinda yok" % (kod, faz))
    temel, _ = (uygula(df, ek_kurallar) if ek_kurallar else (None, None))
    tut_tum = tut_serisi(df, kod)
    ima_tum = ima_olasilik(df, kod, "kap")
    gercek, turetilmis = firma_sutunlari(df, kod, faz)

    print("\n" + "=" * 86)
    print("FIRMA KIRILIMI   pazar=%s  faz=%s  bant=%.2f-%.2f%s"
          % (kod, "acilis" if faz == "ac" else "kapanis", alt, ust,
             "   ek kural: " + " ; ".join(ek_kurallar) if ek_kurallar else ""))
    print('"bu firma bu orani kac maca acmis, kaci gercekten tuttu"')
    print("=" * 86)
    print("firma            " + BASLIK)
    print("-" * 86)
    satirlar = []
    for firma in sorted(gercek) + sorted(turetilmis):
        m = o[firma].between(alt, ust)
        if temel is not None:
            m = m & temel
        d = olc(tut_tum[m], o.loc[m, firma], ima_tum[m])
        if d is None or d["n"] == 0:
            continue
        satirlar.append((firma, d))
    for firma, d in sorted(satirlar, key=lambda x: -x[1]["isabet"]):
        etiket = firma + (" *" if firma.startswith(SENTETIK) else "")
        print("%-16s %s" % (etiket[:16], _fmt(d, esik)))
    print("-" * 86)
    print("* = turetilmis sutun (market_avg/market_max/betbrain), gercek firma degil")
    print("basbs = basabas oran (1/isabet): bu isabet yuzdesi ancak bu orandan YUKSEK fiyatta kar eder")
    print("edge  = isabet% - piyasanin ima ettigi olasilik (puan).  z>=2 = sanstan ayirt edilebilir")
    if esik is not None:
        print("<  = isabet >= %%%d ama istatistiksel kanit yok    <<< = isabet >= %%%d VE z>=2" % (esik, esik))
    print("-" * 86)
    print("NOT: ustteki satirlar ayni mac kumesi DEGIL - her firma farkli maclara bu orani acmis.")
    mutabakat(df, o, gercek, alt, ust, temel, tut_tum, ima_tum, esik, goster)


def mutabakat(df, o, gercek, alt, ust, temel, tut, ima, esik, goster=0):
    """ASIL TEST: ayni maca KAC firma birlikte bu bandi acmis, o maclar tutmus mu."""
    import numpy as np
    sayac = None
    for firma in gercek:
        m = o[firma].between(alt, ust).fillna(False).astype(int)
        sayac = m if sayac is None else (sayac + m)
    kac_kota = o[list(gercek)].notna().sum(axis=1)
    print("=" * 86)
    print("MUTABAKAT: ayni maca kac firma birlikte %.2f-%.2f bandini acmis" % (alt, ust))
    print("(asil soru bu - tek firmaya baglanmadan, firmalarin ortak gorusu)")
    print("-" * 86)
    print("onay      " + BASLIK)
    for k in range(1, len(gercek) + 1):
        m = (sayac >= k) & (kac_kota >= k)
        if temel is not None:
            m = m & temel
        d = olc(tut[m], o.loc[m, "max"], ima[m])
        if d is None or d["n"] == 0:
            continue
        print(">= %d firma %s" % (k, _fmt(d, esik)))
    print("-" * 86)
    print("fiyat = o macta firmalarin EN IYISI (max). Iddaa/Nesine bunun altinda fiyatlar.")
    print("Onay sayisi arttikca edge buyumuyorsa capraz-firma mutabakati bilgi tasimiyor.")
    if goster:
        tam = (sayac >= max(2, len(gercek) - 1)) & (kac_kota >= 2)
        if temel is not None:
            tam = tam & temel
        alt_df = df[tam]
        if len(alt_df):
            print("-" * 86)
            print("En cok firma onayli ilk %d mac:" % goster)
            for _, r in alt_df.head(goster).iterrows():
                i = r.name
                fiy = " ".join("%s=%.2f" % (f[:7], o.at[i, f]) for f in sorted(gercek)
                               if o.at[i, f] == o.at[i, f])
                print("  %s %-13s-%-13s %d-%d %-7s | %s" % (
                    str(r["date"])[:10], r["home_team"][:13], r["away_team"][:13],
                    r["fthg"], r["ftag"], "TUTTU" if tut[i] else "TUTMADI", fiy))


def kesif(df, pazarlar, fazlar, en_az_n, genislik, adim, en_az_onay, ust_sinir=4.0):
    """TUM oran bantlarini otomatik tarar; cok-test duzeltmesi uygular.

    1.75 gibi tek bir bant denemek yerine kayan pencereyle butun oran ekseni
    taranir. 800 hucre tarandiginda bazilarinin z>2 cikmasi KESINDIR, bu yuzden
    Bonferroni esigi ve beklenen yanlis pozitif sayisi birlikte basilir.
    """
    import numpy as np
    bulgular, test_sayisi = [], 0
    for kod in pazarlar:
        for faz in fazlar:
            o = olcumler(df, kod, faz)
            if o is None:
                continue
            gercek, _ = firma_sutunlari(df, kod, faz)
            if not gercek:
                continue
            tut = tut_serisi(df, kod)
            ima = ima_olasilik(df, kod, "kap")
            kota = o[list(gercek)].notna().sum(axis=1)
            alt = 1.20
            while alt < ust_sinir:
                ust = alt + genislik
                sayac = None
                for f in gercek:
                    m = o[f].between(alt, ust).fillna(False).astype(int)
                    sayac = m if sayac is None else sayac + m
                for onay in range(en_az_onay, len(gercek) + 1):
                    m = (sayac >= onay) & (kota >= onay)
                    if int(m.sum()) < en_az_n:
                        continue
                    d = olc(tut[m], o.loc[m, "max"], ima[m])
                    if d is None or d.get("roi_t") != d.get("roi_t"):
                        continue
                    test_sayisi += 1
                    bulgular.append((kod, faz, alt, ust, onay, d))
                alt = round(alt + adim, 4)

    print("\n" + "=" * 96)
    print("KESIF TARAMASI  pazarlar=%s  fazlar=%s" % (",".join(pazarlar), ",".join(fazlar)))
    print("kayan pencere: genislik %.2f, adim %.2f, 1.20-%.2f | en az %d mac, en az %d firma onayi"
          % (genislik, adim, ust_sinir, en_az_n, en_az_onay))
    print("=" * 96)
    if not test_sayisi:
        print("Hic test yapilamadi (veri/esik kombinasyonu bos).")
        return
    # Bonferroni: tek yonlu alfa=0.05 icin esik z
    try:
        from statistics import NormalDist
        z_esik = NormalDist().inv_cdf(1 - 0.05 / test_sayisi)
    except Exception:
        z_esik = math.sqrt(2 * math.log(test_sayisi)) if test_sayisi > 1 else 1.64
    print("TOPLAM TEST: %d   ->  Bonferroni esigi = %.2f  (duzeltilmemis t>=2 ile beklenen"
          " yanlis pozitif: ~%.0f)" % (test_sayisi, z_esik, test_sayisi * 0.0228))
    print("SIRALAMA VE KARAR 'ROI_t' UZERINDEN: ROI'nin kendi getiri dagilimindan hesaplanan")
    print("t degeri. 'edge/z' kolonlari oransal de-vig yanliligi tasir (favorilerde sistematik")
    print("+4-5 puan sahte edge uretir) - karar icin KULLANILMAZ, bilgi olarak basilir.")
    print("-" * 96)
    bulgular.sort(key=lambda b: -b[5]["roi_t"])
    print("pazar faz   bant          onay %s" % BASLIK)
    for kod, faz, alt, ust, onay, d in bulgular[:15]:
        gecti = "GECTI" if d["roi_t"] >= z_esik else ""
        print("%-5s %-5s %4.2f-%4.2f  >=%d  %s %s"
              % (kod, faz, alt, ust, onay, _fmt(d), gecti))
    print("-" * 96)
    gecen = [b for b in bulgular if b[5]["roi_t"] >= z_esik]
    if gecen:
        print("Bonferroni esigini GECEN bant sayisi: %d" % len(gecen))
        for kod, faz, alt, ust, onay, d in gecen:
            print("   %s %s %.2f-%.2f >=%d firma: isabet %%%.1f, ROI %+.1f%%, ROI_t %+.2f, n %d"
                  % (kod, faz, alt, ust, onay, d["isabet"] * 100,
                     d["roi"], d["roi_t"], d["n"]))
    else:
        print("Bonferroni esigini (ROI_t) GECEN bant YOK -> bu veride capraz-firma oran bandi")
        print("istatistiksel olarak dogrulanmiyor. Ustteki en iyi z'ler cok-test gurultusu")
        print("icinde beklenen buyuklukte.")
    print("-" * 96)
    print("NOT: ROI en iyi Avrupa fiyatiyla. Iddaa/Nesine fiyati daha dusuk oldugundan")
    print("     burada +ROI gorunen bir bant bile Nesine'de eksiye dusebilir.")


def izgara(df, kod, faz, esik, en_az_n):
    """Her firma x her oran bandi: isabet yuzdesi izgarasi, esigi gecenler isaretli."""
    o = olcumler(df, kod, faz)
    if o is None:
        raise SystemExit("HATA '%s' pazari '%s' fazinda yok" % (kod, faz))
    tut = tut_serisi(df, kod)
    ima = ima_olasilik(df, kod, "kap")
    gercek, _ = firma_sutunlari(df, kod, faz)
    print("\n" + "=" * 100)
    print("IZGARA  pazar=%s  faz=%s   hucre: n / isabet%% / edge puan   (n>=%d)"
          % (kod, "acilis" if faz == "ac" else "kapanis", en_az_n))
    print("esik %%%d ve uzeri: *  |  ayrica z>=2 ise: **" % esik)
    print("=" * 100)
    print("%-14s" % "firma" + "".join("%-17s" % ("%.2f-%.2f" % b) for b in BANTLAR))
    print("-" * 100)
    for firma in sorted(gercek):
        satir = "%-14s" % firma[:14]
        for alt, ust in BANTLAR:
            m = o[firma].between(alt, ust)
            d = olc(tut[m], o.loc[m, firma], ima[m])
            if d is None or d["n"] < en_az_n:
                satir += "%-17s" % ("n=%d" % (0 if d is None else d["n"]))
                continue
            isaret = ""
            if d["isabet"] * 100 >= esik:
                isaret = "**" if (d["z"] == d["z"] and d["z"] >= 2) else "*"
            e = ("%+.0f" % (d["edge"] * 100)) if d["edge"] == d["edge"] else "?"
            satir += "%-17s" % ("%d/%.0f%%/%s%s" % (d["n"], d["isabet"] * 100, e, isaret))
        print(satir)
    print("-" * 100)
    print("Bir hucrede isabet yuksek ama edge ~0 ise taktik yok: bant zaten o olasiligi fiyatlamis.")
    print("edge '?' = o maclarda piyasa imasi (market_avg kapanis) yok, karsilastirma yapilamaz.")


def rapor(df, maske, kod, detay, ad, goster):
    o_kap = olcumler(df, kod, "kap") or olcumler(df, kod, "ac")
    tut = tut_serisi(df, kod)[maske]
    ima = ima_olasilik(df, kod, "kap")[maske]
    print("\n" + "=" * 86)
    print("TAKTIK: " + (ad or "(adsiz)"))
    for k in detay:
        print("  kural: " + k)
    print("  hedef pazar: %s | havuz: %d mac (Premier Lig 1993-94 -> 2025-26)" % (kod, len(df)))
    print("-" * 86)
    if len(tut) == 0:
        print("UYAN MAC: 0 -> bu kural bandi veride hic maca denk gelmiyor.")
        return
    print("fiyat kaynagi    " + BASLIK)
    for etiket, sut in (("en iyi (max)", "max"), ("konsensus (avg)", "avg"), ("en kotu (min)", "min")):
        d = olc(tut, o_kap.loc[maske, sut], ima)
        print("%-16s %s" % (etiket, _fmt(d)))
    print("-" * 86)
    d = olc(tut, o_kap.loc[maske, "max"], ima)
    print("Iddaa/Nesine fiyati 'en kotu (min)' satirinin ALTINDADIR - karar o satira gore verilir.")
    if d["n"] < 100:
        print("UYARI: n=%d kucuk; |z|<2 ise sonuc sanstan ayirt edilemez." % d["n"])
    if d["z"] == d["z"]:
        if d["z"] >= 2:
            print("KARAR: isabet piyasa imasinin anlamli UZERINDE (z=%.2f)." % d["z"])
        elif d["z"] <= -2:
            print("KARAR: isabet piyasa imasinin anlamli ALTINDA (z=%.2f) - ters yon dusunulebilir." % d["z"])
        else:
            print("KARAR: istatistiksel kanit YOK (|z|=%.2f < 2)." % abs(d["z"]))
    if goster:
        print("-" * 86)
        o_ac = olcumler(df, kod, "ac")
        for _, r in df[maske].head(goster).iterrows():
            i = r.name
            print("  %s %-13s-%-13s %d-%d | ac %.2f-%.2f | kap %.2f-%.2f | %s"
                  % (str(r["date"])[:10], r["home_team"][:13], r["away_team"][:13],
                     r["fthg"], r["ftag"],
                     o_ac.at[i, "min"], o_ac.at[i, "max"],
                     o_kap.at[i, "min"], o_kap.at[i, "max"],
                     "TUTTU" if tut[i] else "TUTMADI"))


# ---------------------------------------------------------------- cli

def main(argv=None):
    ham = argv if argv is not None else sys.argv[1:]
    if "--bugun" in ham:
        import capraz_bugun
        capraz_bugun.main([x for x in ham if x != "--bugun"])
        return
    ap = argparse.ArgumentParser(
        prog="bwm.py capraz",
        description="Capraz-firma oran taktigi backtest: 12.705 mac, acilis+kapanis, 20 firma")
    ap.add_argument("--kir", metavar="PAZAR",
                    help="FIRMA KIRILIMI: her firma bu orani kac maca acmis, kaci tutmus")
    ap.add_argument("--bant", default="1.55-1.95", help="--kir icin oran bandi (vars. 1.55-1.95)")
    ap.add_argument("--izgara", metavar="PAZAR", help="her firma x her bant isabet izgarasi")
    ap.add_argument("--bugun", action="store_true",
                    help="BUGUNUN Nesine/iddaa maclarini gecmis oran bantlariyla eslestir "
                         "(ek secenekler: --gun --pencere --onay --min-n --min-isabet --ev --n)")
    ap.add_argument("--kesif", action="store_true",
                    help="TUM oran bantlarini kayan pencereyle otomatik tara (cok-test duzeltmeli)")
    ap.add_argument("--pazarlar", default="u25,a25,ms1,msx,ms2", help="--kesif icin pazar listesi")
    ap.add_argument("--genislik", type=float, default=0.15, help="--kesif pencere genisligi (vars. 0.15)")
    ap.add_argument("--adim", type=float, default=0.05, help="--kesif pencere adimi (vars. 0.05)")
    ap.add_argument("--onay", type=int, default=2, help="--kesif en az kac firma onayi (vars. 2)")
    ap.add_argument("--faz", default="ac", choices=["ac", "kap"], help="acilis / kapanis (vars. ac)")
    ap.add_argument("--esik", type=float, default=80, help="isaretlenecek isabet yuzdesi (vars. 80)")
    ap.add_argument("--min-n", type=int, default=30, help="izgarada gosterilecek en az mac (vars. 30)")
    ap.add_argument("--kural", action="append", default=[],
                    help='"<pazar> <ac|kap> <secici> <alt-ust>" orn: "u25 ac min 1.55-1.70"')
    ap.add_argument("--hedef", help="oynanan pazar (vars. ilk kuralin pazari)")
    ap.add_argument("--ad", default="", help="taktik adi")
    ap.add_argument("--firmalar", metavar="PAZAR", help="bu pazarda veride olan firmalari listele")
    ap.add_argument("--goster", type=int, default=0, help="ilk N maci listele")
    ap.add_argument("--kaynak", default="cok", choices=["cok", "epl"],
                    help="cok = 161.039 mac / 11 ulke (Turkiye dahil, 1993-2021) [varsayilan]; "
                         "epl = 12.705 mac Premier Lig (acilis+kapanis daha zengin)")
    ap.add_argument("--lig", help="yalniz bu ulke/lig (orn. turkey, england, 'turkey/T1')")
    ap.add_argument("--yil", help="tarih araligi filtresi, orn. 1993-2009 veya 2010-2021 "
                                  "(ornekklem-disi dogrulama icin)")
    ap.add_argument("--yenile", action="store_true", help="veriyi yeniden indir")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])

    for kod in (a.kir, a.izgara, a.firmalar, a.hedef):
        if kod and kod not in PAZAR:
            raise SystemExit("HATA bilinmeyen pazar '%s'. Gecerli: %s" % (kod, ", ".join(PAZAR)))

    if a.kaynak == "cok":
        import capraz_veri
        df = capraz_veri.yukle(a.yenile)
    else:
        df = yukle(a.yenile)
    if a.lig:
        if "lig" not in df.columns:
            raise SystemExit("HATA --lig yalniz --kaynak cok ile kullanilir")
        m = df["lig"].str.startswith(a.lig)
        if not int(m.sum()):
            raise SystemExit("HATA '%s' ile baslayan lig yok. Ornekler: %s"
                             % (a.lig, ", ".join(sorted(df["lig"].unique())[:8])))
        df = df[m].reset_index(drop=True)
        print("lig filtresi '%s' -> %d mac" % (a.lig, len(df)))

    if a.yil:
        y1, y2 = [int(x) for x in a.yil.split("-", 1)]
        m = df["date"].str[:4].astype(int).between(y1, y2)
        df = df[m].reset_index(drop=True)
        print("yil filtresi %d-%d -> %d mac" % (y1, y2, len(df)))

    if a.firmalar:
        for faz in ("ac", "kap"):
            g, t = firma_sutunlari(df, a.firmalar, faz)
            o = olcumler(df, a.firmalar, faz)
            print("\n%s / %s -> %d gercek firma" % (a.firmalar, "acilis" if faz == "ac" else "kapanis", len(g)))
            for f in sorted(g):
                print("   %-14s %5d mac" % (f, int(o[f].notna().sum())))
            if t:
                print("   turetilmis: " + ", ".join(sorted(t)))
        return
    if a.kir:
        alt, ust = [float(x) for x in a.bant.replace(",", ".").split("-", 1)]
        firma_kirilim(df, a.kir, a.faz, alt, ust, a.esik, a.kural, a.goster)
        return
    if a.kesif:
        pz = [x.strip() for x in a.pazarlar.split(",") if x.strip()]
        for k in pz:
            if k not in PAZAR:
                raise SystemExit("HATA bilinmeyen pazar '%s'" % k)
        kesif(df, pz, ["ac", "kap"], a.min_n, a.genislik, a.adim, a.onay)
        return
    if a.izgara:
        izgara(df, a.izgara, a.faz, a.esik, a.min_n)
        return
    if a.kural:
        kod = a.hedef or kural_coz(a.kural[0])[0]
        maske, detay = uygula(df, a.kural)
        rapor(df, maske, kod, detay, a.ad, a.goster)
        return

    ap.print_help()
    print("\nPazarlar: " + ", ".join(PAZAR))
    print("Seciciler: " + ", ".join(AGG) + " (spread=max/min) veya firma adi (bkz. --firmalar u25)")


if __name__ == "__main__":
    main()
