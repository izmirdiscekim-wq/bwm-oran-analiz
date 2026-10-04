---
name: bwm-taktik-tara
description: Katman A3 - kullanıcının YouTube'dan derlediği ORAN ARALIĞI taktiklerini (4,5 Üst, İlk yarı KG Var, İlk yarı 1-1, +6 gol vb.) Nesine bülteninde başlamamış maçlara uygular ve kurallara uyan maçları listeler. "taktiklere uyan maçları bul", "şu kurallara uygun oynanmamış maçlar", "yeni taktik ekle/kuralı değiştir", "taktikler tuttu mu" isteklerinde kullan. Kurallar taktikler.txt'de saklanır ve kullanıcı sürekli günceller. SAF ORAN taktikleri için; istatistik koşulu olan taktikler için `iob-taktik-tara` (ayrı proje) kullanılır.
---

# Taktik tarama (Katman A3, saf oran)
`cd C:\Users\Tuffy\.claude\agents; python bwm.py taktik [bayraklar]` (ajan: `agents/taktik-tarayici.md`, aynı içerik — ayrıntı için bu dosyaya bak). Ham JSON okuma; yalnız komut çıktısı.

**Kayıt:** `skills/bwm-taktik-tara/taktikler.txt` (kategori Oran Analizi). Dosyayı OKUMA: `taktik --liste`/`--kurallar` okur, `--ekle/--degistir/--kapat/--ac/--sil` yazar (`.bak` yedeği, sonra hemen tarar).

| İstek | Komut |
| :--- | :--- |
| Tüm taktikleri tara (TAM uyanlar + kural bazlı geçen sayıları) | `taktik` |
| Tek kuralı kaçıranlar da | `taktik --yakin` |
| Tek taktik / gün / saat / lig | `taktik --ad "4,5" --gun yarin --saat 19:00-23:59 --lig "Uluslar"` |
| Kurallar / kayıtlı taktikler (kısa) / pazar kodları | `taktik --kurallar` / `--liste` / `--kodlar` |
| Geçmişte tuttu mu | `taktik --arsiv --gun-geri 7` |
| Favoriye göre yön değişen ("ev favoriyse X, dep favoriyse Y"; "favori/favori-olmayan oranı") | kod `iygolfav` / `msfav` / `msalt <aralık>` (favori = MS'de düşük oranlı taraf) |
| Birden çok kabul edilebilir aralık | `<aralık1>\|<aralık2>` (ör. `4.45-4.55\|4.70-4.80`); `=X` ±0.05 tolerans |
| Korelasyon ("X oranı Y oranına yakın olmalı") | `fark <kod1> <kod2> <aralık>` → \|oran1-oran2\| aralığa uymalı, iki ham oran da gösterilir |
| Pazar KAPALI olmalı | `kapali <kod>` → yoksa (v=None) geçer, varsa gerçek oranla geçmez |
| Basketbol taktiği | `--ekle "Ad" --spor basketbol --kural "bms1 .."` ("b" önekli kodlar, `basketbol.py` bülteni) |

## Hem istatistik hem oran → AYRI proje (2026-10-02 konsolide edildi)
Oran filtresi + takımların GERÇEK son maç istatistiğine göre dallanan/eşiklenen taktikler (ör. "KLMB formülü") bu projede DEĞİL, `maçları istatistiklere göre bulma` projesinde: `bwm.py iob taktik` (skill `iob-taktik-tara`, ayrı `taktikler.txt`, gerçek istatistik kaynağı `statisticsv2.iddaa.com`). Bu skill'e YALNIZ saf oran-aralığı kuralı (istatistik koşulu yok) taktikler eklenir — karıştırma, iki dosya birbirini görmez.
**Üç mod:** "sadece istatistik" (oran yok) → Katman 2/7 `veri-toplayici`/`derin`/`mac` (Flashscore+Dixon-Coles); "sadece oran" → bu skill; "hem istatistik hem oran" → `iob taktik`.

## Yeni/değişen taktik yazıldığında (ZORUNLU: kaydet, sonra tara)
1. **Önce sınıfla:** metinde oran filtresi + "takımın son N maçına göre FARKLI hedefe git" gibi istatistik-bağımlı bir dallanma varsa, bu BURADA DEĞİL `iob taktik`'te kaydedilir (yukarı bkz.). Yalnız oran aralığı + sabit hedef ise aşağıdaki akış.
2. Her orana `taktik --kodlar`'dan pazar kodu seç (emin değilsen `nesine --takim X --tam`; sırayı uydurma, belirsizse tek soru sor). **Dikkat:** "1. Yarı 1 ve 1,5 Üst" gibi birleşik ifadeler çoğu zaman KOMBİNE bir pazara (`iy1u15`, düz İY1.5Üst'ten FARKLI) karşılık gelir — emin değilsen `--kodlar`'daki açıklamaya bak, uydurma. Genel (takımsız) tam-maç 0,5 Üst/Alt hattı (`u05`/`a05`) iddaa/Nesine kataloğunda HİÇ açılmaz (MTID 11 hep sov=1.5 taşır) — buna dayalı bir `kapali u05` kuralı pratikte her zaman geçer, kullanıcıya söyle.
3. `taktik --ekle "Ad" --hedef "..." --oyna <kod> --basari "top>=5" --kural "u45 4,20-5,10" --kural ".." --orijinal "<kullanıcının cümlesi>"` (varsa `--degistir "Ad" ...`, `--kural` verilirse kurallar TAMAMEN yenilenir).
4. Çıktı = KAYDEDİLDİ + o taktiğin taraması; kaydettiğini tek cümleyle söyle.
- `basari:` isabet ölçümü: `top ev dep iyev iydep iytop kg iykg ykg2`. Başka taktiği izinsiz değiştirme; `--sil` yalnız istenirse.

