"""
BWM tek giris noktasi (token-tasarruflu, KATMANLI). Her komut kisa cikti verir; ham JSON asla ekrana basilmaz.

KATMAN 1 - MAC BULMA        (find_matches.py)
  bul [--takim "A,B"] [--lig L] [--saat 20:30-00:00] [--tarih YYYY-MM-DD] [--n 40] [--canli]
KATMAN A - ARSIV / NESINE / TAKTIK  (kod: Oran analiz/.claude/agents; kural dosyasi skills/bwm-taktik-tara/taktikler.txt)
  nesine [--gun bugun|yarin|hepsi|YYYY-MM-DD] [--saat 20:00-23:59] [--takim "A,B"] [--lig L] [--acik iyms,skor,...] [--n 40] [--atla N] [--tam | --pazar "ust45,kg"]
                              baslamamis FUTBOL maclari + Nesine'de ACILMIS tum pazarlar/oranlar (kullanicinin oynadigi oran; iddaa API'den ~%4.5 dusuk)
  basketbol [ayni bayraklar]  baslamamis BASKETBOL maclari (MS 2 yonlu, Handikap, Toplam Sayi vb.); nesine ile ayni mimari, ayni komut seti
                              "sadece futbol" -> nesine; "sadece basketbol" -> basketbol; "tum maclar" -> ikisi de calistirilir
  taktik [--yakin] [--ad "4,5"] | --liste | --kurallar | --kodlar | --arsiv | --ekle/--degistir/--kapat/--ac/--sil "Ad" --kural "u45 4,20-5,10" ...
                              kullanicinin oran-araligi taktikleri (kategori "Oran Analizi"): kaydet + Nesine bultenindeki maclarda tara; varsayilan spor futbol,
                              "spor: basketbol" etiketli taktikler basketbol.py bultenini tarar (bkz. taktik.py SPOR_MODUL)
  arsiv <kapsam|mackolik|goster|sonuc|topla|disari|sablon|dogruskor|ust45|pdf-liste|pdf-aktar|...>   gecmis bulten/oran/sonuc arsivi (bulten_arsiv.py)
  senkron [--yaz]             skill/ajan belgelerini global + proje kopyalarina yansit
KATMAN 2 - VERI TOPLAMA     (collect_data.py; tek konu, birkac satir)
  oran <takim>                iddaa oranlari (1X2, CS, KG, Alt/Ust, marj)
  istat <takim>               Flashscore son 6 mac: xG, sut, korner, kart, H2H, hakem/stadyum
  sakat "A,B"                 Transfermarkt sakat/cezali
  hakem "Soyad B." | --takim  Transfermarkt hakem kart/penalti ortalamasi
KATMAN 3 - ANALIZ           (match_report.py; model + kurallar + sablon)
  mac [N] [--takim A] [--lig L] [--saat ..] [--tarih ..] [--tam]     sablon analizi (ozet tablo / tek macta tam)
  tara [N] [--api] [--dc]     tum bulten hizli tarama
KATMAN 7 - DERIN MAC ARASTIRMASI (derin.py; belirli mac/maclar sorulunca: fazla veri + iki model + piyasa mutabakati + oran hareketi + tum pazarlar)
  derin "Takim1,Takim2" [--tarih YYYY-MM-DD]     gun verilmezse bugunden itibaren ilk baslamamis mac
KATMAN 6 - GUN TARAMASI, TUM SPORLAR (multi_sport.py; futbol+basketbol+buz hokeyi+tenis+hentbol, en mantikliyi basa, ilk N)
  gun [YYYY-MM-DD] [--n 50] [--futbol-n 150] [--spor futbol,basketbol] [--goster]
KATMAN 5 - CANLI BAHIS      (live.py; oyun-durumu modeli + de-vig'li canli piyasa; kayit: data/live_log.jsonl)
  canli <takim> [--gecmis]    canli maci analiz et: OYNA / OYNAMA (siki filtreler, stake <= 0.5 Unit); tek mac = Adim Adim Canli Analiz + Nihai Tahmin blogu
  canli --hepsi [--bwm N]     tum canli maclar; --bwm N: en iyi N aday icin ayni blok (livefilters.py; test: test_livefilters.py)
KATMAN 8 - SONUC TAKIBI     (settle.py; OYNA/SPEKULATIF kayitlari + Flashscore skorlari -> isabet/ROI/kalibrasyon)
  sonuc [--gun 3]             kararlarin basarisini olc (data/bets_log.jsonl)
  kalibre [--gun 7]           model piyasadan fazla bilgi tasiyor mu? (pazar bazli log-loss, bootstrap GA; backtest_model.py)
KATMAN 10 - KONSENSUS REFERANS (oddsapi.py; The Odds API, anahtar data/odds_api.key; ucretsiz plan 500 kredi/ay)
  referans [--sa 24] [--lig 6] [--yenile] [--n 12]   iddaa 1X2 + Alt/Ust'u cok kitapli (Pinnacle agirlikli) vig-arindirilmis konsensusa karsi olc: EV_ref, DEGER
KATMAN 4 - RAPOR / KARAR    (kayitli rapordan, agsiz)
  goster <takim> [--bolum 1-5] [--tarih ..]     kayitli raporun istenen bolumu
  neden                       maclar neden SKIP
  detay [N]                   N maci tum pazarlariyla cek
  kupon [--gun D] [--canli] [--n 5] [--oran 4-6]   4-6 toplam oranli kuponlar (baslamamis maclar ya da canli); portfoy olasiliklari
  yardim
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

HELP = """Hangi isteği hangi komut karşılar (katman: komut):
| Sen ne dersin | Katman | Komut |
| :--- | :--- | :--- |
| "20:30-00:00 arası maçları bul" | 1 Bulma | bwm.py bul --saat 20:30-00:00 |
| "Barcelona ve Ajax maçları hangileri?" | 1 Bulma | bwm.py bul --takim "Barcelona,Ajax" |
| "Nesine'nin açtığı oranlar / oynanmayan maçlar / bülten" (Nesine oranı) | A2 Nesine | bwm.py nesine [--gun yarin] [--takim A] [--acik iyms] [--tam] |
| "Taktik kurallarına uyan oynanmamış maçları bul" (Oran Analizi taktikleri) | A3 Taktik | bwm.py taktik [--yakin] [--ad "4,5"] [--arsiv] |
| "Yeni taktik yazdım: kaydet / kuralı değiştir / kapat" | A3 Taktik | bwm.py taktik --ekle "Ad" --hedef ".." --oyna u45 --basari "top>=5" --kural "u45 4,20-5,10" [--kural ..] ; --degistir/--kapat/--ac/--sil "Ad"; --liste |
| "Geçmiş bülten / eski maçların oranı ve sonucu" | A Arşiv | bwm.py arsiv kapsam / mackolik YYYY-MM-DD / goster ... / sonuc / topla |
| "Skill ve ajan belgelerini kaydet (global + proje)" | - | bwm.py senkron [--yaz] |
| "Ajax'ın oranları" (iddaa API, Nesine'den ~%4.5 yüksek) | 2 Veri | bwm.py oran Ajax |
| "Ajax'ın istatistikleri/xG" | 2 Veri | bwm.py istat Ajax |
| "Ajax'ta sakat kim var?" | 2 Veri | bwm.py sakat Ajax |
| "Hakemin kart ortalaması" | 2 Veri | bwm.py hakem --takim Ajax |
| "Roma-Inter'i / şu maçları derin analiz et" (belirli maç, gün yok) | 7 Derin | bwm.py derin "Roma,Ajax" |
| "Bugünkü tekli (kombinsiz) oynanabilir maçlar / güvenli tahmin sinyalleri" | 7 Derin | bwm.py derin --gun (OYNA/SPEK tablosu; OYNA yoksa ESNEK ADAY + GÜVENLİ tahmin sinyalleri) |
| "8/10 ve üzeri güvenli (yüksek isabet olasılıklı) seçenekler" | 7 Derin | bwm.py derin --gun --guven 8 [--oran 1.30] (ön maç) / bwm.py canli --hepsi --guven 8 [--oran 1.30] (canlı); güven ≠ değer, EV = maliyet; oran ≥ 1.30 iken en fazla 6/10 ulaşılır |
| "Roma-Inter'in tüm pazar tabloları" | 3 Analiz | bwm.py mac --takim Roma --tam |
| "Bugünün maçlarını tara" | 3 Analiz | bwm.py tara |
| "20.09.2026'daki TÜM maçları (her spor) analiz et, en mantıklı 50" | 6 Gün taraması | bwm.py gun 2026-09-20 |
| "Şu an oynanan Ajax maçında ne yapayım?" | 5 Canlı | bwm.py canli Ajax |
| "Şu an oynanan TÜM maçlara bak" | 5 Canlı | bwm.py canli --hepsi (yalnız OYNA/SPEK + ret özeti; OYNA yoksa ESNEK ADAY; --tum her maç) |
| "4-6 oranlı kupon yap" (başlamamış maçlar) / "canlıdan kupon" | 9 Kupon | bwm.py kupon [--gun YYYY-MM-DD] [--n 5] [--oran 4-6]; canlı: bwm.py kupon --canli |
| "Kararlarım tuttu mu / başarı oranı?" | 8 Sonuç | bwm.py sonuc |
| "Modelimiz piyasadan iyi mi? (hangi pazarda, hangi ağırlık)" | 8 Sonuç | bwm.py kalibre [--gun 7] |
| "iddaa oranı gerçek değer mi? (çok kitaplı konsensüs / Pinnacle referansı)" | 10 Referans | bwm.py referans [--sa 24] [--lig 6] [--yenile] (Odds API, 2 kredi/lig, 90 dk önbellek) |
| "Paris'in sadece nihai kararı" | 4 Rapor | bwm.py goster Paris --bolum 5 |
| "Neden çoğu maç SKIP?" | 4 Rapor | bwm.py neden |"""


def main():
    a = sys.argv[1:]
    cmd, rest = (a[0] if a else "yardim"), a[1:]
    if cmd == "bul":
        import find_matches
        find_matches.main(rest)
    elif cmd in ("taktik", "kural"):
        import taktik
        taktik.main(rest)
    elif cmd in ("nesine", "bulten"):
        import nesine
        nesine.main(rest)
    elif cmd in ("basketbol", "basket"):
        import basketbol
        basketbol.main(rest)
    elif cmd == "arsiv":
        import bulten_arsiv
        sys.argv = ["bulten_arsiv.py"] + rest
        bulten_arsiv.main()
    elif cmd == "senkron":
        import senkron
        senkron.main(rest)
    elif cmd in ("oran", "istat", "sakat", "hakem"):
        import collect_data
        getattr(collect_data, cmd)(rest)
    elif cmd == "derin":
        import derin
        derin.main(rest)
    elif cmd == "gun":
        import multi_sport
        multi_sport.main(rest)
    elif cmd == "canli":
        import live
        live.main(rest)
    elif cmd == "kupon":
        import kupon
        kupon.main(rest)
    elif cmd == "sonuc":
        import settle
        settle.main(rest)
    elif cmd == "referans":
        import oddsapi
        oddsapi.main(rest)
    elif cmd == "kalibre":
        import backtest_model
        backtest_model.main(rest)
    elif cmd == "tara":
        import run_daily
        run_daily.main(rest)
    elif cmd == "mac":
        import match_report
        match_report.main(rest)
    elif cmd == "goster":
        import match_report
        match_report.main(["--goster"] + rest)
    elif cmd == "neden":
        import explain_skips
        explain_skips.main()
    elif cmd == "detay":
        import fetch_details
        sys.argv = ["fetch_details.py"] + rest
        fetch_details.main()
    else:
        print(HELP)


if __name__ == "__main__":
    main()
