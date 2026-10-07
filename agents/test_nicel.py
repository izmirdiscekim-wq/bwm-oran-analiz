# -*- coding: utf-8 -*-
"""
Nicel cekirdek regresyon testleri: devig, pynum, market_coherence, dixon_coles snapshot,
marj (dinamik esik), acilis (kalici taban), clv (CLV otomasyonu).

Render build bu testleri kosturur -> bu dosya, agir paket (numpy/pandas/scipy) olmadan
calismasi gereken yollarin bulutta bozulmasini engeller. Hicbir test ag cagrisi yapmaz ve
gercek veri dosyalarina yazmaz (tempfile ile izole edilir).
"""
import math
import os
import shutil
import tempfile
import time
import unittest

import acilis
import clv
import devig
import dixon_coles_model as DC
import history_log as HL
import market_coherence as MC
import marj
import pynum


class Devig(unittest.TestCase):
    ORAN = {"1": 1.65, "X": 3.70, "2": 5.00}

    def test_olasiliklar_toplami_bir(self):
        p = devig.shin_probs(self.ORAN)
        self.assertAlmostEqual(sum(p.values()), 1.0, places=9)

    def test_shin_favoriye_oransaldan_fazla_verir(self):
        """Shin'in tanimi: favori-sansli yanliligi duzeltir -> favoriye daha yuksek p."""
        self.assertGreater(devig.shin_probs(self.ORAN)["1"], devig.oransal(self.ORAN)["1"])

    def test_marj(self):
        self.assertAlmostEqual(devig.marj(self.ORAN), 0.0763, places=4)

    def test_bozuk_girdi_bos_doner(self):
        self.assertEqual(devig.shin_probs({"1": 1.0}), {})
        self.assertIsNone(devig.marj({"1": 0}))


class Pynum(unittest.TestCase):
    def test_poisson_toplami(self):
        self.assertAlmostEqual(sum(pynum.pois(1.6, 25)), 1.0, places=6)

    def test_izgara_1x2_toplami(self):
        g = pynum.normalize(pynum.outer(pynum.pois(1.6, 13), pynum.pois(1.1, 13)))
        self.assertAlmostEqual(pynum.tril_sum(g) + pynum.trace(g) + pynum.triu_sum(g), 1.0, places=6)

    def test_toplam_gol_dagilimi(self):
        g = pynum.normalize(pynum.outer(pynum.pois(1.3, 13), pynum.pois(1.3, 13)))
        self.assertAlmostEqual(sum(pynum.toplam_gol(g).values()), 1.0, places=6)

    def test_nelder_mead_yakinsar(self):
        r = pynum.nelder_mead(lambda x: (x[0] - 1.3) ** 2 + (x[1] + 0.7) ** 2, [0.0, 0.0])
        self.assertAlmostEqual(r["x"][0], 1.3, places=2)
        self.assertAlmostEqual(r["x"][1], -0.7, places=2)
        self.assertTrue(r["success"])


class PazarTutarliligi(unittest.TestCase):
    MAC = {"markets": {
        "match_outcome": {"1": 1.65, "X": 3.70, "2": 5.00},
        "totals_goals": [
            {"market_name": "Alt/Üst 2.5", "line": 2.5, "outcomes": {"Alt": 1.95, "Üst": 1.85}},
            {"market_name": "Alt/Üst 1.5", "line": 1.5, "outcomes": {"Alt": 3.55, "Üst": 1.30}},
            {"market_name": "Alt/Üst 3.5", "line": 3.5, "outcomes": {"Alt": 1.37, "Üst": 3.15}}],
        "both_teams_score": {"Var": 1.80, "Yok": 2.00}},
        "stats": {"home_avg_goals_scored": 1.6, "away_avg_goals_scored": 1.0}}

    def test_numpy_olmadan_calisir(self):
        """market_coherence artik numpy/scipy import etmiyor."""
        with open(MC.__file__, encoding="utf-8") as f:
            kaynak = f.read()
        self.assertNotIn("import numpy", kaynak)
        self.assertNotIn("from scipy", kaynak)

    def test_tek_poisson_modeli_pazarlara_oturur(self):
        r = MC.analyze_match(self.MAC)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r["n_markets"], 4)
        self.assertLess(r["rmse"], MC.FIT_RMSE_LIMIT)
        self.assertTrue(r["coherent"])
        self.assertTrue(1.0 < r["lam_home"] < 3.0)

    def test_canli_mac_atlanir(self):
        self.assertIsNone(MC.analyze_match(dict(self.MAC, is_live=True)))


