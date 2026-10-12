# -*- coding: utf-8 -*-
"""
MASTER_ORCHESTRATOR - silo modulleri tek potada eriten Ensemble (coklu faktor) ust katmani.

MIMARI: mevcut motorlar DEGISTIRILMEZ. Bu katman onlarin uzerinde durur, her (mac, pazar,
secenek) satirina 0-100 arasi "Master Skor" verir ve kalite siniflarina (TIER) ayirir.
LLM cagrisi YOK - tum puanlama saf Python/heuristik.

  BWM skoru     (maks 40) : model olasiligi + guven + orneklem + risk + ELO/SoS varligi.
                            Model olasiligi YOKSA zayif bant (0-10): yalniz piyasa saglamligi.
  Piyasa/Balina (maks 30) : ilk gorulen orandan bu yana kirilim. |%10| -> 15, |%20| -> 30 puan.
                            Kirilim yoksa 0 puan, SISTEM DURMAZ.
  Deger/EV      (maks 30) : Shin devigging sonrasi harman EV. EV <= 0 -> 0 puan, +%5 -> 30 puan.

  TIER: S >= 85 (kusursuz firtina) | A 70-84 (saf matematik) | B 60-69 (akilli para) | alti: radar disi.

KESINTISIZ CALISMA (graceful degradation): her saglayici ve her puanlayici kendi try/except'i
icinde; eksik veri (ELO yok, acilis orani NULL, model kurulamadi) o bilesene 0 puan yazar ve
"eksik" listesine not duser - script COKMEZ, diger bilesenlerle analiz surer.

OLCULMUS GERCEKLER (bu puanlamanin neden boyle oldugunun gerekcesi, 08.10.2026):
  - 409 yerlesmis kararin TAMAMI kayit aninda EV <= 0 (ort. -%11); ROI -%17 ~ olculen marj
    (iddaa %13-17). Bu yuzden EV bileseni COGU ZAMAN 0 puan verir ve S-TIER nadir olmalidir.
    Esikler sinyal uretmek icin GEVSETILMEZ (CLAUDE.md).
  - Arsivden kurulan futbol ELO'su piyasadan %12 KOTU (Brier 0.6509 vs 0.5811; 4001 takim,
    takim basina ortanca 1 mac). Bu yuzden futbol BWM skoru ELO'ya DAYANDIRILMADI; futbol
    model olasiligi iddaa recent-matches (yol "soccer", takim basina 10 mac) Poisson'undan gelir
    ve KALIBRE DEGILDIR (FB_* katsayilari varsayim).
  - Zayif modeli piyasaya harmanlamak Brier'i kotulestiriyordu (0.5880 vs 0.5811) -> model
    piyasadan %30'dan fazla ayrisirsa aday sayilmaz (SAPMA_TAVANI).

    python master_orchestrator.py tara --kaynak hepsi --saat 24
    python master_orchestrator.py tara --kaynak futbol --tier B --telegram
    python master_orchestrator.py durum
"""
import argparse
import io
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devig
import fetch_iddaa as F
import msa_kaynak as K
import multi_sport_analyzer as M
import pynum

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TR = K.TR
GONDERILEN = os.path.join(K.VERI, "orkestra_gonderilen.json")

# futbol Poisson katsayilari - VARSAYIM, kalibre edilmedi
FB_HA = 0.10          # ev sahibi gol carpani (+%10 / -%10): literatur ~0.2-0.25 gol avantaji
FB_IZGARA = 10        # Poisson izgara boyu (futbolda 10 gol yeterli)
FB_MIN_N = 5          # takim basina asgari son-mac
FB_YOL = "soccer"     # iddaa statisticsv2 futbol yolu (OLCULDU: "football" bos doner)


