# -*- coding: utf-8 -*-
"""Multi-Spor Telegram bildirim filtresi: KOK NEDEN olcumu + YENI KATI FILTRE backtest'i.

AMAC: 08-11.10.2026 arasinda Telegram'a 286 mesaj gitti (gunde ~95-130, saatte 14'e kadar).
Bu betik ESKI kapiyi (`if r["bayrak"]`) ve ONERILEN yeni kapiyi ayni gecmis veri uzerinde
calistirip mesaj sayisindaki azalmayi ve her elenen satirin SEBEBINI olcer.

URETIM KODUNA DOKUNMAZ - yeni kapi burada bagimsiz bir fonksiyon olarak durur (bildirime_deger).
Onay verilirse multi_sport_analyzer._telegram_gonder'e tasinacak.

VERI: data/history/sinyal_log.jsonl (bayrakli her sinyal, oynanmasa bile yazilir) +
      data/multi/telegram_gonderilen.json (gercekten gonderilen mesaj anahtarlari).
AG CAGRISI YOK (Bolum A). Bolum B canli bulteni tarar, yalniz --canli ile.
"""
import collections
import io
import json
import os
import sys
import unittest

DIZIN = os.path.dirname(os.path.abspath(__file__))
SINYAL_LOG = os.path.join(DIZIN, "data", "history", "sinyal_log.jsonl")
GONDERILEN = os.path.join(DIZIN, "data", "multi", "telegram_gonderilen.json")

# ---------------------------------------------------------------- KAPI (tek dogruluk kaynagi)
# Kural govdesi URETIMDE yasiyor: multi_sport_analyzer.bildirime_deger. Burada kopyalanmaz -
# kopya olsa biri degisince digeri sessizce yanlis olurdu. Esikler de COEF'ten okunur.
import multi_sport_analyzer as M

bildirime_deger = M.bildirime_deger
EV_BILDIRIM = M.COEF["EV_SINYAL"]
KIRILIM_ESIK = M.COEF["KIRILIM"]
MIN_ORAN = M.COEF["MIN_ORAN"]
BILDIRIM_UFKU_SAAT = M.BILDIRIM_UFKU_SAAT


def eski_kapi(r):
    """Suan uretimde olan kapi: multi_sport_analyzer._telegram_gonder -> `for r in bayrakli`
    (bayrakli = [r for r in sonuclar if r["bayrak"]]). Yani bayragi olan HER satir gider."""
    return bool(r.get("tip") or r.get("bayrak"))


