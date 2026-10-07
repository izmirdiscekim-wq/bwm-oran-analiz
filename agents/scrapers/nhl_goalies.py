# -*- coding: utf-8 -*-
"""
NHL_GOALIES - DailyFaceoff'tan ONAYLI (Confirmed) kaleci durumunu ceken hafif scraper.

LOKAL-ONLY, OPSIYONEL: Render dongusune/zamanlayiciya BAGLI DEGIL, multi_sport_analyzer
tarafindan import EDILMEZ (bs4 Render'in hafif mimarisine dahil degil). Elle calistirilir:

    cd agents; python scrapers/nhl_goalies.py            # bulunanlari yazdirir
    cd agents; python scrapers/nhl_goalies.py --yaz       # 'Confirmed' olanlari kaleci.json'a yazar

Neden BeautifulSoup + __NEXT_DATA__: DailyFaceoff bir Next.js sayfasi, kaleci verisi CSS
sinifinda degil <script id="__NEXT_DATA__"> icindeki JSON'da (props.pageProps.data[i]). Bu,
CSS class'larina (sayfa guncellenince kirilir) gore daha az kirilgan bir yontem. bs4 burada
yalniz o tek <script> etiketini bulmak icin kullaniliyor (kullanicinin istedigi gibi).

DURUSTLUK: Sadece status TAM OLARAK "Confirmed" olan kaleciler 'onayli' sayilir. "Likely" /
None / baska bir deger ONAYLI SAYILMAZ (uydurma yok) - bu mac/kaleci basitce atlanir, model
'belirsiz' davranisina (katsayi uygulanmaz, risk yuksek) devam eder.

Siteye ulasilamazsa (ag hatasi, 403, HTML yapisi degismis) KESINLIKLE COKMEZ: ({}, uyari_metni)
doner, guven skorunu dusurmeden yalniz 'kaleci verisi alinamadi' bilgisini tasir.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

URL = "https://www.dailyfaceoff.com/starting-goalies/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def confirmed_goalies():
    """-> (sonuc, uyari). sonuc: {takim_adi: {'kaleci','durum','sv','gaa'}} (yalniz status =
    'Confirmed' olanlar). uyari: None (basarili) ya da kisa aciklama metni. ASLA raise etmez."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as ex:
        return {}, "scraper bagimliligi yok (%s) - kaleci verisi alinamadi" % type(ex).__name__
    try:
        r = requests.get(URL, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return {}, "DailyFaceoff HTTP %d - kaleci verisi alinamadi" % r.status_code
        soup = BeautifulSoup(r.text, "html.parser")
        etiket = soup.find("script", id="__NEXT_DATA__")
        if not etiket or not etiket.string:
            return {}, "DailyFaceoff sayfa yapisi degisti (__NEXT_DATA__ yok) - kaleci verisi alinamadi"
        veri = json.loads(etiket.string)
        maclar = (((veri.get("props") or {}).get("pageProps") or {}).get("data")) or []
    except Exception as ex:
        return {}, "DailyFaceoff erisilemedi (%s) - kaleci verisi alinamadi" % type(ex).__name__
    out = {}
    for m in maclar:
        for taraf in ("home", "away"):
            durum = m.get("%sNewsStrengthName" % taraf)
            ad_takim = m.get("%sTeamName" % taraf)
            ad_kaleci = m.get("%sGoalieName" % taraf)
            if durum != "Confirmed" or not ad_takim or not ad_kaleci:
                continue
            sv = m.get("%sGoalieSavePercentage" % taraf)
            try:
                sv = float(sv) if sv is not None else None
            except (TypeError, ValueError):
                sv = None
            out[ad_takim] = {"kaleci": ad_kaleci, "durum": "onayli", "sv": sv,
                             "gaa": m.get("%sGoalieGoalsAgainstAvg" % taraf)}
    return out, None


def uygula(sonuc):
    """confirmed_goalies() ciktisini msa_kaynak.kaleci_yaz ile kaleci.json'a isler. -> yazilan takim sayisi."""
    import msa_kaynak as K
    n = 0
    for takim, bilgi in sonuc.items():
        K.kaleci_yaz(takim, "onayli", bilgi.get("sv"))
        n += 1
    return n


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sonuc, uyari = confirmed_goalies()
    if uyari:
        print(uyari)
        return
    if not sonuc:
        print("Confirmed kaleci bulunamadi (bugun erken/mac yok olabilir).")
        return
    for takim, bilgi in sonuc.items():
        print("%-28s %-22s sv=%s gaa=%s" % (takim, bilgi["kaleci"], bilgi.get("sv"), bilgi.get("gaa")))
    if "--yaz" in argv:
        n = uygula(sonuc)
        print("\n%d takim kaleci.json'a yazildi (durum=onayli)." % n)


if __name__ == "__main__":
    main()