## Kapanış-oranı testi (2026-10-03, ara sıra istenir, varsayılan değil)
"Şu an oynanan/kapanan maçların kapanış oranıyla taktiğe uyuyor mu" türü bir test istenirse: (1) kickoff'a ≤20dk kala bir `taktik`/`nesine` taraması çalıştır (gerçek kapanışı arşive yazdırır — `nesine.py`'nin kendi döngüsü zaten her tarama çeker/arşivler, ekstra adım yok); (2) sonra `nesine.py --arsiv YYYY-MM-DD --takim X [--pazar ..]` ile CANLI ÇEKİM YAPMADAN kayıtlı son/kapanış oranını oku (token-dostu, ağ isteği yok). `goster` komutu bunun için KULLANILMAZ — o iddaa arşivini okur, Nesine kapanışını göstermez. Maç kickoff'tan önce yakalanmadıysa (son kayıt 20dk'dan fazla erken) gerçek kapanış kayıp sayılır, uydurma.
**Otomasyon YOK (2026-10-03 kararlaştırıldı):** Bilgisayar çoğu zaman kapalı/uyku modunda olduğu için Windows zamanlanmış görev (arka planda periyodik `nesine.py`) denenmedi/kurulmadı — o pencerelerde çalışmaz, yanıltıcı boşluklu veri üretir. Kapanış yakalama YALNIZ aktif bir Claude Code oturumu sırasında, kickoff'a yaklaşırken manuel tetiklenir; oturum dışı geçen kickoff'lar için kapanış verisi yoktur, "veri yok" denir, uydurulmaz.

## İz sürme (`--iz-kaydet`/`--iz-kontrol`, 2026-10-03) — "bu maçı o an taktiğe göre oynasam, kapanışta da geçerli miydi, tuttu mu?"
Kullanıcı "şu an taradığın maçları oynadığımı düşün, kapanışta aynı analize uyuyor muydu, tuttu mu" türü bir soru sorunca:
1. Taramayı `taktik --iz-kaydet data/iz/iz_YYYYMMDD.json` ile çalıştır — TAM maçları (taktik, maç, tarandığı an, o anki kural değerleri) dosyaya ekler (var olanları tekrar eklemez).
2. Maçlar başladıktan sonra (aynı oturumda veya sonraki bir oturumda) `taktik --iz-kontrol data/iz/iz_YYYYMMDD.json`: her kayıt için (a) arşivdeki KAPANIŞ (son) oranıyla aynı kuralları yeniden değerlendirir → sinyal hâlâ geçerli mi; (b) sonuç varsa (`bulten_arsiv.py sonuc` çalıştırılmışsa) TUTTU/TUTMADI yazar; henüz başlamamışsa "henüz başlamadı" der, arşiv kaydı yoksa "kıyaslanamıyor" der — uydurmaz.
3. Bu, "aynı sinyalle mi yattı (gerçek isabetsizlik) yoksa kapanışa kadar oran zaten bozulmuş muydu (biz geç/erken yakaladık)" sorusunu ayırt eder — çıktıda `sinyal kapanışta HÂLÂ GEÇERLİ` / `KAYBOLMUŞTU` olarak işaretlenir.
Token-dostu: kayıt anında ekstra ağ isteği yok (zaten yapılan taramanın yan ürünü); kontrol anında da yalnız yerel arşiv dosyaları okunur, canlı çekim yapılmaz.

