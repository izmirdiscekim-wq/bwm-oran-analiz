---
name: bwm-taktik-tara
description: Katman A3 - kullanıcının YouTube'dan derlediği ORAN ARALIĞI taktiklerini (4,5 Üst, İlk yarı KG Var, İlk yarı 1-1, +6 gol vb.) Nesine bülteninde başlamamış maçlara uygular ve kurallara uyan maçları listeler. "taktiklere uyan maçları bul", "şu kurallara uygun oynanmamış maçlar", "yeni taktik ekle/kuralı değiştir", "taktikler tuttu mu" isteklerinde kullan. Kurallar taktikler.txt'de saklanır ve kullanıcı sürekli günceller.
---

# Taktik tarama (Katman A3)
`cd C:\Users\Tuffy\.claude\agents; python bwm.py taktik [bayraklar]` (ajan: `agents/taktik-tarayici.md`). Ham JSON okuma; yalnız komut çıktısı.

**Kayıt:** tüm taktikler `skills/bwm-taktik-tara/taktikler.txt` dosyasında, kategori **Oran Analizi** başlığı altında (ad, eklendi tarihi, hedef, pazar kuralları, kullanıcının orijinal ifadesi). Dosyayı OKUMA (token): `taktik --liste` (kısa liste), `taktik --kurallar` (kurallar), `--ekle/--degistir/--kapat/--ac/--sil` (yazma; her yazmada `.bak` yedeği; sonra taktik hemen taranır). Kullanıcı dosyayı elle de düzenleyebilir (biçim dosyanın başında).

| İstek | Komut |
| :--- | :--- |
| Tüm taktikleri bültende tara (TAM uyanlar + kural bazında geçen sayıları) | `taktik` |
| Tek kuralı kaçıran maçlar da | `taktik --yakin` |
| Yalnız bir taktik / gün / saat / lig | `taktik --ad "4,5" --gun yarin --saat 19:00-23:59 --lig "Uluslar"` |
| Okunan kuralları doğrula | `taktik --kurallar` |
| Kayıtlı taktikler (kısa) | `taktik --liste` |
| Pazar kodları | `taktik --kodlar` (listede yoksa ham kod `t<MTID>[/SOV]/<N>`, ör. `t43/4`) |
| Geçmişte tuttu mu (arşiv + sonuç) | `taktik --arsiv --gun-geri 7` (önce sonuçları kendisi işler) |
| Favoriye göre yön değişen kural (ör. "ev favoriyse İY1&Üst, deplasman favoriyse İY2&Üst"; "favori takımın oranı"/"favori olmayanın oranı") | kod `iygolfav` veya `msfav`/`msalt <aralık>` (favori = MS'de düşük oranlı taraf; `--kodlar` içinde ayrıca listelenir) |
| "Sabit X ya da Y-Z arası" (birden çok kabul edilebilir aralık) | tek kuralda `<aralık1>\|<aralık2>` (ör. `msalt 4.45-4.55\|4.70-4.80`); `=X` otomatik ±0.05 tolerans alır |
| Basketbol taktiği (yeni, 2026-09-23) | `--ekle "Ad" --spor basketbol --kural "bms1 1.50-1.70" ...` (varsayılan spor futbol; basketbol kodları "b" önekli, `--kodlar` ikinci satırda listeler; `basketbol.py` bültenini tarar, ayrı arşiv `data/arsiv/basketbol_*.json`) |

## Yeni/değişen taktik yazıldığında (ZORUNLU: kaydet, sonra tara)
Kullanıcı bir taktik metni (oran aralıkları + hedef) yazınca ayrıca istemese de KAYDET ve tara:
1. Her orana pazar kodu seç (`taktik --kodlar`; yoksa ham `t<MTID>[/SOV]/<N>`; kalıcı ad için dosyaya `@kod ad = t..` satırı). Emin değilsen `nesine --takim X --tam` ile Nesine'deki adına bak; sırayı uydurma, belirsizse tek soru sor.
2. `taktik --ekle "Ad" --hedef "..." --oyna <kod> --basari "top>=5" --kural "u45 4,20-5,10" --kural "..." --orijinal "<kullanıcının cümlesi>"`. Aynı ad varsa `--degistir "Ad" ...` (`--kural` verilirse kurallar TAMAMEN yenilenir). Kullanıcının sözü `--orijinal`'e, senin yorumun (ör. "kombine pazar") kural yorumuna (`"u45 4,20-5,10 # yorum"`). Farklı bir analiz türü için `--kategori "..."`; varsayılan Oran Analizi.
3. Çıktı = KAYDEDİLDİ + o taktiğin bülten taraması. Sonucu listele; kaydettiğini tek cümleyle söyle.
- `basari:` ifadesi isabet ölçümü içindir: `top ev dep iyev iydep iytop kg iykg ykg2` (ör. `top>=5`, `iyev==1 and iydep==1`).
- Başka taktiğin kuralını izinsiz değiştirme; `--sil` yalnız kullanıcı isterse. Kayıt sonrası bir şey ters giderse `taktikler.txt.bak` bir önceki sürümdür.

## Yorum kararları (21.09.2026; kullanıcı düzeltebilir)
- **"1. Yarı 1 ve 1,5 Üst" (5.40-6.20)** = İY sonucu 1 + İY 1.5 Üst KOMBİNE pazarı (`iy1u15`, MTID 459/N4). Düz İY 1.5 Üst ~2-3 oranlıdır, bu aralığa girmez.
- **"İlk ve İkinci Yarı Karşılıklı Gol Var"** = MTID 801 N3 (`iy2ykg`; sıra Yok/Yok, Var/Yok, Var/Var, Yok/Var; 41 maçta 452+599 marjsız olasılıklarıyla doğrulandı, hata 0.012 vs 0.095).
- **"İki yarıda da 1,5 Üst evet"** = MTID 529 N1 (`ciftu15`); **"Ev sahibi hangi yarıda daha çok gol: birinci yarı"** = MTID 586 N1 (`evyari1`); **"6+ gol"** = Toplam Gol N4 (`tg6`); **"4-5 gol"** = N3 (`tg45`).
- **Aralıklar dahil** (ör. 3.25 ve 5.40 tam sınır sayılır). **"Açılış oranı"** kuralı `kod@ilk` ile arşivdeki İLK kayıtlı orana bakar (gerçek açılış değil; arşiv 21.09.2026'da başladı); bülten açılış oranı vermez.
- Oranlar Nesine bülteninden ve DEĞİŞKEN: sınır değerinde geçen maç kickoff'a yakın yeniden taranmalı. Hedef pazar Nesine'de açık değilse (`OYNA u45 pazar Nesine'de AÇIK DEĞİL`) taktik o maçta oynanamaz.
- **Kapanış/güncel ayrımı — TÜM taktiklerde otomatik, her taramada:** araştırma (22.09.2026, 211 arşiv kaydı) gösterdi ki son çekilen oran kickoff'tan medyan 103 dk önce alınmış (yalnız %4'ü ≤20 dk) ve MS1 oranı ilk-son arasında medyan 0.05, %51'i >0.05 kaymış (maks 4.75) — "kapanış oranı" ile "güncel oran" gerçekten farklı olabiliyor. Bu yüzden her taktik taraması TAM uyanları otomatik **iki liste** halinde verir: **KAPANIŞ** (kickoff'a ≤20 dk kalan güncel oran; kullanıcının hedeflediği 5-10 dk'dan geniş tutuldu, çünkü dar pencere neredeyse hep boş çıkardı) ve **GÜNCEL** (kapanış öncesi — oran değişebilir, kickoff'a yaklaşınca tekrar taranmalı). Ayrı bir bayrak/alan gerekmez; boş liste satır açmaz (token). Arşiv taramasında (`--arsiv`) aynı ayrım gerçek `son_cekim`-kickoff farkıyla yapılır (gerçek gözlem, tahmin değil).
- **Favoriye göre yön değişen kriter** ("ev sahibi favoriyse X, deplasman favoriyse Y" gibi): tek bir `iygolfav` kodu kullanılır, favori MS oranından (düşük oranlı taraf) hesaplanır; çıktıda hangi alt-pazara gidildiği `iygolfav→iy1u15` gibi gösterilir. Favori belirsizse (MS eşit/yok) kural geçmez.

