# -*- coding: utf-8 -*-
"""aksiyon.py (ne oynanmali yonu) + balina mesaj sablonu regresyon testleri.

Render build'i bu testleri kosturur: aksiyon.py repoya girmeden balina.py push edilirse
ya da esleme tablosu bozulursa build burada durur, canliya hatali surum cikmaz.
"""
import time
import unittest

import aksiyon as AKS
import balina as B


class PazarEtiketi(unittest.TestCase):
    def test_handikap_cizgisini_parantezde_gosterir(self):
        self.assertEqual(AKS.pazar_etiketi("Handikaplı Maç Sonucu 1", "2"),
                         "Handikaplı Maç Sonucu 2 (1:0)")

    def test_negatif_handikap(self):
        self.assertEqual(AKS.pazar_etiketi("Handikaplı Maç Sonucu -1", "1"),
                         "Handikaplı Maç Sonucu 1 (0:1)")

    def test_duz_pazar_oldugu_gibi_birlesir(self):
        self.assertEqual(AKS.pazar_etiketi("Alt/Üst 2.5", "Alt"), "Alt/Üst 2.5 Alt")


class TersYon(unittest.TestCase):
    def test_oran_yukselirse_karsi_taraf_onerilir(self):
        yon, gerekce = AKS.oneri("Handikaplı Maç Sonucu 1", "2", +2.38, "Gnistan", "Inter Turku")
        self.assertEqual(yon, "HMS 1 (Gnistan +1) veya Çifte Şans 1X")
        self.assertIn("Gnistan", gerekce)

    def test_alt_ust_ters_cevrilir(self):
        yon, _ = AKS.oneri("Deplasman Alt/Üst 2.5", "Alt", +0.45, "A", "B")
        self.assertEqual(yon, "Deplasman Alt/Üst 2.5 Üst")

    def test_mac_sonucu_cifte_sans_alternatifi_verir(self):
        yon, _ = AKS.oneri("Maç Sonucu", "1", +0.30, "Ev", "Dep")
        self.assertEqual(yon, "MS 2 veya Çifte Şans X2")

    def test_karsilikli_gol(self):
        yon, _ = AKS.oneri("Karşılıklı Gol", "Var", +0.31, "A", "B")
        self.assertEqual(yon, "Karşılıklı Gol Yok")

    def test_cifte_sans_tek_tarafa_doner(self):
        yon, _ = AKS.oneri("Çifte Şans", "1X", +0.40, "A", "B")
        self.assertEqual(yon, "MS 2")

    def test_ilk_yari_oneki(self):
        yon, _ = AKS.oneri("İlk Yarı Sonucu", "1", +0.40, "Ev", "Dep")
        self.assertEqual(yon, "İY 2 veya İY Çifte Şans X2")

    def test_oran_duserse_ayni_yon_ama_uyarili(self):
        yon, gerekce = AKS.oneri("Alt/Üst 1.5", "Üst", -0.22, "A", "B")
        self.assertEqual(yon, "Alt/Üst 1.5 Üst")
        self.assertIn("fiyatın bir kısmı gitti", gerekce)

    def test_kombine_pazarda_yon_verilmez(self):
        yon, gerekce = AKS.oneri("Maç Sonucu ve Alt/Üst 1.5", "1 ve Üst", +0.50, "A", "B")
        self.assertIsNone(yon)
        self.assertIn("girmeyin", gerekce)

    def test_guvenli_alternatif(self):
        self.assertEqual(AKS.guvenli_alternatif("1"), "Çifte Şans 1X")
        self.assertEqual(AKS.guvenli_alternatif("2"), "Çifte Şans X2")
        self.assertIsNone(AKS.guvenli_alternatif("Alt"))


class BalinaSablonu(unittest.TestCase):
    """balina.py'nin aksiyon modulunu GERCEKTEN kullandigini dogrular (import kopmasina karsi)."""

    def _kayit(self):
        return {"esd_ms": int((time.time() + 9000) * 1000), "hn": "Inter", "an": "Bologna",
                "lig": "Serie A", "sec": "1", "oran_n": 2.10, "p_w": 0.54, "dp_w": 3.2,
                "dp_n": 0.2, "n_kitap": 11, "ev": 0.134, "dk_kala": 150, "tip": "AKIS",
                "kitap": "iddaa", "marj_n": 0.148, "pin": True, "sapma": 0.02, "dp_w_onceki": 2.1}

    def test_mesaj_mac_saati_ve_aksiyon_icerir(self):
        m = B._mesaj(self._kayit())
        self.assertIn("⏰ Maç Saati:", m)
        self.assertIn("🎯 Aksiyon / Önerilen Yön:", m)
        self.assertIn("Çifte Şans 1X", m)        # guvenli_alternatif entegrasyonu

    def test_mesaj_saat_ve_kalan_sureyi_yazar(self):
        m = B._mesaj(self._kayit())
        self.assertIn("TSİ", m)
        self.assertIn("kaldı", m)


if __name__ == "__main__":
    unittest.main()
