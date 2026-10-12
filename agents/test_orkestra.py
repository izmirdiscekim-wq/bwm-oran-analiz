# -*- coding: utf-8 -*-
"""master_orchestrator testleri - HICBIRI ag cagrisi yapmaz, uretim verisine yazmaz (tempfile).
Puanlama, TIER esikleri, graceful degradation, Telegram sablonu + mukerrer kilidi."""
import json
import os
import shutil
import tempfile
import unittest

import master_orchestrator as MO

KO = 2_000_000_000


def satir(**kw):
    d = {"kaynak": "futbol", "spor": "futbol", "esd_ms": KO * 1000, "ko": KO, "lig": "L",
         "ev": "Galatasaray", "dep": "Fenerbahce", "pazar": "Maç Sonucu", "pazar_etiket": "Maç Sonucu: 1",
         "sec": "1", "oran": 2.10, "ilk_oran": 2.10, "p_model": 0.50, "p_piyasa": 0.48,
         "ev_yuzde": 0.0, "guven": 5, "risk": "Orta", "n": (10, 10), "marj": 0.10, "f": {}, "eksik": []}
    d.update(kw)
    return d


class Puanlama(unittest.TestCase):
    def setUp(self):
        self.o = MO.MasterOrchestrator()

    def test_agirlik_toplami_100(self):
        self.assertEqual(sum(self.o.AGIRLIK.values()), 100.0)

    # --- piyasa / balina
    def test_kirilim_yuzde10_yarim_puan(self):
        p, d = self.o.puan_piyasa(satir(ilk_oran=2.00, oran=1.80))      # -%10
        self.assertAlmostEqual(p, 15.0, places=1)
        self.assertIn("kırılım", d)

    def test_kirilim_yuzde20_tam_puan(self):
        p, _ = self.o.puan_piyasa(satir(ilk_oran=2.00, oran=1.60))      # -%20
        self.assertAlmostEqual(p, 30.0, places=1)

    def test_kirilim_yuzde30_tavani_gecmez(self):
        p, _ = self.o.puan_piyasa(satir(ilk_oran=2.00, oran=1.40))
        self.assertEqual(p, 30.0)

    def test_oran_yukseldiyse_puan_yok(self):
        p, d = self.o.puan_piyasa(satir(ilk_oran=1.80, oran=2.20))      # aleyhte hareket
        self.assertEqual(p, 0.0)
        self.assertIn("aleyhte", d)

    def test_acilis_orani_yoksa_sifir_puan_ve_cokmez(self):
        p, d = self.o.puan_piyasa(satir(ilk_oran=None))
        self.assertEqual(p, 0.0)
        self.assertIn("açılış", d)

    # --- EV
    def test_ev_negatifse_puan_yok(self):
        self.assertEqual(self.o.puan_ev(satir(ev_yuzde=-9.0))[0], 0.0)
        self.assertEqual(self.o.puan_ev(satir(ev_yuzde=0.0))[0], 0.0)

    def test_ev_yuzde5_tam_puan(self):
        self.assertAlmostEqual(self.o.puan_ev(satir(ev_yuzde=5.0))[0], 30.0, places=1)
        self.assertAlmostEqual(self.o.puan_ev(satir(ev_yuzde=2.5))[0], 15.0, places=1)
        self.assertEqual(self.o.puan_ev(satir(ev_yuzde=12.0))[0], 30.0)      # tavan

    def test_ev_yoksa_sifir(self):
        self.assertEqual(self.o.puan_ev(satir(ev_yuzde=None))[0], 0.0)

    # --- BWM
    def test_model_yoksa_zayif_bant_en_cok_10(self):
        p, d = self.o.puan_bwm(satir(p_model=None, marj=0.08))
        self.assertLessEqual(p, 10.0)
        self.assertIn("zayıf", d)

    def test_model_varsa_guven_ve_risk_puana_girer(self):
        yuksek = self.o.puan_bwm(satir(guven=9, risk="Düşük", n=(10, 10)))[0]
        dusuk = self.o.puan_bwm(satir(guven=3, risk="Yüksek", n=(5, 5)))[0]
        self.assertGreater(yuksek, dusuk)
        self.assertLessEqual(yuksek, 40.0)

    def test_elo_sos_varsa_ek_puan(self):
        with_elo = self.o.puan_bwm(satir(f={"elo_sos": [20.0, -5.0]}))[0]
        without = self.o.puan_bwm(satir(f={"elo_sos": [None, None]}))[0]
        self.assertGreater(with_elo, without)

    def test_bozuk_veri_cokmez_sifir_puan(self):
        for bozuk in ({"p_model": 0.5, "guven": "abc"}, {"ilk_oran": "x", "oran": None},
                      {"ev_yuzde": "yok"}):
            r = self.o.master_skor(satir(**bozuk))
            self.assertIsInstance(r["skor"], float)


