# -*- coding: utf-8 -*-
"""
multi_sport_analyzer + msa_kaynak regresyon testleri. HICBIR test ag cagrisi yapmaz ve gercek veri
dosyalarina yazmaz (tempfile). Render build bunlari kosturur.
"""
import json
import os
import shutil
import tempfile
import time
import unittest

import elo_tracker as ET
import msa_kaynak as K
import multi_sport_analyzer as M

GUN = 86400.0


def oyun(pf, pa, gun_once, lig="Lig", ts0=2_000_000_000):
    return {"ts": ts0 - gun_once * GUN, "pf": pf, "pa": pa, "lig": lig, "ad": "T", "sets": [], "sets_opp": []}


def seri(pf, pa, n=8, aralik=3.0, ts0=2_000_000_000, lig="Lig"):
    return [oyun(pf, pa, aralik * (i + 1), lig, ts0) for i in range(n)]


KO = 2_000_000_000


class Matematik(unittest.TestCase):
    def test_ncdf(self):
        self.assertAlmostEqual(M.ncdf(0), 0.5, places=9)
        self.assertAlmostEqual(M.ncdf(1.96), 0.975, places=3)

    def test_shrunk_havuza_ceker(self):
        self.assertAlmostEqual(M.shrunk([], 100.0), 100.0)
        self.assertLess(M.shrunk([120.0], 100.0), 120.0)
        self.assertGreater(M.shrunk([120.0], 100.0), 100.0)

    def test_kelly_ev_esigin_altinda_sifir(self):
        self.assertEqual(M.kelly_pct(0.50, 1.90, "Orta"), 0.0)          # EV -5%
        self.assertEqual(M.kelly_pct(0.0, 2.0, "Orta"), 0.0)

    def test_kelly_pozitif_ve_tavan(self):
        k = M.kelly_pct(0.60, 2.0, "Orta")                              # EV +20%, k=0.2 -> 0.25*20 = 5 -> tavan 2.0
        self.assertEqual(k, M.COEF["STAKE_CAP"])
        kucuk = M.kelly_pct(0.53, 2.0, "Orta")                          # EV +6%, k=0.06 -> 1.5
        self.assertAlmostEqual(kucuk, 1.5, places=2)

    def test_kelly_yuksek_risk_yarisi(self):
        self.assertAlmostEqual(M.kelly_pct(0.53, 2.0, "Yüksek"), M.kelly_pct(0.53, 2.0, "Orta") / 2, places=2)


class Siniflandirma(unittest.TestCase):
    def test_gercek_iddaa_adlari(self):
        self.assertEqual(M.siniflandir("Maç Sonucu (Uzt.Dahil)", None), ("win", None, None, True))
        self.assertEqual(M.siniflandir("Handikaplı Maç Sonucu (Uzt.Dahil) 6.5", "6.5"), ("hcp", None, 6.5, True))
        self.assertEqual(M.siniflandir("Maç Sonucu", None), ("win", None, None, False))
        self.assertEqual(M.siniflandir("Handikaplı Maç Sonucu -0.5", "-0.5"), ("hcp", None, -0.5, False))
        self.assertEqual(M.siniflandir("Altı/Üstü 5.5", "5.5"), ("total", None, 5.5, False))
        self.assertEqual(M.siniflandir("Ev Sahibi Alt/Üst 2.5", "2.5"), ("team", "h", 2.5, False))
        self.assertEqual(M.siniflandir("Deplasman Alt/Üst (Uzt.Dahil) 91.5", "91.5"), ("team", "a", 91.5, True))
        self.assertEqual(M.siniflandir("Handikaplı Maç Sonucu (Set) -1.5", "-1.5"), ("sethcp", None, -1.5, False))

    def test_tanimadigi_pazarlar_none(self):
        for ad in ("1. Çeyrek Sonucu", "En Yüksek Skorlu Çeyrek Altı/Üstü 51.5", "2. Periyot Tek/Çift",
                   "Uzatma Evet / Hayır", "1. Yarı / Maç Sonucu", "3|1.5. Periyot Toplam Sayı Alt/Üst {1}"):
            self.assertIsNone(M.siniflandir(ad, None), ad)

    def test_yarim_mi(self):
        self.assertTrue(M.yarim_mi(6.5))
        self.assertTrue(M.yarim_mi(-0.5))
        self.assertFalse(M.yarim_mi(6.0))
        self.assertFalse(M.yarim_mi(None))


