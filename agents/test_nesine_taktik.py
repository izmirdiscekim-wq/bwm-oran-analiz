"""nesine.py + taktik.py birim testleri (ağsız). Çalıştır: python -m unittest test_nesine_taktik -v"""
import os, sys, tempfile, time, unittest, argparse, io, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nesine as N
import taktik as T

BASLIK = "# test\n@kod ozel6 = t43/4  # Toplam Gol 6+\n"


def ev(mk, esd=None):
    return {"esd": esd or time.time() + 3600, "esd_ms": 0, "hn": "A", "an": "B", "lig": "L", "mk": mk}


def mk(t, o, sov=0.0):
    return {"t": t, "sov": sov, "st": 0, "o": {str(i + 1): v for i, v in enumerate(o)}}


class EtiketTests(unittest.TestCase):
    def test_etiketler(self):
        self.assertEqual(N._etiket("Toplam Gol", 4), ["0-1", "2-3", "4-5", "6+"])
        self.assertEqual(N._etiket("1. Yarı ve 2. Yarıda Karşılıklı Gol Olur", 4)[2], "Var/Var")
        self.assertEqual(N._etiket("İlk Golü Hangi Takım Atar", 3), ["Ev", "Yok", "Dep"])   # 'İ' Türkçe küçük harf hatası olmasın
        self.assertEqual(N._etiket("Her İki Yarıda da Üst 1.5", 2), ["Evet", "Hayır"])
        self.assertEqual(len(N._etiket("1. Yarı Sonucu ve Alt/Üst 1.5", 6)), 6)
        self.assertEqual(N._etiket("Alt/Üst 2.5", 2), ["Alt", "Üst"])
        self.assertIsNone(N._etiket("Hangi Takım Kaç Farkla Kazanır?", 7))
        self.assertEqual(N._norm("Yarıda Şampiyonlar"), "yarida sampiyonlar")

    def test_sayi_filtreleri(self):
        from datetime import datetime
        t = datetime(2026, 9, 21, 23, 30, tzinfo=N.TR).timestamp()
        self.assertTrue(N._saat_ok(t, "20:00-23:59") and N._saat_ok(t, "22:00-01:00"))      # gece yarısını aşan aralık
        self.assertFalse(N._saat_ok(t, "10:00-12:00"))
        self.assertTrue(N._gun_ok(t, "hepsi") and N._gun_ok(t, "2026-09-21") and N._gun_ok(t, "21.09") and not N._gun_ok(t, "22.09"))

    def test_eski_kopya(self):
        gecmis = [ev([], esd=time.time() - 100) for _ in range(5)]
        self.assertTrue(N._eski_mi(10, gecmis, 0))                              # başlamış çok maç: eski
        self.assertTrue(N._eski_mi(5, [ev([])], 10))                            # daha düşük sürüm: eski
        self.assertFalse(N._eski_mi(10, [ev([])], 10))


