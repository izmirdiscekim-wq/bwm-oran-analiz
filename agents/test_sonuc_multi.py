# -*- coding: utf-8 -*-
"""sonuc_multi regresyon testleri - AG CAGRISI YOK (feed/parse enjekte edilir), uretim verisine yazmaz."""
import io
import json
import os
import shutil
import tempfile
import unittest

import sonuc_multi as S


class PazarCozumu(unittest.TestCase):
    """coz(): pazar metni + nihai skor -> tuttu mu. Cozulemeyen pazar None DONER (elle sayilmaz)."""

    def test_mac_sonucu_takim_adiyla(self):
        self.assertTrue(S.coz("Maç Sonucu: HC Lugano", "HC Lugano", "ZSC Lions", 3, 2))
        self.assertFalse(S.coz("Maç Sonucu: ZSC Lions", "HC Lugano", "ZSC Lions", 3, 2))

    def test_mac_sonucu_uzatma_dahil_varyantlari(self):
        for ad in ("Maç Sonucu (Uzt.Dahil)", "Maç Sonucu (Uz. Dahil)"):
            self.assertTrue(S.coz(ad + ": Brooklyn", "Brooklyn", "Philadelphia", 101, 93), ad)

    def test_mac_sonucu_beraberlikte_iki_taraf_da_kaybeder(self):
        self.assertFalse(S.coz("Maç Sonucu: A", "A", "B", 2, 2))
        self.assertFalse(S.coz("Maç Sonucu: B", "A", "B", 2, 2))

    def test_handikap_secilen_tarafa_eklenir(self):
        # KT Sonicboom +3.5: 82-81 kaybetse bile handikapla kazanir
        self.assertTrue(S.coz("Handikaplı Maç Sonucu (Uzt.Dahil) 3.5: KT Sonicboom",
                              "KT Sonicboom", "Goyang Orions", 81, 82))
        # -3.5 ise 1 sayi fark yetmez
        self.assertFalse(S.coz("Handikaplı Maç Sonucu (Uzt.Dahil) -3.5: KT Sonicboom",
                               "KT Sonicboom", "Goyang Orions", 82, 81))

    def test_handikap_deplasman_tarafi(self):
        self.assertTrue(S.coz("Handikaplı Maç Sonucu 1.5: Dallas Stars",
                              "Buffalo Sabres", "Dallas Stars", 2, 2))

    def test_tenis_set_handikabi(self):
        # Mannarino -1.5 set: 2-0 kazanmali
        self.assertTrue(S.coz("Handikaplı Maç Sonucu (Set) -1.5: Mannarino,A.",
                              "Mannarino,A.", "Cobolli F.", 2, 0))
        self.assertFalse(S.coz("Handikaplı Maç Sonucu (Set) -1.5: Mannarino,A.",
                               "Mannarino,A.", "Cobolli F.", 2, 1))

    def test_toplam_alt_ust(self):
        self.assertTrue(S.coz("Toplam Sayı Alt/Üst (Uzt.Dahil) 140.5: Alt", "A", "B", 69, 67))
        self.assertFalse(S.coz("Toplam Sayı Alt/Üst (Uzt.Dahil) 140.5: Üst", "A", "B", 69, 67))
        self.assertTrue(S.coz("Altı/Üstü 5.5: Alt", "A", "B", 2, 2))      # hokey varyanti

    def test_takim_toplami(self):
        self.assertTrue(S.coz("Ev Sahibi Alt/Üst (Uzt.Dahil) 105.5: Alt", "A", "B", 101, 93))
        self.assertFalse(S.coz("Ev Sahibi Alt/Üst (Uzt.Dahil) 105.5: Üst", "A", "B", 101, 93))
        self.assertTrue(S.coz("Deplasman Altı/Üstü 3.5: Alt", "A", "B", 5, 2))

    def test_bilinmeyen_pazar_None_doner(self):
        self.assertIsNone(S.coz("İlk Periyodu Kim Kazanır: A", "A", "B", 3, 2))
        self.assertIsNone(S.coz("", "A", "B", 1, 0))
        self.assertIsNone(S.coz("Maç Sonucu: A", "A", "B", None, 2))