## Cevap kuralları (CLAUDE.md)
- Tablo: tarih-saat, lig, maç, MS, kural değerleri, hedef pazarın Nesine oranı. Tek kuralı kaçıranlar yalnız istenirse/`--yakin`.
- **TAM uyan her maç için (ZORUNLU): taktik adı + "neden uyuyor" AYNI SATIRDA, tek tabloda.** İkinci/ayrı bir kural tablosu AÇMA — maç satırının kendisine tek bir "Kurallar" sütunu ekle, o sütun içinde taktiğin TÜM kurallarını (yalnız "oyna" pazarını değil) **açık pazar adıyla** (kısaltma kodu değil) `Ad gerçekdeğer→aralık ✓` biçiminde `·` ile ayırıp yan yana yaz, örnek: `Toplam Gol 6+ 8.64→8.60-8.84 ✓ · İkinci Yarı Karşılıklı Gol Var 3.03→2.90-3.12 ✓ · İki Yarıda da 1,5 Üst 3.94→3.80-4.20 ✓`. Kod (`tg6`, `ykg2`...) yalnız parantez içinde istenirse eklenir, ana metin açık ad olur. `--kodlar` çıktısındaki adlar bile kısaltma içerebilir (MS/KG/İY/Y) — bunları da tam yaz: **MS = Maç Sonucu, KG = Karşılıklı Gol, İY = İlk Yarı, "2.Y" = 2. Yarı**. Ham komut çıktısı sayısal değerleri zaten basar (ör. `tg6 8.64 ykg2 3.03 ciftu15 3.94`) — değerleri tekrar hesaplama, yalnız adı açık yaz.
- Taktikler KANITSIZ: oran kuralına uymak isabet demek değildir. `--arsiv` örneklemi <30 ise "kanıt değil" yaz. Hedef pazarın kendi marjından EV'yi (= −marj payı, ~%18-25) yaz; "banko/garanti" deme. Kasa: kanıt yokken 0 Unit (izleme/kayıt) önerilir.
- Bulunan maçlar arşive yazılır (bwm-nesine-bulten); sonuçlar `sonuc` ile işlenince `--arsiv` isabeti ölçer.