# ---------------------------------------------------------------- Bolum A: gecmis veri
def _oku_sinyal():
    """sinyal_log satirlarini kapinin bekledigi semaya cevirir.

    DIKKAT: log'daki 'ts' sinyalin YAZILDIGI an, mac saati DEGIL (mac saati 'esd_ms').
    Normalize etmezsek 7. kural (bildirim ufku) yanlis alani okur. 'kayit_ts' olarak saklanir.
    """
    out = []
    if not os.path.exists(SINYAL_LOG):
        return out
    for ln in io.open(SINYAL_LOG, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            k = json.loads(ln)
        except Exception:
            continue
        if not str(k.get("kaynak", "")).startswith("multi:"):
            continue
        k["kayit_ts"] = k.get("ts")
        if isinstance(k.get("esd_ms"), (int, float)):
            k["ts"] = k["esd_ms"] / 1000.0
        out.append(k)
    return out


def rapor():
    kayit = _oku_sinyal()
    if not kayit:
        print("sinyal_log.jsonl'de multi-spor kaydi yok - backtest yapilamaz.")
        return
    print("=" * 74)
    print("BOLUM A - GECMIS VERI BACKTEST (n=%d bayrakli multi-spor sinyali)" % len(kayit))
    print("=" * 74)

    gun = collections.Counter(k.get("gun") for k in kayit)
    print("\nGune gore bayrakli sinyal:")
    for g in sorted(gun):
        print("  %s : %4d" % (g, gun[g]))

    if os.path.exists(GONDERILEN):
        try:
            d = json.load(io.open(GONDERILEN, encoding="utf-8"))
            print("\nGERCEKTEN GONDERILEN (mukerrer kilidi dosyasi, 2 gun omurlu): %d mesaj" % len(d))
        except Exception:
            pass

    eski = [k for k in kayit if eski_kapi(k)]
    yeni, sebepler = [], collections.Counter()
    for k in kayit:
        # Her satir KENDI yazildigi ana gore degerlendirilir (ufuk kurali icin dogru referans);
        # bugunun saatiyle bakmak gecmis maclarin tamamini "coktan basladi" yapardi.
        ok, sebep = bildirime_deger(k, simdi=k.get("kayit_ts"))
        if ok:
            yeni.append(k)
        else:
            sebepler[sebep] += 1

    print("\n" + "-" * 74)
    print("ESKI KAPI  : %4d mesaj" % len(eski))
    print("YENI KAPI  : %4d mesaj" % len(yeni))
    if eski:
        print("AZALMA     : %.1f%% (%d -> %d)" % (100.0 * (len(eski) - len(yeni)) / len(eski),
                                                   len(eski), len(yeni)))
    print("-" * 74)

    print("\nELENME SEBEPLERI (baskin sebebe gore):")
    for s, n in sebepler.most_common():
        print("  %4d  %s" % (n, s))

    ev = [k.get("ev_yuzde") for k in kayit if isinstance(k.get("ev_yuzde"), (int, float))]
    if ev:
        poz = sum(1 for x in ev if x > 0)
        print("\nGONDERILEN TUM MESAJLARIN EV DAGILIMI (eski kapi):")
        print("  n=%d | EV>0: %d (%.1f%%) | ortalama EV: %.2f%% | en iyi: %+.1f%%"
              % (len(ev), poz, 100.0 * poz / len(ev), sum(ev) / len(ev), max(ev)))
        print("  -> Telefonu caldiran mesajlarin %%%.1f'i kendi matematigine gore ZARARDA."
              % (100.0 * (len(ev) - poz) / len(ev)))

    tip = collections.Counter(k.get("tip") for k in kayit)
    karar = collections.Counter(k.get("karar") for k in kayit)
    print("\nBAYRAK: %s" % dict(tip))
    print("KARAR : %s" % dict(karar))
    print("  -> 'SINYAL' (tek OYNA adayi olan bayrak) sayisi: %d" % tip.get("SINYAL", 0))

    print("\nNOT (durustluk): gecmis log'da 'dusus' (kendi pazarindaki oran hareketi) ve 'guven'")
    print("alanlari YAZILMIYORDU. Bu yuzden KIRILIM yon kurali (madde 5) gecmis veride")
    print("'dogrulanamaz' olarak elenir; gercek etkisi Bolum B'de canli bultende olculur.")


# ---------------------------------------------------------------- Bolum B: canli olcum
def canli_olc(n=40, saat=12.0):
    """Canli bultende: bayrak=KIRILIM olan satirlarin kaci KENDI pazarinda lehte kirilmis?
    Eski kapinin `or kirilimlar` dalinin yarattigi yanlis-etiket oranini olcer."""
    import multi_sport_analyzer as M
    sonuclar, ozet = M.tara("hepsi", saat=saat, n=n, log=False, bildir=False)
    bayrakli = [r for r in sonuclar if r["bayrak"]]
    print("=" * 74)
    print("BOLUM B - CANLI OLCUM (taranan ozet: %s)" % {k: v.get("taranan") for k, v in ozet.items()})
    print("=" * 74)
    print("bayrakli satir: %d" % len(bayrakli))
    kir = [r for r in bayrakli if r["bayrak"] == "KIRILIM"]
    kendi_lehte = [r for r in kir if r.get("dusus") is not None and r["dusus"] <= -KIRILIM_ESIK]
    yanlis = [r for r in kir if r not in kendi_lehte]
    print("KIRILIM damgali          : %d" % len(kir))
    print("  kendi pazarinda LEHTE  : %d" % len(kendi_lehte))
    print("  YANLIS ETIKET          : %d" % len(yanlis))
    for r in yanlis[:8]:
        print("    %s | %s | %.2f -> %.2f (%+.1f%%)"
              % (r["spor"], r["mac"][:38], r["ilk"] or 0, r["oran"], (r["dusus"] or 0) * 100))
    eski = len(bayrakli)
    yeni = [r for r in bayrakli if bildirime_deger(r)[0]]
    print("\nESKI KAPI: %d mesaj | YENI KAPI: %d mesaj" % (eski, len(yeni)))
    ozet_ad = M._ozet_adaylari(sonuclar)
    print("GUNLUK OZETE girecek aday: %d (tek mesaj)" % len(ozet_ad))


# ---------------------------------------------------------------- testler
class YeniFiltre(unittest.TestCase):
    SIMDI = 1_800_000_000.0

    def _r(self, **ek):
        """Uretim semasi: bayrak/ev(kesir)/ts. Mac SIMDI+2 saat sonra (ufuk icinde)."""
        d = {"bayrak": "SINYAL", "karar": "OYNA", "oran": 1.80, "risk": "Düşük",
             "ev": 0.03, "dusus": -0.12, "ts": self.SIMDI + 2 * 3600}
        d.update(ek)
        return d

    def _ok(self, **ek):
        return bildirime_deger(self._r(**ek), simdi=self.SIMDI)

    def test_temiz_sinyal_gecer(self):
        self.assertTrue(self._ok()[0])

    def test_pas_karari_gonderilmez(self):
        self.assertFalse(self._ok(karar="PAS")[0])

    def test_izle_bayragi_gonderilmez(self):
        ok, sebep = self._ok(bayrak="IZLE", ev=-0.09)
        self.assertFalse(ok)
        self.assertIn("IZLE", sebep)

    def test_kirilim_kendi_pazarinda_lehte_gecer(self):
        kk = {"ilk": 2.00, "oran": 1.78, "dusus": -0.11}
        self.assertTrue(self._ok(bayrak="KIRILIM", ev=-0.04, kirilim_kendi=kk)[0])

    def test_kirilim_hareketsizse_gonderilmez(self):
        """Gercek ornek: KT Sonicboom 1.63 -> 1.63 (+0.0%) KIRILIM damgaliydi."""
        ok, sebep = self._ok(bayrak="KIRILIM", dusus=0.0, kirilim_kendi=None)
        self.assertFalse(ok)
        self.assertIn("esik altinda", sebep)

    def test_kirilim_oran_yukseldiyse_gonderilmez(self):
        """Gercek ornek: Barrie Colts 1.65 -> 1.72 (+4.2%) KIRILIM damgaliydi."""
        ok, sebep = self._ok(bayrak="KIRILIM", dusus=0.042, kirilim_kendi=None)
        self.assertFalse(ok)
        self.assertIn("lehte degil", sebep)

    def test_kirilim_esik_altindaki_dusus_gonderilmez(self):
        """Gercek ornek: Ottawa 1.87 -> 1.73 (-7.5%) - yon dogru ama %10 esiginin ALTINDA."""
        ok, sebep = self._ok(bayrak="KIRILIM", dusus=-0.075, kirilim_kendi=None)
        self.assertFalse(ok)
        self.assertIn("esik altinda", sebep)

    def test_kirilim_dususu_bilinmiyorsa_gonderilmez(self):
        self.assertFalse(self._ok(bayrak="KIRILIM", dusus=None, kirilim_kendi=None)[0])

    def test_dusuk_oran_gonderilmez(self):
        self.assertFalse(self._ok(oran=1.20)[0])

    def test_yuksek_risk_gonderilmez(self):
        self.assertFalse(self._ok(risk="Yüksek")[0])

    def test_sinyal_ev_esigi_altinda_gonderilmez(self):
        self.assertFalse(self._ok(ev=0.01)[0])

    def test_uzak_mac_ertelenir(self):
        ok, sebep = self._ok(ts=self.SIMDI + 10 * 3600)
        self.assertFalse(ok)
        self.assertIn("ERTELENDI", sebep)

    def test_log_semasi_da_kabul_edilir(self):
        """Backtest sinyal_log satirlarini (tip/ev_yuzde/dusus) verir - ayni kapi okumali."""
        satir = {"tip": "IZLE", "karar": "İZLE", "oran": 1.7, "risk": "Orta", "ev_yuzde": -9.0}
        self.assertFalse(bildirime_deger(satir, simdi=self.SIMDI)[0])

    def test_esikler_gevsetilmedi(self):
        """COEF ile ayni kalmali - bildirim kapisi esik GEVSETMEZ, sikilastirir."""
        self.assertEqual(EV_BILDIRIM, M.COEF["EV_SINYAL"])
        self.assertEqual(KIRILIM_ESIK, M.COEF["KIRILIM"])
        self.assertEqual(MIN_ORAN, M.COEF["MIN_ORAN"])

    def test_bozuk_satir_cokmez(self):
        for bozuk in ({}, {"bayrak": None}, {"bayrak": "KIRILIM"}, {"bayrak": "SINYAL", "oran": "x"},
                      {"bayrak": "SINYAL", "ev": None, "ts": "x"}):
            self.assertIsInstance(bildirime_deger(bozuk), tuple)


class GunlukOzet(unittest.TestCase):
    """gunluk_ozet_gonder - AG CAGRISI YOK (gonder_fn enjekte edilir)."""

    def setUp(self):
        import msa_kaynak as K
        self.K = K
        self.eski, K.VERI = K.VERI, __import__("tempfile").mkdtemp()
        self.gonderilen = []

    def tearDown(self):
        __import__("shutil").rmtree(self.K.VERI, ignore_errors=True)
        self.K.VERI = self.eski

    def _r(self, **ek):
        d = {"spor": "hokey", "ts": 1_800_000_000.0, "mac": "A - B", "pick": "Maç Sonucu: A",
             "oran": 1.90, "ev": -0.05, "risk": "Orta", "bayrak": "IZLE", "karar": "İZLE"}
        d.update(ek)
        return d

    def test_tek_mesaj_gonderir_ve_gun_icinde_tekrarlamaz(self):
        n = M.gunluk_ozet_gonder([self._r(), self._r(mac="C - D")],
                                  gonder_fn=self.gonderilen.append, gun_etiketi="2026-10-11")
        self.assertEqual((n, len(self.gonderilen)), (1, 1))
        self.assertIn("A - B", self.gonderilen[0])
        self.assertIn("C - D", self.gonderilen[0])
        n2 = M.gunluk_ozet_gonder([self._r()], gonder_fn=self.gonderilen.append,
                                   gun_etiketi="2026-10-11")
        self.assertEqual((n2, len(self.gonderilen)), (0, 1))          # ayni gun ikinci mesaj YOK

    def test_ozet_oyna_onerisi_olmadigini_yazar(self):
        M.gunluk_ozet_gonder([self._r()], gonder_fn=self.gonderilen.append, gun_etiketi="G")
        self.assertIn("OYNA önerisi DEĞİL", self.gonderilen[0])

    def test_pas_ve_yuksek_risk_ozete_girmez(self):
        ad = M._ozet_adaylari([self._r(karar="PAS"), self._r(risk="Yüksek"), self._r(oran=1.10)])
        self.assertEqual(ad, [])

    def test_aday_yoksa_mesaj_gonderilmez(self):
        self.assertEqual(M.gunluk_ozet_gonder([], gonder_fn=self.gonderilen.append), 0)
        self.assertEqual(self.gonderilen, [])

    def test_en_iyi_ev_basta(self):
        ad = M._ozet_adaylari([self._r(ev=-0.08, mac="kotu"), self._r(ev=-0.01, mac="iyi")])
        self.assertEqual(ad[0]["mac"], "iyi")


if __name__ == "__main__":
    if "--rapor" in sys.argv:
        rapor()
    elif "--canli" in sys.argv:
        canli_olc()
    else:
        unittest.main()