class KuralTests(unittest.TestCase):
    def test_aralik_ve_sinir(self):
        self.assertEqual(T._aralik("4,20-5,10"), (4.2, 5.1))
        self.assertEqual(T._aralik(">=4.2")[0], 4.2)
        self.assertEqual(T._aralik("=7.00"), (7.0, 7.0))
        with self.assertRaises(ValueError):
            T._aralik("5.10-4.20")
        k = T.kural_coz("u45 4,20-5,10")
        self.assertTrue(k.gecer(4.2) and k.gecer(5.1) and not k.gecer(5.11) and not k.gecer(None))

    def test_kod_ve_hatalar(self):
        self.assertEqual(T._kod_coz("t43/4"), (43, None, 4))
        self.assertEqual(T._kod_coz("tg6"), (43, None, 4))
        self.assertTrue(T.kural_coz("iyskor11@ilk 8.00-8.92").ilk)
        with self.assertRaises(ValueError):
            T.kural_coz("yokboyle 1-2")
        with self.assertRaises(ValueError):
            T.kural_coz("u45")

    def test_degerlendir_ve_basari(self):
        e = ev([mk(155, [1.0, 4.5], 4.5), mk(43, [3, 2, 3.0, 8.6])])
        ks = [T.kural_coz("u45 4.2-5.1"), T.kural_coz("tg45 2.8-3.1"), T.kural_coz("tg6 8.6-8.84")]
        self.assertEqual([ok for _, ok in T.degerlendir(e, ks)], [True, True, True])
        self.assertFalse(T.degerlendir(ev([mk(43, [3, 2, 3.0, 8.6])]), ks)[0][1])   # pazar açık değil -> geçmez
        self.assertTrue(T.basari("top>=5", {"ms": [3, 2], "iy": [1, 1]}))
        self.assertFalse(T.basari("iyev==1 and iydep==1", {"ms": [3, 2], "iy": [2, 0]}))
        self.assertIsNone(T.basari("top>=5", None))
        self.assertIsNone(T.basari("iykg", {"ms": [1, 0], "iy": [None, None]}))

    def test_veya_araliklar(self):
        k = T.kural_coz("msalt 4.45-4.55|4.70-4.80")
        self.assertEqual(len(k.araliklar), 2)
        self.assertTrue(k.gecer(4.50) and k.gecer(4.75) and not k.gecer(4.60) and not k.gecer(5.0))
        keq = T.kural_coz("tg6 =12.00")               # '=X' -> ±0.05 tolerans (oranlar tam basılmayabilir)
        self.assertTrue(keq.gecer(11.96) and keq.gecer(12.04) and not keq.gecer(11.90))
        self.assertEqual(T._aralik("=7.00"), (7.0, 7.0))   # ham _aralik toleranssız kalır (test/dogruskor kullanır)

    def test_msfav_msalt(self):
        ev_fav = ev([mk(1, [1.65, 3.0, 4.75])])
        dep_fav = ev([mk(1, [4.75, 3.0, 1.65])])
        kf, ka = T.kural_coz("msfav 1.60-1.69"), T.kural_coz("msalt 4.45-4.55|4.70-4.80")
        self.assertTrue(T.degerlendir(ev_fav, [kf, ka])[0][1] and T.degerlendir(ev_fav, [kf, ka])[1][1])
        self.assertTrue(T.degerlendir(dep_fav, [kf, ka])[0][1] and T.degerlendir(dep_fav, [kf, ka])[1][1])
        self.assertIn("ms2", T._kural_etiket(ka, ev_fav["mk"]))     # ev favori -> altfavori (deplasman oranı) = ms2
        self.assertTrue(T.basari("(iydep>iyev and ev>dep) or (iyev>iydep and dep>ev)", {"ms": [2, 1], "iy": [0, 1]}))   # 2/1
        self.assertTrue(T.basari("(iydep>iyev and ev>dep) or (iyev>iydep and dep>ev)", {"ms": [1, 2], "iy": [1, 0]}))   # 1/2
        self.assertFalse(T.basari("(iydep>iyev and ev>dep) or (iyev>iydep and dep>ev)", {"ms": [2, 1], "iy": [1, 0]}))  # ters yok (1/1)

    def test_favoriye_gore_kural(self):
        # ev favori (o1<o2): iygolfav -> iy1u15 (459/1.5/4); dep favori: iy2u15 (459/1.5/6)
        ev_fav = ev([mk(1, [1.5, 3.0, 5.0]), mk(459, [0, 0, 0, 6.0, 0, 0], 1.5)])
        dep_fav = ev([mk(1, [5.0, 3.0, 1.5]), mk(459, [0, 0, 0, 0, 0, 6.0], 1.5)])
        esit = ev([mk(1, [2.0, 3.0, 2.0])])
        k = T.kural_coz("iygolfav 5.90-6.30")
        self.assertTrue(k.fav)
        self.assertEqual(T.degerlendir(ev_fav, [k]), [(6.0, True)])
        self.assertEqual(T.degerlendir(dep_fav, [k]), [(6.0, True)])
        self.assertEqual(T.degerlendir(esit, [k]), [(None, False)])          # favori belirsiz -> geçmez
        self.assertIn("iy1u15", T._kural_etiket(k, ev_fav["mk"]))
        self.assertIn("iy2u15", T._kural_etiket(k, dep_fav["mk"]))

    def test_kapanis_mi(self):
        simdi = time.time()
        self.assertTrue(T._kapanis_mi(simdi + 300, simdi))                # 5 dk sonra -> kapanışa yakın
        self.assertFalse(T._kapanis_mi(simdi + 3600, simdi))              # 1 sa sonra -> henüz kapanış değil
        self.assertFalse(T._kapanis_mi(simdi - 60, simdi))                # referans kickoff'tan sonraysa (arşivde geç çekim) kapanış sayılmaz
        # arşiv kullanımı: esd=kickoff, referans=son_cekim (şimdi değil)
        self.assertTrue(T._kapanis_mi(simdi, simdi - 15 * 60))            # son kayıt kickoff'tan 15 dk önce alınmış
        self.assertFalse(T._kapanis_mi(simdi, simdi - 3 * 3600))          # son kayıt kickoff'tan 3 sa önce alınmış