class Tier(unittest.TestCase):
    def setUp(self):
        self.o = MO.MasterOrchestrator()

    def test_esikler(self):
        self.assertEqual(self.o.tier(92), "S")
        self.assertEqual(self.o.tier(85), "S")
        self.assertEqual(self.o.tier(84), "A")
        self.assertEqual(self.o.tier(70), "A")
        self.assertEqual(self.o.tier(69), "B")
        self.assertEqual(self.o.tier(60), "B")
        self.assertIsNone(self.o.tier(59))

    def test_s_tier_ucu_birden_gerektirir(self):
        # guclu BWM + %20 kirilim + EV +%5 -> 40+30+30 = 100
        r = self.o.master_skor(satir(guven=10, risk="Düşük", n=(10, 10), f={"elo_sos": [30.0, 0.0]},
                                     ilk_oran=2.00, oran=1.60, ev_yuzde=5.0))
        self.assertEqual(r["tier"], "S")
        self.assertGreaterEqual(r["skor"], 85)

    def test_b_tier_yalniz_balina_ile(self):
        # BWM zayif (model yok) + sert kirilim -> B bandina girebilir
        r = self.o.master_skor(satir(p_model=None, ev_yuzde=None, marj=0.08,
                                     ilk_oran=2.00, oran=1.55, p_piyasa=0.6))
        self.assertEqual(r["bilesen"]["ev"], 0.0)
        self.assertGreater(r["bilesen"]["piyasa"], 25)
        self.assertIn(r["tier"], ("B", None))

    def test_ev_negatif_dunyada_s_tier_cikmaz(self):
        """Olculen gerceklik: tum kararlar EV <= 0. Bu durumda tavan 70 puandir -> S imkansiz."""
        r = self.o.master_skor(satir(guven=10, risk="Düşük", n=(10, 10), f={"elo_sos": [30.0, 0.0]},
                                     ilk_oran=2.00, oran=1.50, ev_yuzde=-11.0))
        self.assertEqual(r["bilesen"]["ev"], 0.0)
        self.assertLess(r["skor"], 85)
        self.assertNotEqual(r["tier"], "S")

    def test_siralama_skora_gore(self):
        s = self.o.siralat([satir(oran=2.0, ilk_oran=2.0), satir(ilk_oran=2.00, oran=1.60)])
        self.assertGreaterEqual(s[0]["skor"], s[1]["skor"])


class Mesaj(unittest.TestCase):
    def setUp(self):
        self.o = MO.MasterOrchestrator()

    def test_sablon_alanlari(self):
        r = self.o.master_skor(satir(guven=10, risk="Düşük", n=(10, 10), ilk_oran=2.00, oran=1.72,
                                     ev_yuzde=4.2, f={"elo_sos": [20.0, 0.0]}))
        m = self.o.mesaj(r)
        self.assertIn("TIER]", m)
        self.assertIn("Maç: Galatasaray - Fenerbahce", m)
        self.assertIn("Master Skor:", m)
        self.assertIn("EV: +4.2%", m)
        self.assertIn("balina kırılımı", m)

    def test_eksik_veri_mesajda_belirtilir(self):
        r = self.o.master_skor(satir(p_model=None, ev_yuzde=None, ilk_oran=None))
        self.assertIn("Eksik veri", self.o.mesaj(r))


class Telegram(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.eski = MO.GONDERILEN
        MO.GONDERILEN = os.path.join(self.dizin, "gonderilen.json")
        self.o = MO.MasterOrchestrator()
        self.gonderilen = []

    def tearDown(self):
        MO.GONDERILEN = self.eski
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _fn(self, metin):
        self.gonderilen.append(metin)
        return True

    def _s_tier(self):
        return self.o.master_skor(satir(guven=10, risk="Düşük", n=(10, 10), f={"elo_sos": [30.0, 0.0]},
                                        ilk_oran=2.00, oran=1.60, ev_yuzde=5.0))

    def test_tier_esigi_altindakiler_gonderilmez(self):
        dusuk = self.o.master_skor(satir(ev_yuzde=-10.0, ilk_oran=2.0, oran=2.0))
        self.assertEqual(self.o.telegram_gonder([dusuk], min_tier="B", gonder_fn=self._fn), 0)
        self.assertEqual(self.gonderilen, [])

    def test_s_tier_gonderilir(self):
        n = self.o.telegram_gonder([self._s_tier()], min_tier="B", gonder_fn=self._fn)
        self.assertEqual(n, 1)
        self.assertIn("[S-TIER]", self.gonderilen[0])

    def test_ayni_sinyal_ikinci_turda_gonderilmez(self):
        r = self._s_tier()
        self.o.telegram_gonder([r], min_tier="B", gonder_fn=self._fn)
        n2 = self.o.telegram_gonder([r], min_tier="B", gonder_fn=self._fn)
        self.assertEqual(n2, 0)
        self.assertEqual(len(self.gonderilen), 1)

    def test_min_tier_s_ise_b_gonderilmez(self):
        b = self.o.master_skor(satir(p_model=None, ev_yuzde=None, marj=0.08, ilk_oran=2.00, oran=1.55))
        self.o.telegram_gonder([b], min_tier="S", gonder_fn=self._fn)
        self.assertEqual(self.gonderilen, [])

    def test_gonderim_hatasi_cokmez(self):
        def patlak(metin):
            raise RuntimeError("telegram yok")
        self.assertEqual(self.o.telegram_gonder([self._s_tier()], gonder_fn=patlak), 0)


class FutbolModeli(unittest.TestCase):
    def _oyun(self, pf, pa, gun):
        return {"ts": KO - gun * 86400, "pf": pf, "pa": pa, "lig": "L", "ad": "T", "rakip": "R",
                "sets": [], "sets_opp": []}

    def test_ornek_yetersizse_model_yok(self):
        self.assertIsNone(MO.futbol_model([self._oyun(1, 0, 3)], [self._oyun(1, 0, 3)]))

    def test_ev_avantaji_lam_mu_farkina_yansir(self):
        gh = [self._oyun(2, 1, 3 * (i + 1)) for i in range(6)]
        ga = [self._oyun(2, 1, 3 * (i + 1)) for i in range(6)]
        mod = MO.futbol_model(gh, ga)
        self.assertGreater(mod["lam"], mod["mu"])                       # ayni form, ev sahibi avantajli
        self.assertEqual(mod["kind"], "poisson")

    def test_poisson_olasiliklari_1x2_toplami_bir(self):
        gh = [self._oyun(2, 1, 3 * (i + 1)) for i in range(8)]
        ga = [self._oyun(1, 1, 3 * (i + 1)) for i in range(8)]
        mod = MO.futbol_model(gh, ga)
        p = MO.M.OLAS["poisson"](mod, "win", None, None, False, {"1", "0", "2"})
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)

    def test_saglayici_hatada_bos_liste_doner(self):
        """Graceful degradation: ag/bulten patlarsa saglayici bos liste doner, tara() surer."""
        eski = MO.F._get_session
        MO.F._get_session = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ag yok"))
        try:
            self.assertEqual(MO.saglayici_futbol(), [])
        finally:
            MO.F._get_session = eski