class ModelSnapshot(unittest.TestCase):
    MODEL = {"T1": {"idx": {"A": 0, "B": 1}, "att": [0.45, 0.15], "def": [-0.30, -0.05],
                    "home": 0.26, "rho": -0.04, "n_mac": 240}}

    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.yol = os.path.join(self.dizin, "snap.json")

    def tearDown(self):
        shutil.rmtree(self.dizin, ignore_errors=True)

    def test_yaz_oku_predict(self):
        self.assertEqual(DC.snapshot_yaz(self.MODEL, self.yol), 1)
        geri = DC.snapshot_oku(self.yol)
        p = DC.predict(geri["T1"], "A", "B")
        self.assertAlmostEqual(p["p1"] + p["pX"] + p["p2"], 1.0, places=6)
        self.assertGreater(p["p1"], p["p2"])          # ev sahibi daha gucluydu
        self.assertTrue(0.0 < p["p_over25"] < 1.0)

    def test_eksik_dosya_bos_sozluk(self):
        self.assertEqual(DC.snapshot_oku(os.path.join(self.dizin, "yok.json")), {})


class DinamikMarj(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.eski_dosya, marj.DOSYA = marj.DOSYA, os.path.join(self.dizin, "marj.json")
        self.eski_bellek = marj._BELLEK.copy()
        marj._BELLEK.update({"veri": {}, "kirli": False, "son_yaz": 0.0})

    def tearDown(self):
        marj.DOSYA = self.eski_dosya
        marj._BELLEK.update(self.eski_bellek)
        shutil.rmtree(self.dizin, ignore_errors=True)

    def test_ucuz_lig_dar_tavan_pahali_lig_ek_esik(self):
        for i in range(marj.MIN_N + 4):
            marj.ogren("Ucuz", "1X2", 0.13)
            marj.ogren("Pahali", "1X2", 0.27)
        ucuz_tavan, n1, kaynak1 = marj.tavan("Ucuz", "1X2")
        pahali_tavan, _, _ = marj.tavan("Pahali", "1X2")
        self.assertEqual(kaynak1, "lig medyani")
        self.assertGreaterEqual(n1, marj.MIN_N)
        self.assertLess(ucuz_tavan, pahali_tavan)
        self.assertEqual(marj.esik_ek("Ucuz", "1X2"), 0.0)
        self.assertGreater(marj.esik_ek("Pahali", "1X2"), 0.0)
        self.assertLessEqual(marj.esik_ek("Pahali", "1X2"), marj.ESIK_EK_UST)

    def test_orneklem_yetersizse_globale_duser(self):
        marj.ogren("Yeni", "1X2", 0.20)
        tav, _, kaynak = marj.tavan("Yeni", "1X2", global_tavan=0.23)
        self.assertEqual((tav, kaynak), (0.23, "global"))

    def test_gecersiz_olcum_yoksayilir(self):
        self.assertEqual(marj.ogren("X", "1X2", None), 0)
        self.assertEqual(marj.ogren("X", "1X2", 1.5), 0)


class AcilisTabani(unittest.TestCase):
    def _veri(self, ms1=1.39, ts=None):
        ts = ts or time.time()
        return {"cekim": ts, "olaylar": [{
            "esd": ts + 7200, "esd_ms": 1791400000000, "hn": "Ev", "an": "Dep", "lig": "Test",
            "mk": [{"t": 1, "sov": 0, "o": {"1": ms1, "2": 3.77, "3": 5.14}},
                   {"t": 12, "sov": 2.5, "o": {"1": 1.95, "2": 1.85}},
                   {"t": 999, "sov": 0, "o": {"1": 2.0, "2": 2.0}}]}]}

    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.eski, acilis.DIZIN = acilis.DIZIN, self.dizin
        self.eski_bellek = dict(acilis._BELLEK)
        acilis._BELLEK.clear()

    def tearDown(self):
        acilis.DIZIN = self.eski
        acilis._BELLEK.clear()
        acilis._BELLEK.update(self.eski_bellek)
        shutil.rmtree(self.dizin, ignore_errors=True)

    def test_acilis_asla_uzerine_yazilmaz(self):
        st = acilis.kaydet(self._veri(1.39))
        self.assertEqual(st["yeni"], 1)
        acilis.kaydet(self._veri(1.55))                 # oran degisti
        gun = acilis._gun(time.time() + 7200)
        ilk, _ = acilis.oran(1791400000000, "Ev", "Dep", 1, 0, gun)
        self.assertEqual(ilk["1"], 1.39)                # ACILIS korundu
        hrk = acilis.hareket(1791400000000, "Ev", "Dep", 1, 0, gun)
        self.assertEqual(hrk["1"], (1.39, 1.55))        # acilis -> son

    def test_yalniz_secili_pazarlar_saklanir(self):
        acilis.kaydet(self._veri())
        gun = acilis._gun(time.time() + 7200)
        kayit = acilis._oku(gun)[acilis._anahtar(1791400000000, "Ev", "Dep")]
        self.assertIn("1:0", kayit["ilk"])
        self.assertIn("12:2.5", kayit["ilk"])
        self.assertNotIn("999:0", kayit["ilk"])         # PAZARLAR disi atilir

    def test_disk_yazilamazsa_bellege_duser(self):
        gercek = acilis.os.makedirs

        def patla(*a, **k):
            raise OSError("salt-okunur")
        acilis.os.makedirs = patla
        try:
            st = acilis.kaydet(self._veri())
            self.assertFalse(st["disk"])
            self.assertTrue(acilis._BELLEK)             # kayit bellekte yasiyor
            gun = acilis._gun(time.time() + 7200)
            self.assertIsNotNone(acilis.oran(1791400000000, "Ev", "Dep", 1, 0, gun)[0])
        finally:
            acilis.os.makedirs = gercek


class ClvOtomasyonu(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.eski_iz, clv.IZ_DIR = clv.IZ_DIR, os.path.join(self.dizin, "iz")
        self.eski_hist, HL._SINYAL_DIR = HL._SINYAL_DIR, os.path.join(self.dizin, "history")
        self.now = time.time()
        self.esd = self.now - 60                        # kickoff 1 dk once = pencere icinde
        self.mk = [{"t": 1, "sov": 0, "o": {"1": 1.85, "2": 3.60, "3": 4.40}},
                   {"t": 12, "sov": 2.5, "o": {"1": 1.95, "2": 1.85}}]
        self.veri = {"cekim": self.now, "olaylar": [
            {"esd": self.esd, "esd_ms": int(self.esd * 1000), "hn": "Ev", "an": "Dep",
             "lig": "Test", "mk": self.mk}]}

    def tearDown(self):
        clv.IZ_DIR, HL._SINYAL_DIR = self.eski_iz, self.eski_hist
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _iz(self, oran, hedef):
        return {"kaynak": "test", "taktik": "T", "spor": "futbol", "ev": "Ev", "dep": "Dep",
                "lig": "Test", "esd": self.esd, "esd_ms": int(self.esd * 1000),
                "tarandi": self.now - 7200, "oyna": [{"kod": "k", "hedef": hedef, "oran": oran}]}

    def test_pozitif_clv(self):
        self.assertEqual(clv.iz_kaydet([self._iz(2.00, [1, 0, "1"])]), 1)
        self.assertEqual(clv.kapanis_yakala(self.veri)["yakalanan"], 1)
        self.assertEqual(clv.clv_hesapla()["hesaplanan"], 1)
        kayit = HL.sinyal_oku(dosya=clv.CLV_DOSYA)
        self.assertEqual(len(kayit), 1)
        self.assertTrue(kayit[0]["pozitif_clv"])
        self.assertAlmostEqual(kayit[0]["clv_pct"], (2.00 / 1.85 - 1) * 100, places=2)

    def test_negatif_clv(self):
        clv.iz_kaydet([self._iz(1.70, [12, 2.5, "2"])])
        clv.kapanis_yakala(self.veri)
        clv.clv_hesapla()
        kayit = HL.sinyal_oku(dosya=clv.CLV_DOSYA)
        self.assertFalse(kayit[0]["pozitif_clv"])

    def test_ayni_sinyal_iki_kez_yazilmaz(self):
        r = self._iz(2.00, [1, 0, "1"])
        self.assertEqual(clv.iz_kaydet([r]), 1)
        self.assertEqual(clv.iz_kaydet([r]), 0)

    def test_pencere_kacirilirsa_isaretlenir(self):
        eski = self._iz(2.00, [1, 0, "1"])
        eski["esd"] = self.now - clv.GECIKME_SN - 600
        clv.iz_kaydet([eski])
        st = clv.kapanis_yakala(self.veri)
        self.assertEqual(st["gecti"], 1)

    def test_sinyal_gunlugu_her_izi_yazar(self):
        clv.iz_kaydet([self._iz(2.00, [1, 0, "1"])])
        self.assertEqual(len(HL.sinyal_oku()), 1)       # oynanmasa bile loglandi


if __name__ == "__main__":
    unittest.main()