class Basketbol(unittest.TestCase):
    def test_dinlenme_puani(self):
        self.assertEqual(M.dinlenme_puan_basket(1.0), M.COEF["BK_B2B"])
        self.assertEqual(M.dinlenme_puan_basket(2.0), 0.0)
        self.assertEqual(M.dinlenme_puan_basket(4.0), M.COEF["BK_REST3"])
        self.assertEqual(M.dinlenme_puan_basket(None), 0.0)

    def test_b2b_takim_daha_az_beklenen_fark(self):
        gh = seri(100, 95, aralik=3.0)
        dinlenmis = M.model_basket(gh, seri(98, 97, aralik=3.0), KO)
        yorgun = M.model_basket(gh, seri(98, 97, aralik=1.0), KO)           # deplasman B2B
        self.assertTrue(yorgun["f"]["b2b"][1])
        self.assertFalse(yorgun["f"]["b2b"][0])
        # deplasman yorgun -> ev sahibinin beklenen farki ARTAR
        self.assertGreater(yorgun["mh"] - yorgun["ma"], dinlenmis["mh"] - dinlenmis["ma"])

    def test_tempo_havuza_gore(self):
        hizli = M.model_basket(seri(110, 108), seri(112, 109), KO, havuz_tot=200.0)
        yavas = M.model_basket(seri(80, 78), seri(79, 77), KO, havuz_tot=200.0)
        self.assertGreater(hizli["f"]["tempo_kats"], 1.0)
        self.assertLess(yavas["f"]["tempo_kats"], 1.0)
        c = M.COEF["BK_PACE_CLAMP"]
        self.assertTrue(1 - c <= yavas["f"]["tempo_kats"] <= 1 + c)

    def test_ornek_yetersiz_model_yok(self):
        self.assertIsNone(M.model_basket(seri(100, 95, n=3), seri(98, 97, n=8), KO))

    def test_handikap_riski_kurali(self):
        mod = {"F_h": 2.0, "F_a": -2.0}
        # ev -9.5 verir (cizgi -9.5): |H|=9.5, |F|=2 -> 7.5 > 3 -> YUKSEK
        yuk, _ = M.hcp_riski("basketbol", mod, {"tur": "hcp", "sec": "1", "cizgi": -9.5})
        self.assertTrue(yuk)
        # ev -4.5: |H|=4.5, |F|=2 -> 2.5 <= 3 -> riskli degil
        yuk, _ = M.hcp_riski("basketbol", mod, {"tur": "hcp", "sec": "1", "cizgi": -4.5})
        self.assertFalse(yuk)
        # kural yalniz basketbol icin
        yuk, _ = M.hcp_riski("hokey", mod, {"tur": "hcp", "sec": "1", "cizgi": -9.5})
        self.assertFalse(yuk)

    def test_olasiliklar_toplami(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        p = M.olas_normal(mod, "win", None, None, True, {"1", "2"})
        self.assertAlmostEqual(p["1"] + p["2"], 1.0, places=9)
        self.assertIsNone(M.olas_normal(mod, "hcp", None, 6.0, True, {"1", "2"}))     # tam sayi cizgi = iade riski


class Hokey(unittest.TestCase):
    def test_kaleci_belirsizse_katsayi_uygulanmaz(self):
        self.assertEqual(M.gk_faktor(None), 1.0)
        self.assertEqual(M.gk_faktor({"durum": "belirsiz", "sv": 0.95, "gp": 30}), 1.0)

    def test_kaleci_onayli_iyi_az_gol_yedirir(self):
        iyi = M.gk_faktor({"durum": "onayli", "sv": 0.930, "gp": 40})
        kotu = M.gk_faktor({"durum": "onayli", "sv": 0.880, "gp": 40})
        self.assertLess(iyi, 1.0)
        self.assertGreater(kotu, 1.0)
        lo, hi = M.COEF["HK_GK_CLAMP"]
        self.assertTrue(lo <= iyi <= hi and lo <= kotu <= hi)

    def test_yedek_kaleci_varsayilan_kotu(self):
        self.assertGreater(M.gk_faktor({"durum": "yedek", "sv": None, "gp": 10}), 1.0)

    def test_kaleci_etkisi_model_golune_yansir(self):
        gh, ga = seri(3, 3), seri(3, 3)
        notr = M.model_hokey(gh, ga, KO)
        duvar = M.model_hokey(gh, ga, KO, kal_a={"durum": "onayli", "sv": 0.935, "gp": 40})
        self.assertLess(duvar["lam"], notr["lam"])                    # deplasman kalecisi ev golunu keser
        self.assertEqual(duvar["mu"], notr["mu"])

    def test_b2b_az_gol_atar(self):
        gh = seri(3, 3, aralik=1.0)                                    # ev B2B
        yorgun = M.model_hokey(gh, seri(3, 3, aralik=3.0), KO)
        notr = M.model_hokey(seri(3, 3, aralik=3.0), seri(3, 3, aralik=3.0), KO)
        self.assertLess(yorgun["lam"], notr["lam"])

    def test_eksik_listesi_kaleci(self):
        self.assertIn("kaleci-onayi", M.model_hokey(seri(3, 3), seri(3, 3), KO)["eksik"])
        onayli = M.model_hokey(seri(3, 3), seri(3, 3), KO, kal_h={"durum": "onayli", "sv": 0.91, "gp": 20})
        self.assertNotIn("kaleci-onayi", onayli["eksik"])

    def test_puckline_ve_moneyline(self):
        mod = {"kind": "poisson", "lam": 3.4, "mu": 2.2}
        ml = M.olas_hokey(mod, "win", None, None, True, {"1", "2"})
        self.assertAlmostEqual(ml["1"] + ml["2"], 1.0, places=9)
        self.assertGreater(ml["1"], 0.5)
        pl_ev = M.olas_hokey(mod, "hcp", None, -1.5, False, {"1", "2"})     # ev -1.5 (2+ farkla kazan)
        pl_dep = M.olas_hokey(mod, "hcp", None, 1.5, False, {"1", "2"})     # ev +1.5
        self.assertLess(pl_ev["1"], ml["1"])                                # -1.5, moneyline'dan zor
        self.assertGreater(pl_dep["1"], ml["1"])                            # +1.5, moneyline'dan kolay
        self.assertAlmostEqual(pl_ev["1"] + pl_ev["2"], 1.0, places=9)

    def test_uzatma_beraberligi_boler(self):
        mod = {"kind": "poisson", "lam": 2.0, "mu": 2.0}
        resmi = M.olas_hokey(mod, "win", None, None, False, {"1", "0", "2"})
        uz = M.olas_hokey(mod, "win", None, None, True, {"1", "2"})
        self.assertAlmostEqual(uz["1"], resmi["1"] + resmi["0"] / 2.0, places=9)

    def test_uzatma_dahil_toplam_fiyatlanmaz(self):
        mod = {"kind": "poisson", "lam": 3.0, "mu": 2.5}
        self.assertIsNone(M.olas_hokey(mod, "total", None, 5.5, True, {"Alt", "Üst"}))
        t = M.olas_hokey(mod, "total", None, 5.5, False, {"Alt", "Üst"})
        self.assertAlmostEqual(t["Alt"] + t["Üst"], 1.0, places=9)


class Tenis(unittest.TestCase):
    def test_q_coz_ters_donusum(self):
        for p in (0.2, 0.5, 0.73):
            q = M.q_coz(p, 3)
            self.assertAlmostEqual(q * q * (3 - 2 * q), p, places=6)
            q5 = M.q_coz(p, 5)
            self.assertAlmostEqual(q5 ** 3 * (10 - 15 * q5 + 6 * q5 * q5), p, places=6)

    def test_set_handikabi(self):
        mod = {"kind": "tennis", "p": 0.7, "q": M.q_coz(0.7, 3), "bo": 3}
        a = M.olas_tenis(mod, "sethcp", None, -1.5, False, set())
        b = M.olas_tenis(mod, "sethcp", None, 1.5, False, set())
        self.assertLess(a["1"], 0.7)                                         # 2-0 kazanmak, mac kazanmaktan zor
        self.assertGreater(b["1"], 0.7)                                      # +1.5, mac kazanmaktan kolay
        self.assertIsNone(M.olas_tenis(dict(mod, bo=5), "sethcp", None, -1.5, False, set()))

    def test_zemin_tablosu(self):
        self.assertEqual(K.tenis_zemin("ATP Şanghay, Çin Erkekler Tekler"), "sert")
        self.assertEqual(K.tenis_zemin("ATP Challenger Villena"), "kil")
        self.assertEqual(K.tenis_zemin("Wimbledon"), "cim")
        self.assertIsNone(K.tenis_zemin("ITF Bilinmeyen Kasaba"))

    def test_zemin_gecmisi_yalniz_ayni_zeminde(self):
        oy = [oyun(2, 0, 3, "Shanghai, China"), oyun(2, 1, 6, "Beijing, China"), oyun(0, 2, 9, "ATP Challenger Villena")]
        wr_sert, n_sert, _ = M.zemin_wr(oy, "sert")
        self.assertEqual(n_sert, 2)
        wr_kil, n_kil, _ = M.zemin_wr(oy, "kil")
        self.assertEqual(n_kil, 1)
        self.assertGreater(wr_sert, wr_kil)

    def test_servis_verisi_yoksa_eksik_ve_uygulanmaz(self):
        gh = [oyun(2, 0, 3 * (i + 1), "Shanghai, China") for i in range(6)]
        ga = [oyun(0, 2, 3 * (i + 1), "Shanghai, China") for i in range(6)]
        m0 = M.model_tenis(gh, ga, "Shanghai, China")
        self.assertIn("servis/karsilama", m0["eksik"])
        self.assertIsNone(m0["f"]["servis_farki"])
        m1 = M.model_tenis(gh, ga, "Shanghai, China", {"spw": 0.70, "rpw": 0.40}, {"spw": 0.60, "rpw": 0.33})
        self.assertNotIn("servis/karsilama", m1["eksik"])
        self.assertGreater(m1["p"], m0["p"] - 1e-9)

    def test_olasilik_siniri(self):
        gh = [oyun(2, 0, 3 * (i + 1), "Shanghai, China") for i in range(10)]
        ga = [oyun(0, 2, 3 * (i + 1), "Shanghai, China") for i in range(10)]
        m = M.model_tenis(gh, ga, "Shanghai, China")
        lo, hi = M.COEF["TN_P_CLAMP"]
        self.assertTrue(lo <= m["p"] <= hi)

    def test_skor_tahmini(self):
        self.assertIn("2-0", M.skor_tahmin({"kind": "tennis", "q": 0.8, "bo": 3}))


class Secim(unittest.TestCase):
    def _pz(self):
        return [("Maç Sonucu (Uzt.Dahil)", None, {"1": 1.50, "2": 2.50}),
                ("Handikaplı Maç Sonucu (Uzt.Dahil) -3.5", "-3.5", {"1": 2.05, "2": 1.75}),
                ("Handikaplı Maç Sonucu (Uzt.Dahil) -9.5", "-9.5", {"1": 3.10, "2": 1.33}),
                ("Handikaplı Maç Sonucu (Uzt.Dahil) -6.5", "-6.5", {"1": 2.40, "2": 1.55})]

    def test_ana_hat_yalniz_dengeli_hat(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        sat = M.fiyatla("basketbol", mod, self._pz(), "A", "B")
        cizgiler = {s["cizgi"] for s in sat if s["tur"] == "hcp"}
        self.assertEqual(len(cizgiler), 1)                              # 3 hattan yalniz biri kaldi
        self.assertEqual(cizgiler, {-3.5})                              # p(1)~0.46 -> 0.5'e en yakin

    def test_min_oran_kapisi(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        sat = M.fiyatla("basketbol", mod, [("Maç Sonucu (Uzt.Dahil)", None, {"1": 1.12, "2": 6.5})], "A", "B")
        self.assertTrue(sat)
        self.assertFalse(next(s for s in sat if s["sec"] == "1")["ok"])      # 1.12 < 1.30

    def test_hcp_isaret_testi(self):
        # ev sahibi favori (1.50 / 2.50) ve ana hat NEGATIF -> uyumlu
        self.assertTrue(M.hcp_isaret_testi(self._pz()))
        ters = [("Maç Sonucu (Uzt.Dahil)", None, {"1": 1.50, "2": 2.50}),
                ("Handikaplı Maç Sonucu (Uzt.Dahil) 3.5", "3.5", {"1": 2.05, "2": 1.75})]
        self.assertFalse(M.hcp_isaret_testi(ters))
        self.assertIsNone(M.hcp_isaret_testi([("Maç Sonucu (Uzt.Dahil)", None, {"1": 1.95, "2": 1.95})]))

    def test_hcp_kapaliysa_handikap_fiyatlanmaz(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        sat = M.fiyatla("basketbol", mod, self._pz(), "A", "B", hcp_ok=False)
        self.assertFalse([s for s in sat if s["tur"] == "hcp"])

    def test_risk(self):
        mod = M.model_hokey(seri(3, 3, n=6), seri(3, 3, n=6), KO)
        risk, neden = M.risk_belirle("hokey", mod, {"tur": "win", "sec": "1", "cizgi": None})
        self.assertEqual(risk, "Yüksek")                                    # ornek<8 + kaleci onayi yok
        self.assertTrue(any("kaleci" in n for n in neden))

    def test_guven_tavani(self):
        mod = M.model_hokey(seri(3, 3), seri(3, 3), KO)
        self.assertLessEqual(M.guven("hokey", mod, 10, 0.0), 4)             # kaleci belirsiz -> tavan 4
        tn = {"n": (10, 10), "eksik": []}
        self.assertLessEqual(M.guven("tenis", tn, 10, 0.0), M.SPORLAR["tenis"]["cap"])

    def test_kompakt_json_kucuk_ve_ham_veri_icermez(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        mac = {"id": "1", "home": "A", "away": "B", "league": "Lig", "ts": KO}
        yol_eski, K.VERI = K.VERI, tempfile.mkdtemp()
        try:
            r = M.degerlendir("basketbol", mac, mod, self._pz())
            self.assertIsNotNone(r)
            js = json.dumps(M.kompakt(r), ensure_ascii=False, separators=(",", ":"))
            self.assertLess(len(js.encode("utf-8")), 700)                    # mac basina kucuk
            for yasak in ("pf", "pa", "recent", "oyunlar"):
                self.assertNotIn('"%s"' % yasak, js)
        finally:
            shutil.rmtree(K.VERI, ignore_errors=True)
            K.VERI = yol_eski


class Kaynak(unittest.TestCase):
    def setUp(self):
        self.eski, K.VERI = K.VERI, tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(K.VERI, ignore_errors=True)
        K.VERI = self.eski

    def test_fold(self):
        self.assertEqual(K.fold("  ŞANGHAY  Çin "), "sanghay cin")

    def test_oyunlar_konu_takim_ve_cift_filtresi(self):
        ham = [{"t": 3, "l": {"n": "ATP Şanghay"}, "h": {"n": "Tomic B.", "rs": 2}, "a": {"n": "Skatov, T.", "rs": 0}},
               {"t": 2, "l": {"n": "ATP Paris"}, "h": {"n": "Rune H.", "rs": 1}, "a": {"n": "Tomic B.", "rs": 2}},
               {"t": 1, "l": {"n": "ATP Viyana, Çiftler"}, "h": {"n": "Tomic B.", "rs": 2}, "a": {"n": "X", "rs": 0}},
               {"t": 0, "l": {"n": "ATP Basel"}, "h": {"n": "Tomic B.", "rs": None}, "a": {"n": "Y", "rs": 1}}]
        oy = K.oyunlar(ham)
        self.assertEqual(len(oy), 2)                                        # cift + skorsuz elendi
        self.assertEqual([g["pf"] for g in oy], [2, 2])
        self.assertEqual([g["ts"] for g in oy], [3, 2])                     # yeniden eskiye

    def test_dinlenme_gun(self):
        oy = [oyun(1, 0, 2.0), oyun(1, 0, 5.0)]
        self.assertAlmostEqual(K.dinlenme_gun(oy, KO), 2.0, places=6)
        self.assertIsNone(K.dinlenme_gun([], KO))

    def test_ilk_oran_ilk_deger_korunur(self):
        self.assertEqual(K.ilk_oran("k", 1.80)[0], 1.80)
        self.assertEqual(K.ilk_oran("k", 1.50)[0], 1.80)                    # sonradan degisse de ilk deger

    def test_kaleci_yaz_oku_ve_gecerlilik(self):
        K.kaleci_yaz("Toronto Maple Leafs", "onayli", 0.918)
        k = K.kaleci_oku("toronto maple leafs")
        self.assertEqual((k["durum"], k["sv"]), ("onayli", 0.918))
        with open(K.kaleci_dosyasi(), encoding="utf-8") as f:
            d = json.load(f)
        d["toronto maple leafs"]["ts"] = time.time() - 20 * 3600             # 18 saatten eski
        with open(K.kaleci_dosyasi(), "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertIsNone(K.kaleci_oku("Toronto Maple Leafs"))
        with self.assertRaises(ValueError):
            K.kaleci_yaz("X", "belki")

    def test_nhl_eslesme(self):
        tablo = {"washington capitals": {"sv": 0.92, "gp": 5}}
        self.assertEqual(K.nhl_bul("Washington Capitals", tablo)["sv"], 0.92)
        self.assertIsNone(K.nhl_bul("Jokerit Helsinki", tablo))


class SinyalGunlugu(unittest.TestCase):
    def setUp(self):
        import history_log as HL
        self.HL = HL
        self.dizin = tempfile.mkdtemp()
        self.eski, HL._SINYAL_DIR = HL._SINYAL_DIR, self.dizin

    def tearDown(self):
        self.HL._SINYAL_DIR = self.eski
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _r(self, pick="Maç Sonucu: A"):
        return {"spor": "hokey", "ts": KO, "mac": "A - B", "lig": "L", "pick": pick, "oran": 1.9, "p_mod": 0.6,
                "p_mkt": 0.5, "ev": 0.03, "bayrak": "SINYAL", "karar": "OYNA", "kasa": 0.5, "risk": "Orta", "f": {}}

    def test_ayni_sinyal_iki_taramada_tek_kez_yazilir(self):
        M._logla([self._r()])
        M._logla([self._r()])
        M._logla([self._r("Altı/Üstü 5.5: Üst")])
        kayit = [k for k in self.HL.sinyal_oku() if k["kaynak"].startswith("multi:")]
        self.assertEqual(len(kayit), 2)                                    # tekrar yazilmadi, yeni pick yazildi


class EloMotoru(unittest.TestCase):
    def setUp(self):
        self.eski, K.VERI = K.VERI, tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(K.VERI, ignore_errors=True)
        K.VERI = self.eski

    def test_beklenen_esit_guclerde_yarim(self):
        self.assertAlmostEqual(ET.beklenen(1500, 1500), 0.5, places=9)
        self.assertGreater(ET.beklenen(1600, 1500), 0.5)

    def test_seed_from_market_veri_yoksa_none(self):
        self.assertEqual(ET.seed_from_market(None), (None, None))

    def test_seed_from_market_favoriye_yuksek_seed(self):
        sh, sa = ET.seed_from_market(0.7)
        self.assertGreater(sh, ET.BASE)
        self.assertLess(sa, ET.BASE)
        self.assertAlmostEqual(sh + sa, 2 * ET.BASE, places=6)

    def test_guncelle_kazanan_yukselir_kaybeden_duser(self):
        eh, ea = ET.guncelle("test_guncelle", "A", "B", 3, 1)
        self.assertGreater(eh, ET.BASE)
        self.assertLess(ea, ET.BASE)

    def test_gecmisten_kur_rakip_de_guncellenir(self):
        oy = [{"ts": 100, "pf": 3, "pa": 1, "rakip": "Rakip"}, {"ts": 200, "pf": 2, "pa": 2, "rakip": "Rakip2"}]
        elo = ET.gecmisten_kur("test_replay", "Takim", oy)
        self.assertNotEqual(elo, ET.BASE)
        self.assertIsNotNone(ET.oku("test_replay", "Rakip"))
        self.assertIsNotNone(ET.oku("test_replay", "Rakip2"))

    def test_gecmisten_kur_ayni_mac_tekrar_islenmez(self):
        oy = [{"ts": 100, "pf": 3, "pa": 1, "rakip": "Rakip"}]
        e1 = ET.gecmisten_kur("test_idempotent", "Takim", oy)
        e2 = ET.gecmisten_kur("test_idempotent", "Takim", oy)       # ayni tarama, ayni mac
        self.assertEqual(e1, e2)

    def test_gecmisten_kur_ilk_seed_kullanilir(self):
        self.assertEqual(ET.gecmisten_kur("test_seed", "Yeni Takim", [], ilk_seed=1650.0), 1650.0)
        self.assertEqual(ET.gecmisten_kur("test_seed2", "Yeni Takim2", []), ET.BASE)   # seed yok -> duz 1500

    def test_sos_bilinen_rakip_ortalamasi(self):
        ET.guncelle("test_sos", "GucluTakim", "ZayifTakim", 5, 0)
        gt_elo = ET.oku("test_sos", "GucluTakim")
        delta, n = ET.sos("test_sos", [{"rakip": "GucluTakim"}])
        self.assertEqual(n, 1)
        self.assertAlmostEqual(delta, gt_elo - ET.BASE, places=6)

    def test_sos_bos_liste(self):
        self.assertEqual(ET.sos("test_sos_bos", []), (0.0, 0))


class EloSosEntegrasyonu(unittest.TestCase):
    def test_basketbolda_guclu_rakip_gecmisi_ev_lehine_farki_arttirir(self):
        notr = M.model_basket(seri(100, 95), seri(100, 95), KO)
        guclu_sos = M.model_basket(seri(100, 95), seri(100, 95), KO, elo_sos=(200.0, 0.0))
        self.assertGreater(guclu_sos["mh"] - guclu_sos["ma"], notr["mh"] - notr["ma"])
        self.assertEqual(guclu_sos["f"]["elo_sos"], [200.0, 0.0])

    def test_basketbolda_elo_sos_yoksa_eski_davranis(self):
        notr = M.model_basket(seri(100, 95), seri(98, 97), KO)
        self.assertEqual(notr["f"]["elo_sos"], [None, None])

    def test_hokeyde_guclu_rakip_gecmisi_oranini_arttirir(self):
        notr = M.model_hokey(seri(3, 3), seri(3, 3), KO)
        guclu_sos = M.model_hokey(seri(3, 3), seri(3, 3), KO, elo_sos=(200.0, 0.0))
        self.assertGreater(guclu_sos["lam"] / guclu_sos["mu"], notr["lam"] / notr["mu"])


class TenisZeminEloFallback(unittest.TestCase):
    def test_elo_fallback_kullanilir_servis_hala_eksik_sayilir(self):
        # karisik sonuclar (tam 2-0/0-2 degil) -> olasilik TN_P_CLAMP tavanina yapismaz,
        # elo fallback'in eklendigini olcebiliriz
        gh = [oyun(2, 0, 3, "Shanghai, China"), oyun(2, 0, 6, "Shanghai, China"),
              oyun(2, 0, 9, "Shanghai, China"), oyun(2, 0, 12, "Shanghai, China"),
              oyun(0, 2, 15, "Shanghai, China"), oyun(0, 2, 18, "Shanghai, China")]
        ga = [oyun(2, 0, 3, "Shanghai, China"), oyun(2, 0, 6, "Shanghai, China"),
              oyun(0, 2, 9, "Shanghai, China"), oyun(0, 2, 12, "Shanghai, China"),
              oyun(0, 2, 15, "Shanghai, China"), oyun(0, 2, 18, "Shanghai, China")]
        m = M.model_tenis(gh, ga, "Shanghai, China", surf_elo=(1700, 1500))
        self.assertTrue(m["f"]["elo_fallback"])
        self.assertIn("servis/karsilama", m["eksik"])                # hala durustce "yok" deniyor
        m_notr = M.model_tenis(gh, ga, "Shanghai, China")
        self.assertGreater(m["p"], m_notr["p"])

    def test_elo_fallback_yetersiz_zemin_orneginde_uygulanmaz(self):
        gh = [oyun(2, 0, 3 * (i + 1), "Shanghai, China") for i in range(6)]
        ga = [oyun(0, 2, 3 * (i + 1), "ATP Challenger Villena") for i in range(6)]   # farkli zemin
        m = M.model_tenis(gh, ga, "Shanghai, China", surf_elo=(1700, 1500))
        self.assertFalse(m["f"]["elo_fallback"])

    def test_servis_verisi_varsa_elo_fallback_devreye_girmez(self):
        gh = [oyun(2, 0, 3 * (i + 1), "Shanghai, China") for i in range(6)]
        ga = [oyun(0, 2, 3 * (i + 1), "Shanghai, China") for i in range(6)]
        m = M.model_tenis(gh, ga, "Shanghai, China", {"spw": 0.70, "rpw": 0.40},
                          {"spw": 0.60, "rpw": 0.33}, surf_elo=(1700, 1500))
        self.assertFalse(m["f"]["elo_fallback"])

    def test_guven_tavani_elo_fallback_ile_4e_yukselir(self):
        gh = [oyun(2, 0, 3 * (i + 1), "Shanghai, China") for i in range(8)]
        ga = [oyun(0, 2, 3 * (i + 1), "Shanghai, China") for i in range(8)]
        m = M.model_tenis(gh, ga, "Shanghai, China", surf_elo=(1700, 1500))
        self.assertEqual(M.guven("tenis", m, 10, 0.0), 4)
        m_notr = M.model_tenis(gh, ga, "Shanghai, China")
        self.assertLessEqual(M.guven("tenis", m_notr, 10, 0.0), 3)


class PipeFormat(unittest.TestCase):
    def _r(self):
        mod = M.model_basket(seri(100, 95), seri(98, 97), KO)
        mac = {"id": "1", "home": "Los Angeles Lakers", "away": "Boston Celtics", "league": "NBA", "ts": KO}
        pz = [("Handikaplı Maç Sonucu (Uzt.Dahil) -3.5", "-3.5", {"1": 2.05, "2": 1.75})]
        return M.degerlendir("basketbol", mac, mod, pz)

    def test_format_parcalari_mevcut(self):
        r = self._r()
        self.assertIsNotNone(r)
        s = M.to_pipe_format(r)
        self.assertTrue(s.startswith("[BSK]|LAL-BCE|"))
        for parca in ("FLG:", "MKT:", "EV:", "KLY:"):
            self.assertIn(parca, s)

    def test_ham_veri_icermez(self):
        s = M.to_pipe_format(self._r())
        for yasak in ("pf", "pa", "recent", "oyunlar"):
            self.assertNotIn(yasak, s)

    def test_kompakttan_daha_kisa(self):
        r = self._r()
        js = json.dumps(M.kompakt(r), ensure_ascii=False, separators=(",", ":"))
        pipe = M.to_pipe_format(r)
        self.assertLess(len(pipe.encode("utf-8")), len(js.encode("utf-8")))

    def test_fct_hokeyde_kaleci_kats_gosterir(self):
        mod = M.model_hokey(seri(3, 3), seri(3, 3), KO, kal_a={"durum": "onayli", "sv": 0.935, "gp": 40})
        r = {"spor": "hokey", "mac": "A - B", "ts": KO, "pick": "Maç Sonucu (Uzt.Dahil): A", "oran": 1.9,
             "ev": 0.03, "kasa": 0.5, "bayrak": "SINYAL", "f": mod["f"]}
        s = M.to_pipe_format(r)
        self.assertIn("FCT:", s)
        self.assertIn("GK(", s)


class ScraperGuvenlik(unittest.TestCase):
    def test_ag_hatasinda_cokmez_bos_sozluk_doner(self):
        import importlib
        from unittest import mock
        nhl_goalies = importlib.import_module("scrapers.nhl_goalies")
        with mock.patch("requests.get", side_effect=OSError("ag yok")):
            sonuc, uyari = nhl_goalies.confirmed_goalies()
        self.assertEqual(sonuc, {})
        self.assertIsNotNone(uyari)

    def test_http_hata_kodunda_cokmez(self):
        import importlib
        from unittest import mock
        nhl_goalies = importlib.import_module("scrapers.nhl_goalies")

        class SahteYanit:
            status_code = 403
        with mock.patch("requests.get", return_value=SahteYanit()):
            sonuc, uyari = nhl_goalies.confirmed_goalies()
        self.assertEqual(sonuc, {})
        self.assertIn("403", uyari)

    def test_uygula_yalniz_confirmed_olanlari_yazar(self):
        import importlib
        nhl_goalies = importlib.import_module("scrapers.nhl_goalies")
        eski, K.VERI = K.VERI, tempfile.mkdtemp()
        try:
            n = nhl_goalies.uygula({"Toronto Maple Leafs": {"kaleci": "X", "durum": "onayli", "sv": 0.92}})
            self.assertEqual(n, 1)
            self.assertEqual(K.kaleci_oku("Toronto Maple Leafs")["durum"], "onayli")
        finally:
            shutil.rmtree(K.VERI, ignore_errors=True)
            K.VERI = eski


if __name__ == "__main__":
    unittest.main()