class TaraEntegrasyon(unittest.TestCase):
    def test_bir_kaynak_patlarsa_diger_kaynak_devam_eder(self):
        eski = dict(MO.SAGLAYICI)
        MO.SAGLAYICI["patlak"] = lambda **kw: (_ for _ in ()).throw(RuntimeError("bozuk"))
        MO.SAGLAYICI["sahte"] = lambda **kw: [satir(ilk_oran=2.00, oran=1.60, ev_yuzde=5.0,
                                                    guven=10, risk="Düşük", n=(10, 10))]
        try:
            sonuc, ozet = MO.tara(("patlak", "sahte"))
            self.assertEqual(len(sonuc), 1)
            self.assertEqual(ozet["patlak"], 0)
            self.assertEqual(ozet["sahte"], 1)
        finally:
            MO.SAGLAYICI.clear()
            MO.SAGLAYICI.update(eski)

    def test_multi_sport_analyzer_degistirilmedi(self):
        """Orkestrator M'yi sarmalar; M.SPORLAR'a futbol EKLENMEMIS olmali (M.tara('hepsi') bozulmaz)."""
        self.assertNotIn("futbol", MO.M.SPORLAR)
        self.assertEqual(set(MO.M.SPORLAR), {"basketbol", "hokey", "tenis"})


if __name__ == "__main__":
    unittest.main()


class FutbolGuvenRisk(unittest.TestCase):
    def test_guven_orneklem_ve_adayla_artar(self):
        mod_az, mod_cok = {"n": (5, 5)}, {"n": (10, 10)}
        az = MO.futbol_guven_risk(mod_az, {"ev": -0.10, "rel": 0.05}, 1)[0]
        cok = MO.futbol_guven_risk(mod_cok, {"ev": -0.02, "rel": 0.05}, 6)[0]
        self.assertGreater(cok, az)
        self.assertLessEqual(cok, 5)                                     # tavan 5 (kalibre degil)

    def test_risk_orneklem_ve_sapmayla_yukselir(self):
        self.assertEqual(MO.futbol_guven_risk({"n": (10, 10)}, {"ev": -0.02, "rel": 0.05}, 6)[1], "Düşük")
        self.assertEqual(MO.futbol_guven_risk({"n": (6, 6)}, {"ev": -0.02, "rel": 0.05}, 6)[1], "Orta")
        self.assertEqual(MO.futbol_guven_risk({"n": (6, 6)}, {"ev": -0.02, "rel": 0.30}, 6)[1], "Yüksek")

    def test_bozuk_girdide_cokmez(self):
        g, r, _ = MO.futbol_guven_risk({"n": "bozuk"}, {"ev": None, "rel": None}, 0)
        self.assertEqual((g, r), (1, "Yüksek"))

    def test_futbol_satiri_artik_anlamli_bwm_puani_alabilir(self):
        o = MO.MasterOrchestrator()
        zayif = o.puan_bwm(satir(guven=None, risk=None, n=(10, 10)))[0]
        gucluu = o.puan_bwm(satir(guven=5, risk="Düşük", n=(10, 10)))[0]
        self.assertGreater(gucluu, zayif)
        self.assertGreaterEqual(gucluu, 20)
