"""
Nesine gecmis kapanis oranini API olarak sunmuyor; bu yuzden izlenen maclarin kickoff'una
5 dakika kala kendimiz tekrar cekip 'kapanis_orani' olarak kaydediyoruz.

Kullanim: python kapanis_bekle.py data/iz/iz_20261003.json [--pencere-dk 5]
--iz-kaydet ile olusturulmus dosyayi okur, her kaydin kickoff'una (esd) 5 dk kala
taze bir Nesine cekimi yapar, o maca ait mk (market) listesini kaydin icine
"kapanis_orani": {"cekildi": ts, "mk": [...]} olarak yazar. Zaten yakalanmis veya
kickoff'u gecmis (yakalanamadan) kayitlar atlanir. Tum kayitlar islenince biter.
Arka planda (run_in_background) calistirilmasi icin tasarlandi; PC uyursa/kapanirsa
surec de durur - bu bilinen bir sinir, otomatik kurtarma yok.
"""
import sys
import os
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nesine as N


def _mk_bul(veri, esd_ms, ev, dep):
    for e in veri["olaylar"]:
        if e["esd_ms"] == esd_ms and e["hn"] == ev and e["an"] == dep:
            return e["mk"]
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dosya")
    p.add_argument("--pencere-dk", type=int, default=5)
    a = p.parse_args()
    pencere = a.pencere_dk * 60

    while True:
        kayitlar = json.load(open(a.dosya, encoding="utf-8"))
        now = time.time()
        bekleyen = [r for r in kayitlar if "kapanis_orani" not in r and r["esd"] > now]
        if not bekleyen:
            print(f"[{time.strftime('%H:%M:%S')}] bitti: yakalanmayı bekleyen kayıt yok")
            return
        hazir = [r for r in bekleyen if r["esd"] - pencere <= now]
        if hazir:
            veri, _ = N.bulten(True)
            N._arsive_yaz(veri)
            for r in hazir:
                mk = _mk_bul(veri, r["esd_ms"], r["ev"], r["dep"])
                r["kapanis_orani"] = {"cekildi": now, "mk": mk, "not": "pazar yok/eslesme yok" if mk is None else None}
                print(f"[{time.strftime('%H:%M:%S')}] KAPANIŞ YAKALANDI: {r['ev']} - {r['dep']} ({r['taktik']}) | kickoff'a {round((r['esd']-now)/60)} dk kala")
            json.dump(kayitlar, open(a.dosya, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            continue
        hedef = min(r["esd"] - pencere for r in bekleyen)
        uyku = max(5, min(120, hedef - now))
        print(f"[{time.strftime('%H:%M:%S')}] {len(bekleyen)} maç bekleniyor, sıradaki yakalama ~{round((hedef-now)/60)} dk sonra, {round(uyku)}s uyku")
        time.sleep(uyku)


if __name__ == "__main__":
    main()