class AdEslemesi(unittest.TestCase):
    """12.10.2026: iddaa basketbolda SEHIR adi ("Indiana"), Flashscore tam kulup adi
    ("Indiana Pacers"). Duz oran 0.62-0.65 veriyordu -> 288 basketbol sinyalinin 144'u
    eslesemiyordu. Jeton kapsamasi eklendi; kadin/erkek karismasi ayrica engellendi."""

    def test_sehir_adi_tam_kulup_adiyla_eslesir(self):
        for kisa, uzun in (("Indiana", "Indiana Pacers"),
                           ("Oklahoma", "Oklahoma City Thunder"),
                           ("Memphis", "Memphis Grizzlies")):
            self.assertGreaterEqual(S._benzer(kisa, uzun), S.AD_BENZERLIK, kisa)

    def test_kadin_takimi_isareti_eslesir(self):
        self.assertGreaterEqual(S._benzer("Atlanta (K)", "Atlanta Dream W"), S.AD_BENZERLIK)
        self.assertGreaterEqual(S._benzer("New York (K)", "New York Liberty W"), S.AD_BENZERLIK)

    def test_kadin_takimi_erkek_takimiyla_ESLESMEZ(self):
        """Ayni sehrin iki takimi yuksek dizge orani verir; cinsiyet isareti uyusmazsa
        skor esik ALTINA cekilmeli - aksi halde 'New York (K)' Knicks ile eslesirdi (0.80)."""
        self.assertLess(S._benzer("New York (K)", "New York Knicks"), S.AD_BENZERLIK)

    def test_farkli_sehir_eslesmez(self):
        self.assertLess(S._benzer("Indiana", "Memphis Grizzlies"), S.AD_BENZERLIK)

    def test_backspace_tuzagi_regresyonu(self):
        """Yazim sirasinda r"\b" yerine backspace (chr(8)) yazilmisti: 'k' jetonu hic
        normalize edilmiyordu. _ad cikti kontrolu bunu bir daha kacirmamak icin."""
        self.assertEqual(S._ad("New York (K)"), "new york w")
        self.assertEqual(S._ad("Oklahoma City Thunder"), "oklahoma city thunder")


class Esleme(unittest.TestCase):
    SON = [{"id": "x1", "ts": 1_800_000_000, "ev": "Washington Capitals",
            "dep": "Pittsburgh Penguins", "sh": 4, "sa": 1}]

    def test_duz_eslesme(self):
        e = S.eslestir("Washington Capitals", "Pittsburgh Penguins", self.SON[0]["ts"], self.SON)
        self.assertEqual((e["sh"], e["sa"], e["cevrildi"]), (4, 1, False))

    def test_ters_eslesme_skoru_cevirir(self):
        """Bultende ev/deplasman ters yazilmissa skor da cevrilmeli - aksi halde sonuc ters okunur."""
        e = S.eslestir("Pittsburgh Penguins", "Washington Capitals", self.SON[0]["ts"], self.SON)
        self.assertEqual((e["sh"], e["sa"], e["cevrildi"]), (1, 4, True))

    def test_tenis_ad_bicimi_farki(self):
        son = [{"id": "t1", "ts": 1_800_000_000, "ev": "Mannarino A.", "dep": "Cobolli F.",
                "sh": 2, "sa": 0}]
        self.assertIsNotNone(S.eslestir("Mannarino,A.", "Cobolli F.", son[0]["ts"], son))

    def test_zaman_penceresi_disi_eslesmez(self):
        self.assertIsNone(S.eslestir("Washington Capitals", "Pittsburgh Penguins",
                                      self.SON[0]["ts"] + 5 * 3600, self.SON))

    def test_farkli_takim_eslesmez(self):
        self.assertIsNone(S.eslestir("Anaheim Ducks", "Edmonton Oilers",
                                      self.SON[0]["ts"], self.SON))