## Kickoff'a 5dk kala otomatik kapanış yakalama (`kapanis_bekle.py`, 2026-10-03)
Nesine geçmiş kapanış oranını API olarak sunmuyor — kendimiz kickoff'a yakın yakalamak ZORUNDAYIZ (yoksa kalıcı kayıp, bkz. yukarı). `--iz-kaydet` ile kaydedilmiş bir dosya varsa: `python kapanis_bekle.py data/iz/iz_YYYYMMDD.json` (arka planda, `run_in_background: true` + nohup/disown) her kaydın kickoff'una 5dk kala taze bir Nesine çekimi yapar ve o maçın pazarlarını kayda `"kapanis_orani": {"cekildi": ts, "mk": [...]}` olarak yazar; tüm kayıtlar işlenince kendiliğinden biter. `--iz-kontrol` artık önce bu alanı arar (varsa "kapanis_bekle.py ile T-5dk'da yakalandı" der), yoksa genel arşivin son kaydına döner.
**Bilinen sınır (OS zamanlanmış görev DENENMEDİ — PC çoğu zaman uykuda, ayrıca "Unauthorized Persistence" ile engellendi):** Bu bir arka plan SÜRECİ, sistem görevi DEĞİL — yalnız bu oturumun/bilgisayarın açık olduğu sürece çalışır; PC uyursa/kapanırsa veya oturum tamamen kapanırsa süreç de ölür, o maçların kapanışı kaçar (yeniden deneme yok, "veri yok" denir). Her yeni "iz sürme" isteğinde bu watcher'ı yeniden başlatmak gerekir.

**Dış kaynaktan geriye dönük kapanış oranı: DENENDİ, MÜMKÜN DEĞİL (2026-10-03).** Oranı kendimiz kickoff'a yakın yakalamadıysak (bkz. yukarı, otomasyon yok), geriye dönük başka bir siteden bulma denendi: oddsportal, oddschecker, oddspedia, vitibet, forebet — 5'i de WebFetch'i bağlantı resetleyerek (bot koruması) engelliyor; Bash/curl ile de sandbox dış internete zaten çıkamıyor. Mackolik (zaten kullandığımız `requests` kaynağı) MS/KG/A-Ü2.5/ÇifteŞans/HandikaplıMS/İY1.5 ile sınırlı, İY-MS/skor22 gibi taktik-özel pazarları hiç taşımıyor — tekrar denemeye kalkma: kaçırılan kapanış kalıcı olarak kayıptır, "veri yok" denir.

## Davranış notları (özet; gerekçe/ölçüm detayı → `PAZAR-NOTLARI.md`, otomatik yüklenmez)
- **KAPANIŞ/GÜNCEL ayrımı otomatik, her taramada:** TAM uyanlar kickoff'a ≤20dk kalan **KAPANIŞ** ve öncesi **GÜNCEL** olarak iki ayrı liste halinde gelir (boş liste satır açmaz); ayrı bayrak gerekmez.
- Aralıklar dahil sayılır; oranlar değişkendir, sınırdakiler kickoff'a yakın yeniden taranmalı. Hedef pazar açık değilse maç oynanamaz, söyle.
- `kod@ilk` = arşivdeki İLK kayıt (gerçek açılış değil, bülten açılış vermez).

## Cevap kuralları (CLAUDE.md)
- **"Oran analizi yap" = taktik taraması, BWM sinyali/model DEĞİL:** varsayılan kapsamla (tüm açık bülten, `--gun` kısıtlaması YOK) tara — "bugün" demeden `--gun bugun` kullanma, çoğu maçı sessizce düşürür (02.10.2026'da doğrulandı: aynı taktik `bugun` ile 4, `hepsi` ile 20 TAM maç verdi). Her taktiğin TAM maçlarını GERÇEK markdown tablosu olarak yaz (satır başına bir maç, düzyazı/madde imi DEĞİL): `Maç (tarih-saat) | Lig | MS (1/X/2) | Kurallar | Durum`. 0 maçlı taktik için tek satır yeter, boş tablo açma. `mac`/`derin` çalıştırma, "Nihai Tahmin"/"Nihai Karar" ekleme — bunlar yalnız ayrıca "analiz et"/"BWM sinyali ne diyor" denirse.
- **TAM uyan maç = taktik adı + TÜM kuralların gerçek değeri + "neden uyuyor", AYNI SATIRDA tek tabloda** (ikinci kural tablosu açma): `Ad değer→aralık ✓` `·` ile ayrılmış, **açık pazar adıyla** (kod değil; MS=Maç Sonucu, KG=Karşılıklı Gol, İY=İlk Yarı, 2.Y=2.Yarı açılır). Ham çıktı değerleri zaten verir, tekrar hesaplama.
- Taktikler KANITSIZ: `--arsiv` örneklemi <30 ise "kanıt değil" yaz; hedef pazarın marjından EV'yi hatırlat, "banko/garanti" deme; kanıt yokken 0 Unit öner.