class KayitTests(unittest.TestCase):
    def ns(self, **kw):
        d = dict(ekle=None, degistir=None, kapat=None, ac=None, sil=None, hedef=None, oyna=None, basari=None, not_=None,
                 orijinal=None, kategori=None, spor=None, kural=None)
        d.update(kw)
        return argparse.Namespace(**d)

    def test_yasam_dongusu(self):
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "taktikler.txt")
            with open(yol, "w", encoding="utf-8") as f:
                f.write(BASLIK)
            msg, hedef = T.duzenle(self.ns(ekle="Deneme 4,5", hedef="4,5 Üst", oyna="u45", basari="top>=5", orijinal="video # metni",
                                           kural=["u45 4,20-5,10", "ozel6 8,42-8,60 # özel kod"]), yol)
            self.assertIn("KAYDEDİLDİ", msg)
            ust, tk = T.dosya_oku(yol)
            self.assertEqual((tk[0]["ad"], tk[0]["kategori"], len(tk[0]["kurallar"])), ("Deneme 4,5", "Oran Analizi", 2))
            self.assertEqual(tk[0]["kurallar"][1].t, 43)                        # @kod ile tanımlı özel kod çözüldü
            self.assertIn("video", tk[0]["orijinal"])
            with self.assertRaises(ValueError):                                 # aynı ad iki kez eklenmez
                T.duzenle(self.ns(ekle="deneme 4,5", kural=["u45 1-2"]), yol)
            T.duzenle(self.ns(ekle="Deneme 4,5 B", kural=["u45 1-2"]), yol)     # ad başkasının içinde geçse de eklenir
            T.duzenle(self.ns(degistir="Deneme 4,5 B", hedef="yeni hedef", kural=["tg6 8-9"]), yol)
            _, tk = T.dosya_oku(yol)
            b = [t for t in tk if t["ad"] == "Deneme 4,5 B"][0]
            self.assertEqual((b["hedef"], b["kurallar"][0].kod), ("yeni hedef", "tg6"))
            T.duzenle(self.ns(kapat="Deneme 4,5 B"), yol)
            self.assertFalse([t for t in T.dosya_oku(yol)[1] if t["ad"] == "Deneme 4,5 B"][0]["aktif"])
            T.duzenle(self.ns(ac="Deneme 4,5 B"), yol)
            T.duzenle(self.ns(sil="Deneme 4,5 B"), yol)
            self.assertEqual([t["ad"] for t in T.dosya_oku(yol)[1]], ["Deneme 4,5"])
            self.assertTrue(os.path.exists(yol + ".bak"))
            with open(yol, encoding="utf-8") as f:
                self.assertIn("@kod ozel6", f.read())      # başlık ve özel kodlar korunur
            with self.assertRaises(ValueError):
                T.duzenle(self.ns(ekle="Kuralsiz"), yol)

    def test_veya_aralik_yaz_oku_round_trip(self):
        """dosya_yaz'ın yazdığı VEYA aralık satırı dosya_oku ile UYARI vermeden geri okunmalı (regresyon: ' | ' boşluklu ayraç bozuyordu)."""
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "taktikler.txt")
            with open(yol, "w", encoding="utf-8") as f:
                f.write(BASLIK)
            T.duzenle(self.ns(ekle="VEYA Deneme", kural=["msfav 1.60-1.69", "msalt 4.45-4.55|4.70-4.80"]), yol)
            with open(yol, encoding="utf-8") as f:
                yazilan = f.read()
            self.assertIn("msalt", yazilan)
            self.assertNotIn(" | ", yazilan.split("orijinal", 1)[-1])   # boşluklu ayraç dosyaya yazılmamalı
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _, tk = T.dosya_oku(yol)
            self.assertEqual(buf.getvalue(), "")                        # UYARI yok = satır bozulmadan geri okundu
            k = [t for t in tk if t["ad"] == "VEYA Deneme"][0]["kurallar"][1]
            self.assertEqual(len(k.araliklar), 2)
            self.assertTrue(k.gecer(4.50) and k.gecer(4.75) and not k.gecer(4.60))


if __name__ == "__main__":
    unittest.main()