class MasterOrchestrator:
    """Coklu faktor puanlama + TIER siniflandirma. Saf Python, LLM yok."""

    AGIRLIK = {"bwm": 40.0, "piyasa": 30.0, "ev": 30.0}
    TIER_ESIK = (("S", 85.0), ("A", 70.0), ("B", 60.0))
    KIRILIM_YARIM, KIRILIM_TAM = 0.10, 0.20     # |%10| -> yarim puan, |%20| -> tam puan
    EV_TAM = 0.05                                # EV +%5 -> tam puan
    MIN_ORAN = 1.30                              # proje kurali (CLAUDE.md)
    SAPMA_TAVANI = 0.30                          # model piyasadan bu kadardan fazla ayrisirsa aday degil
    W_MKT = 0.70                                 # harman: 0.70 piyasa + 0.30 model

    TIER_AD = {"S": "Kusursuz Fırtına", "A": "Saf Matematik", "B": "Akıllı Para"}
    TIER_SIMGE = {"S": "🥇", "A": "🥈", "B": "🥉"}

    def __init__(self, min_oran=None):
        if min_oran is not None:
            self.MIN_ORAN = min_oran

    # ------------------------------------------------------------------ puanlayicilar
    def puan_bwm(self, s):
        """-> (puan <= 40, detay). Model olasiligi yoksa ZAYIF BANT (0-10)."""
        try:
            tavan = self.AGIRLIK["bwm"]
            if s.get("p_model") is None:
                # zayif bant: yalniz piyasa saglamligi olculebilir
                p = 0.0
                notlar = []
                if s.get("marj") is not None and s["marj"] <= 0.12:
                    p += 5.0
                    notlar.append("ucuz pazar")
                elif s.get("marj") is not None and s["marj"] <= 0.18:
                    p += 2.5
                    notlar.append("orta marj")
                if (s.get("oran") or 0) >= self.MIN_ORAN:
                    p += 3.0
                    notlar.append("oran ≥ %.2f" % self.MIN_ORAN)
                if s.get("p_piyasa") and 0.35 <= s["p_piyasa"] <= 0.80:
                    p += 2.0
                    notlar.append("dengeli bant")
                return min(p, 10.0), "BWM zayıf (model yok): " + (", ".join(notlar) or "veri yok")
            p, notlar = 0.0, []
            g = s.get("guven")
            if g:
                p += min(max(g, 0), 10) * 2.0                      # guven 1-10 -> 0-20
                notlar.append("güven %s/10" % g)
            n = s.get("n")
            nmin = min(n) if isinstance(n, (list, tuple)) and n else (n or 0)
            if nmin >= 8:
                p += 8.0
            elif nmin >= 5:
                p += 4.0
            notlar.append("örneklem %s" % nmin)
            p += {"Düşük": 8.0, "Orta": 4.0, "Yüksek": 0.0}.get(s.get("risk"), 0.0)
            notlar.append("risk %s" % (s.get("risk") or "?"))
            if (s.get("f") or {}).get("elo_sos") and any(x is not None for x in s["f"]["elo_sos"]):
                p += 4.0
                notlar.append("ELO/SoS var")
            return min(p, tavan), "BWM: " + ", ".join(notlar)
        except Exception as ex:
            return 0.0, "BWM hesaplanamadı (%s)" % type(ex).__name__

    def puan_piyasa(self, s):
        """-> (puan <= 30, detay). Kirilim = oran / ilk_oran - 1.

        YON: bizim secenegimizin orani DUSTUYSE o secenege para geldi (balina destegi) -> puan.
        Oran YUKSELDIYSE piyasa aleyhimize hareket etmistir -> 0 puan (aksiyon.py ile ayni mantik:
        oran yukseldiyse onerilen yon TERS taraftir)."""
        try:
            ilk, oran = s.get("ilk_oran"), s.get("oran")
            if not ilk or not oran:
                return 0.0, "kırılım yok (açılış oranı kaydı yok)"
            delta = oran / ilk - 1.0
            if delta >= 0:
                return 0.0, "kırılım yok/aleyhte (%.2f → %.2f, %+.1f%%)" % (ilk, oran, delta * 100)
            d = abs(delta)
            tam, yarim = self.KIRILIM_TAM, self.KIRILIM_YARIM
            if d >= tam:
                p = self.AGIRLIK["piyasa"]
            elif d >= yarim:
                p = self.AGIRLIK["piyasa"] * (0.5 + 0.5 * (d - yarim) / (tam - yarim))
            else:
                p = self.AGIRLIK["piyasa"] * 0.5 * (d / yarim)
            return min(p, self.AGIRLIK["piyasa"]), "balina kırılımı %.2f → %.2f (%+.1f%%)" % (ilk, oran, delta * 100)
        except Exception as ex:
            return 0.0, "kırılım hesaplanamadı (%s)" % type(ex).__name__

    def puan_ev(self, s):
        """-> (puan <= 30, detay). Harman (Shin piyasa + model) EV. EV <= 0 -> 0 puan."""
        try:
            ev = s.get("ev_yuzde")
            if ev is None:
                return 0.0, "EV yok (model olasılığı yok)"
            if ev <= 0:
                return 0.0, "EV %+.1f%% (≤ 0 → puan yok; marjın bedeli)" % ev
            p = self.AGIRLIK["ev"] * min(ev / 100.0, self.EV_TAM) / self.EV_TAM
            return min(p, self.AGIRLIK["ev"]), "EV %+.1f%%" % ev
        except Exception as ex:
            return 0.0, "EV hesaplanamadı (%s)" % type(ex).__name__

    def tier(self, skor):
        for ad, esik in self.TIER_ESIK:
            if skor >= esik:
                return ad
        return None

    def master_skor(self, s):
        """Tek satir -> {skor, tier, bilesen, detay, eksik}. ASLA raise etmez."""
        bwm, d1 = self.puan_bwm(s)
        piy, d2 = self.puan_piyasa(s)
        ev, d3 = self.puan_ev(s)
        skor = round(bwm + piy + ev, 1)
        eksik = list(s.get("eksik") or [])
        if s.get("p_model") is None:
            eksik.append("model-olasılığı")
        if not s.get("ilk_oran"):
            eksik.append("açılış-oranı")
        return dict(s, skor=skor, tier=self.tier(skor),
                    bilesen={"bwm": round(bwm, 1), "piyasa": round(piy, 1), "ev": round(ev, 1)},
                    detay=[d1, d2, d3], eksik=sorted(set(eksik)))

    def siralat(self, satirlar):
        return sorted((self.master_skor(s) for s in satirlar), key=lambda r: -r["skor"])

    # ------------------------------------------------------------------ cikti
    def mesaj(self, r):
        """Telegram sablonu (TIER basligi + tek satir detay)."""
        tier = r.get("tier") or "-"
        bil = r["bilesen"]
        parcalar = []
        if bil["bwm"] >= 20:
            parcalar.append("BWM Onaylı")
        elif bil["bwm"] > 0:
            parcalar.append("BWM zayıf")
        else:
            parcalar.append("BWM yok")
        if bil["piyasa"] > 0:
            d = (r["oran"] / r["ilk_oran"] - 1.0) * 100 if r.get("ilk_oran") else 0.0
            parcalar.append("%%%.0f balina kırılımı" % abs(d))
        else:
            parcalar.append("balina hareketi yok")
        parcalar.append("EV: %+.1f%%" % (r["ev_yuzde"] if r.get("ev_yuzde") is not None else 0.0))
        saat = datetime.fromtimestamp(r["ko"], TR).strftime("%d.%m %H:%M") if r.get("ko") else "--"
        baslik = ("%s <b>[%s-TIER]</b> %s" % (self.TIER_SIMGE.get(tier), tier, self.TIER_AD[tier])
                  if tier in self.TIER_AD else "• <b>[RADAR]</b> tier eşiği altı")
        sat = [baslik,
               "Maç: %s - %s  ⏰ %s TSİ" % (r.get("ev"), r.get("dep"), saat),
               "Seçim: %s @%.2f (Master Skor: %.0f/100)" % (r.get("pazar_etiket") or r.get("pazar"), r["oran"], r["skor"]),
               "Detay: %s" % " · ".join(parcalar)]
        if r.get("eksik"):
            sat.append("Eksik veri: %s (o bileşen 0 puan)" % ", ".join(r["eksik"]))
        return "\n".join(sat)

    # ------------------------------------------------------------------ Telegram
    def _gonderilen_oku(self):
        try:
            with io.open(GONDERILEN, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def telegram_gonder(self, sonuclar, min_tier="B", gonder_fn=None):
        """TIER esigini gecen satirlari Telegram'a yollar. Ayni (mac, pazar, secim, tier)
        bir kez gonderilir (mukerrer kilidi). -> gonderilen sayisi."""
        sira = {"S": 3, "A": 2, "B": 1}
        esik = sira.get(min_tier, 1)
        aday = [r for r in sonuclar if r.get("tier") and sira.get(r["tier"], 0) >= esik]
        if not aday:
            return 0
        kilit = self._gonderilen_oku()
        simdi = time.time()
        kilit = {k: v for k, v in kilit.items() if simdi - v < 3 * 86400}
        if gonder_fn is None:
            def gonder_fn(metin):
                sys.path.insert(0, r"C:\Users\Tuffy\.claude\agents")
                import telegram_report as TR_
                return TR_.send_telegram([metin])[0]
        n = 0
        for r in aday:
            anah = "%s|%s|%s|%s" % (r.get("esd_ms"), r.get("pazar"), r.get("sec"), r.get("tier"))
            if anah in kilit:
                continue
            try:
                if gonder_fn(self.mesaj(r)):
                    kilit[anah] = simdi
                    n += 1
            except Exception as ex:
                print("Telegram gönderilemedi: %s" % type(ex).__name__)
        try:
            os.makedirs(os.path.dirname(GONDERILEN), exist_ok=True)
            with io.open(GONDERILEN, "w", encoding="utf-8") as f:
                json.dump(kilit, f)
        except OSError:
            pass
        return n


# ---------------------------------------------------------------------- futbol modeli
def futbol_model(gh, ga):
    """iddaa recent-matches (yol 'soccer') son-10 maclarindan Poisson gol modeli.
    -> {"kind":"poisson","lam","mu","n","f","eksik"} | None. KALIBRE DEGIL (FB_HA varsayim)."""
    if min(len(gh), len(ga)) < FB_MIN_N:
        return None
    havuz = sum(g["pf"] + g["pa"] for g in gh + ga) / (2.0 * len(gh + ga))
    eh = (M.shrunk([g["pf"] for g in gh], havuz) + M.shrunk([g["pa"] for g in ga], havuz)) / 2.0
    ea = (M.shrunk([g["pf"] for g in ga], havuz) + M.shrunk([g["pa"] for g in gh], havuz)) / 2.0
    return {"kind": "poisson", "lam": max(eh * (1 + FB_HA), 0.15), "mu": max(ea * (1 - FB_HA), 0.15),
            "n": (len(gh), len(ga)), "f": {"havuz_gol": round(havuz, 2)}, "eksik": []}


def _fiyatla_futbol(mod, pazar_listesi, home, away, min_oran, sapma_tavani, w_mkt):
    """Futbol icin satir uretimi: siniflandir + Shin devig + Poisson olasilik + ana hat.
    multi_sport_analyzer'in fonksiyonlari YENIDEN YAZILMADAN kullanilir, M DEGISTIRILMEZ."""
    satirlar = []
    for ad, sov, outs in pazar_listesi:
        sn = M.siniflandir(ad, sov)
        if not sn or len(outs) < 2 or any(v <= 1.01 for v in outs.values()):
            continue
        tur, taraf, cizgi, uz = sn
        marj = sum(1.0 / v for v in outs.values()) - 1.0
        if not (0.0 <= marj <= 0.35):
            continue
        pm = devig.shin_probs(outs)
        pmod = M.OLAS["poisson"](mod, tur, taraf, cizgi, uz, set(outs))
        if not pm or not pmod:
            continue
        for sec, oran in outs.items():
            a, b = pm.get(sec), pmod.get(sec)
            if a is None or b is None or a <= 0:
                continue
            rel = abs(b - a) / a
            pb = w_mkt * a + (1.0 - w_mkt) * b
            satirlar.append({"pazar": ad, "sov": sov, "tur": tur, "cizgi": cizgi, "sec": sec, "oran": oran,
                             "p_mkt": a, "p_mod": b, "p": pb, "ev": pb * oran - 1.0, "marj": marj, "rel": rel,
                             "denge": M._denge(pm), "grup": (tur, taraf, uz, "0" in outs, ad.split(" ")[0] == "Deplasman"),
                             "ok": rel <= sapma_tavani and oran >= min_oran})
    return M.ana_hat(satirlar)


def futbol_guven_risk(mod, satir, aday_sayisi):
    """Futbol satiri icin (guven 1-5, risk) - multi_sport_analyzer.guven/risk_belirle ile AYNI
    iskelet, futbol icin ayri tutuldu (M degistirilmesin). Tavan 5: futbol modeli de KALIBRE DEGIL.
    risk: orneklem < 8 ya da model piyasadan > %20 ayrisiyorsa yukselir."""
    try:
        n = mod.get("n") or (0, 0)
        nmin = min(n) if n else 0
        ev = satir.get("ev")
        g = 2 + (nmin >= 5) + (nmin >= 8) + (aday_sayisi >= 4) + (ev is not None and ev >= -0.05)
        guven = max(1, min(g, 5))
        puan, neden = 0, []
        if nmin < 8:
            puan += 1
            neden.append("örneklem %d < 8" % nmin)
        if (satir.get("rel") or 0) > 0.20:
            puan += 1
            neden.append("model piyasadan %%%.0f ayrışıyor" % ((satir.get("rel") or 0) * 100))
        risk = "Yüksek" if puan >= 2 else ("Orta" if puan == 1 else "Düşük")
        return guven, risk, neden
    except Exception:
        return 1, "Yüksek", ["güven/risk hesaplanamadı"]


# ---------------------------------------------------------------------- saglayicilar
def _normalize(**kw):
    taban = {"kaynak": None, "spor": None, "esd_ms": None, "ko": None, "lig": "", "ev": "", "dep": "",
             "pazar": "", "pazar_etiket": None, "sec": "", "oran": None, "ilk_oran": None,
             "p_model": None, "p_piyasa": None, "ev_yuzde": None, "guven": None, "risk": None,
             "n": None, "marj": None, "f": {}, "eksik": []}
    taban.update(kw)
    return taban


def saglayici_futbol(saat=24.0, n=40, oturum=None):
    """iddaa futbol (st=1) -> normalize satirlar. Her mac kendi try/except'inde: biri patlarsa
    digerleri taranmaya devam eder."""
    out = []
    try:
        s = oturum or F._get_session()
        comps = F._fetch_competitions(s)
        cfg = F._fetch_market_config(s, st=1)
        maclar = K.gunluk_maclar(s, 1, comps, saat, n)
    except Exception as ex:
        print("[futbol] bülten alınamadı (%s) - bu kaynak atlandı" % type(ex).__name__)
        return out

    def _cek(m):
        try:
            return {"m": m, "pz": K.pazarlar(s, cfg, m["id"]),
                    "gh": K.oyunlar(K.son_maclar(s, FB_YOL, m["id"], 0)),
                    "ga": K.oyunlar(K.son_maclar(s, FB_YOL, m["id"], 1))}
        except Exception:
            return {"m": m, "pz": [], "gh": [], "ga": []}

    with ThreadPoolExecutor(max_workers=6) as ex:
        veri = list(ex.map(_cek, maclar))
    for v in veri:
        m = v["m"]
        try:
            mod = futbol_model(v["gh"], v["ga"])
            eksik = [] if mod else ["model-olasılığı (örneklem < %d)" % FB_MIN_N]
            if mod:
                satirlar = [x for x in _fiyatla_futbol(mod, v["pz"], m["home"], m["away"],
                                                       MasterOrchestrator.MIN_ORAN,
                                                       MasterOrchestrator.SAPMA_TAVANI,
                                                       MasterOrchestrator.W_MKT) if x["ok"]]
            else:
                satirlar = []
                for ad, sov, outs in v["pz"]:
                    if not M.siniflandir(ad, sov) or len(outs) < 2:
                        continue
                    pm = devig.shin_probs(outs)
                    marj = sum(1.0 / x for x in outs.values()) - 1.0
                    for sec, oran in outs.items():
                        if oran >= MasterOrchestrator.MIN_ORAN and pm.get(sec):
                            satirlar.append({"pazar": ad, "sec": sec, "oran": oran, "p_mkt": pm[sec],
                                             "p_mod": None, "ev": None, "marj": marj, "tur": "ham"})
                satirlar = satirlar[:6]
            aday = len(satirlar)
            for b in satirlar:
                gv, rsk, _neden = futbol_guven_risk(mod or {}, b, aday) if mod else (None, None, [])
                ilk, _ = K.ilk_oran("1|%s|%s|%s" % (m["id"], b["pazar"], b["sec"]), b["oran"])
                out.append(_normalize(kaynak="futbol", spor="futbol", esd_ms=int(m["ts"] * 1000),
                                      ko=m["ts"], lig=m["league"], ev=m["home"], dep=m["away"],
                                      pazar=b["pazar"], sec=b["sec"],
                                      pazar_etiket="%s: %s" % (b["pazar"], M.secenek_adi(b["sec"], m["home"], m["away"])),
                                      oran=b["oran"], ilk_oran=ilk, p_model=b.get("p_mod"),
                                      p_piyasa=b.get("p_mkt"),
                                      ev_yuzde=(b["ev"] * 100.0) if b.get("ev") is not None else None,
                                      guven=gv, risk=rsk, n=(mod or {}).get("n"),
                                      marj=b.get("marj"), f=(mod or {}).get("f") or {}, eksik=eksik))
        except Exception as ex:
            print("[futbol] %s - %s atlandı (%s)" % (m.get("home"), m.get("away"), type(ex).__name__))
    return out


def saglayici_multi(saat=24.0, n=40, spor="hepsi"):
    """multi_sport_analyzer.tara() -> normalize satirlar. M'nin kendi mantigi DEGISMEZ."""
    out = []
    try:
        sonuclar, _ozet = M.tara(spor, saat=saat, n=n, log=False)
    except Exception as ex:
        print("[multi] tarama alınamadı (%s) - bu kaynak atlandı" % type(ex).__name__)
        return out
    for r in sonuclar:
        try:
            ev_, _, dep_ = (r.get("mac") or "").partition(" - ")
            out.append(_normalize(kaynak="multi", spor=r.get("spor"), esd_ms=int(r["ts"] * 1000),
                                  ko=r.get("ts"), lig=r.get("lig"), ev=ev_, dep=dep_,
                                  pazar=r.get("pick"), pazar_etiket=r.get("pick"), sec="",
                                  oran=r.get("oran"), ilk_oran=r.get("ilk"), p_model=r.get("p_mod"),
                                  p_piyasa=r.get("p_mkt"), ev_yuzde=(r.get("ev") or 0) * 100.0,
                                  guven=r.get("guven"), risk=r.get("risk"), n=r.get("n"),
                                  marj=None, f=r.get("f") or {}, eksik=list(r.get("eksik") or [])))
        except Exception:
            continue
    return out


SAGLAYICI = {"futbol": saglayici_futbol, "multi": saglayici_multi}


def tara(kaynaklar=("futbol", "multi"), saat=24.0, n=40, min_oran=None):
    """-> (siralanmis sonuclar, ozet). Bir kaynak patlasa digerleri devam eder."""
    orc = MasterOrchestrator(min_oran=min_oran)
    satirlar, ozet = [], {}
    for ad in kaynaklar:
        fn = SAGLAYICI.get(ad)
        if not fn:
            continue
        try:
            s = fn(saat=saat, n=n)
        except Exception as ex:
            print("[%s] saglayici hatasi (%s) - atlandi" % (ad, type(ex).__name__))
            s = []
        ozet[ad] = len(s)
        satirlar.extend(s)
    sonuc = orc.siralat(satirlar)
    ozet["tier"] = {t: sum(1 for r in sonuc if r.get("tier") == t) for t in ("S", "A", "B")}
    ozet["radar_disi"] = sum(1 for r in sonuc if not r.get("tier"))
    return sonuc, ozet


# ---------------------------------------------------------------------- CLI
def tablo(sonuc, ozet, en=15):
    print("kaynak: %s | S:%d A:%d B:%d | radar dışı: %d"
          % (", ".join("%s=%d" % (k, v) for k, v in ozet.items() if k not in ("tier", "radar_disi")),
             ozet["tier"]["S"], ozet["tier"]["A"], ozet["tier"]["B"], ozet["radar_disi"]))
    print("\n| Tier | Skor | BWM/Piyasa/EV | Spor | Maç | Seçim | Oran | EV |")
    print("|---|---:|---|---|---|---|---:|---:|")
    for r in sonuc[:en]:
        b = r["bilesen"]
        print("| %s | %.0f | %.0f/%.0f/%.0f | %s | %s - %s | %s | %.2f | %s |"
              % (r.get("tier") or "-", r["skor"], b["bwm"], b["piyasa"], b["ev"], r.get("spor"),
                 (r.get("ev") or "")[:16], (r.get("dep") or "")[:16],
                 (r.get("pazar_etiket") or r.get("pazar") or "")[:38], r["oran"] or 0,
                 ("%+.1f%%" % r["ev_yuzde"]) if r.get("ev_yuzde") is not None else "-"))


def durum():
    orc = MasterOrchestrator()
    print("--- master_orchestrator durum ---")
    print("ağırlıklar: %s | tier eşikleri: %s" % (orc.AGIRLIK, dict(orc.TIER_ESIK)))
    print("kırılım: |%%%.0f| -> %.0f puan, |%%%.0f| -> %.0f puan"
          % (orc.KIRILIM_YARIM * 100, orc.AGIRLIK["piyasa"] / 2, orc.KIRILIM_TAM * 100, orc.AGIRLIK["piyasa"]))
    print("EV: +%%%.0f -> %.0f puan | min oran %.2f | sapma tavanı %%%.0f"
          % (orc.EV_TAM * 100, orc.AGIRLIK["ev"], orc.MIN_ORAN, orc.SAPMA_TAVANI * 100))
    print("sağlayıcılar: %s" % ", ".join(SAGLAYICI))
    print("futbol modeli: iddaa recent-matches (yol '%s'), Poisson, FB_HA=%.2f (KALIBRE DEĞİL)" % (FB_YOL, FB_HA))
    print("mükerrer kilidi: %s (%d kayıt)" % (GONDERILEN, len(orc._gonderilen_oku())))


def main(argv=None):
    p = argparse.ArgumentParser(prog="master_orchestrator.py")
    p.add_argument("komut", choices=["tara", "durum"])
    p.add_argument("--kaynak", default="hepsi", help="futbol | multi | hepsi")
    p.add_argument("--saat", type=float, default=24.0)
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--tier", default="B", help="Telegram için asgari tier (S|A|B)")
    p.add_argument("--telegram", action="store_true")
    p.add_argument("--en", type=int, default=15)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    if a.komut == "durum":
        durum()
        return
    kaynaklar = ("futbol", "multi") if a.kaynak in ("hepsi", "tum") else (a.kaynak,)
    sonuc, ozet = tara(kaynaklar, saat=a.saat, n=a.n)
    if a.json:
        print(json.dumps([{k: r[k] for k in ("tier", "skor", "bilesen", "spor", "ev", "dep",
                                             "pazar_etiket", "oran", "ev_yuzde", "eksik")}
                          for r in sonuc if r.get("tier")], ensure_ascii=False, separators=(",", ":")))
    else:
        tablo(sonuc, ozet, a.en)
    if a.telegram:
        n = MasterOrchestrator().telegram_gonder(sonuc, min_tier=a.tier)
        print("\nTelegram: %d yeni mesaj (tier >= %s)" % (n, a.tier))


if __name__ == "__main__":
    main()