class GunSonuclari(unittest.TestCase):
    """gun_sonuclari: feed/parse enjekte edilir -> ag cagrisi yok."""

    def _feed(self, metin):
        return lambda s, ad: metin

    def test_yalniz_bitmis_mac_ve_okunabilir_skor(self):
        rows = [{"AA": "1", "AD": "1800000000", "AE": "A", "AF": "B", "AB": "3", "AG": "3", "AH": "1"},
                {"AA": "2", "AD": "1800000000", "AE": "C", "AF": "D", "AB": "1", "AG": "0", "AH": "0"},
                {"AA": "3", "AD": "1800000000", "AE": "E", "AF": "F", "AB": "3"}]
        out = S.gun_sonuclari(None, "hokey", -1, feed_fn=self._feed("x"), parse_fn=lambda t: rows)
        self.assertEqual([m["id"] for m in out], ["1"])        # bitmeyen ve skorsuz atlanir
        self.assertEqual((out[0]["sh"], out[0]["sa"]), (3, 1))

    def test_bilinmeyen_spor_bos_liste(self):
        self.assertEqual(S.gun_sonuclari(None, "futbol", -1,
                                          feed_fn=self._feed("x"), parse_fn=lambda t: []), [])


class Kapatma(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.eski = S.KAPALI
        S.KAPALI = os.path.join(self.dizin, "settled_multi.jsonl")

    def tearDown(self):
        S.KAPALI = self.eski
        shutil.rmtree(self.dizin, ignore_errors=True)

    def _sinyal(self, **ek):
        d = {"kaynak": "multi:hokey", "esd_ms": 1_800_000_000_000, "ev": "A Team - B Team",
             "pazar": "Maç Sonucu: A Team", "oran": 2.00, "tip": "IZLE", "karar": "İZLE"}
        d.update(ek)
        return d

    def _son(self):
        return {"hokey": [{"id": "h1", "ts": 1_800_000_000, "ev": "A Team", "dep": "B Team",
                           "sh": 3, "sa": 1}]}

    def _oku(self):
        return [json.loads(x) for x in io.open(S.KAPALI, encoding="utf-8") if x.strip()]

    def test_kapatir_ve_tuttu_dogru(self):
        o = S.kapat(sonuclar=self._son(), sinyaller=[self._sinyal()])
        self.assertEqual((o["aday"], o["yeni"]), (1, 1))
        k = self._oku()[0]
        self.assertTrue(k["tuttu"])
        self.assertEqual(k["skor"], [3, 1])

    def test_ayni_sinyal_iki_turda_tek_kez_yazilir(self):
        S.kapat(sonuclar=self._son(), sinyaller=[self._sinyal()])
        o2 = S.kapat(sonuclar=self._son(), sinyaller=[self._sinyal()])
        self.assertEqual((o2["yeni"], o2["zaten"]), (0, 1))
        self.assertEqual(len(self._oku()), 1)                  # orneklem sismedi

    def test_eslesmeyen_sinyal_yazilmaz(self):
        o = S.kapat(sonuclar={"hokey": []}, sinyaller=[self._sinyal()])
        self.assertEqual((o["yeni"], o["eslesmedi"]), (0, 1))
        self.assertFalse(os.path.exists(S.KAPALI))

    def test_cozulemeyen_pazar_tutmadi_SAYILMAZ(self):
        o = S.kapat(sonuclar=self._son(),
                    sinyaller=[self._sinyal(pazar="İlk Periyodu Kim Kazanır: A Team")])
        self.assertEqual((o["yeni"], o["pazar_cozulmedi"]), (0, 1))

    def test_rapor_bos_log_cokmez(self):
        self.assertEqual(S.rapor(yazdir=False), {})

    def test_ozet_hesabi(self):
        o = S._oz([{"tuttu": True, "oran": 2.0}, {"tuttu": False, "oran": 2.0}])
        self.assertEqual(o["n"], 2)
        self.assertAlmostEqual(o["isabet"], 50.0)
        self.assertAlmostEqual(o["roi"], 0.0)


if __name__ == "__main__":
    unittest.main()
