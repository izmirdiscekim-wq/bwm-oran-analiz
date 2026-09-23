---
name: bwm-nesine-bulten
description: Katman A2 - Nesine.com'un AÇTIĞI oranlar/seçenekler ve başlamamış (oynanmayan) maçlar. "oynanmayan maçları/bülteni bul", "Nesine'de açılan oranlar", "yarınki maçlar Nesine oranıyla", "hangi maçlarda İY/MS (doğru skor, 4.5 Üst, korner) açık", "X maçının Nesine'deki tüm pazarları", "oran analizi için maç bul" isteklerinde ilk tercih. Kullanıcı Nesine'de oynar; iddaa API oranı ~%4.5 yüksektir, bu araç Nesine'nin kendi bültenini okur.
---

# Nesine bülteni (Katman A2) — oynanmayan maçlar + açılmış tüm pazarlar
`cd C:\Users\Tuffy\.claude\agents; python bwm.py nesine [bayraklar]` (ajan: `agents/nesine-bulteni.md`). Ham JSON okuma; yalnız komut çıktısı. Kod ve veri: `Oran analiz\.claude\agents` (global bwm.py oraya yönlendirir).

## Karar ağacı (kullanıcı maç/bülten sorunca)
| Soru | Komut |
| :--- | :--- |
| bugün/yarın hangi maçlar var, bülten, oynanmayan maçlar | `nesine [--gun yarin]` |
| X maçının oranı/seçenekleri (Nesine) | `nesine --takim X` (analiz de isteniyorsa ardından `derin "X"`) |
| hangi maçlarda İY/MS, skor, 4.5 Üst… açık | `nesine --acik iyms,skor --gun hepsi` |
| oran-aralığı taktiğine uyanlar / yeni taktik | `taktik` (skill bwm-taktik-tara) |
| biten maç, geçmiş oran/sonuç | `arsiv ...` (skill bwm-bulten-arsiv) |
| basketbol maçları/oranları (Nesine) | `basketbol [bayraklar]` (skill bwm-basketbol-bulten; bu araç YALNIZ futbol çeker) |
| "tüm maçlar" (futbol + basketbol) | önce `nesine`, sonra `basketbol` (ikisi de çalıştırılır) |

| İstek | Komut |
| :--- | :--- |
| "Bülten / yarınki maçlar" (liste: saat, lig, maç, MS, A/Ü 2.5, KG, pazar sayısı, açık özel pazarlar) | `nesine --gun yarin --n 40` (varsayılan bugün+yarın) |
| "20:00-23:59 arası" | `nesine --saat 20:00-23:59 [--gun 2026-09-25]` |
| "Şu ligin maçları" | `nesine --lig "Uluslar" --gun hepsi` |
| "İY/MS (doğru skor, 4.5 Üst, korner…) AÇIK olan maçlar" | `nesine --acik iyms,skor --gun hepsi` (kısayollar: iyms skor iyskor toplamgol ust45 kombine korner kart handikap ya da pazar adı parçası) |
| "X maçının oranları / seçenekleri" (çekirdek: MS, ÇŞ, handikap, A/Ü 1.5-4.5, KG, İY, İY/MS, Toplam Gol, Maç Skoru + marj) | `nesine --takim "Ev,Dep"` (takım verilince tüm günlere bakar; en fazla 3 maç) |
| "X maçının TÜM açık pazarları" (~60 satır, ~2000 token/maç) | `nesine --takim X --tam` |
| "X maçında yalnız şu pazarlar" | `nesine --takim X --pazar "ust45,iyms,korner"` |
| "Şu pazarı açık maçlarda oranı" | `nesine --pazar ust45 --gun 24.09 --n 8` |
| Sonraki sayfa (liste 40'tan uzunsa) | `--atla 40` (çıktı sonu ipucu verir) |
| Yeniden çek / arşive yazma | `--yenile` (önbellek 3 dk) / `--kayitsiz` |

## Nasıl çalışır (21.09.2026'da doğrulandı)
- **Kaynak:** `bulten.nesine.com/api/bulten/getprebultenfull` (anahtarsız JSON, ~2.4 MB; `cdnbulten` yedek). YALNIZ bugün ve ileri günler; biten/başlamış maç yok. Bültende futbol = `TYPE 1`; `TYPE 2` = basketbol (ayrı araç: `bwm-basketbol-bulten`, `bwm.py basketbol`), 4 buz hokeyi, 5 tenis, 137/153 e-spor/sanal (henüz araç yok).
- **Eski kopya koruması:** `bulten.nesine.com` (origin) taze ama bazen zaman aşımına düşer; `cdnbulten.nesine.com` saatler öncesinin kopyasını verebilir (21.09'da 942.5M-942.7M sürüm, başlamış 47 maç dahil; taze sürüm 943.0M). Araç iki kez origin dener, görülen en yüksek `eventVersion`'dan düşük ya da başlamış maç içeren kopyayı reddeder, olmazsa önbelleği kullanır ve UYARI yazar (arşive yazılmaz). UYARI görürsen maç listesi/oranlar eski olabilir: bir dakika sonra `--yenile`.
- **Her taze çekim `data/arsiv/nesine_YYYYMMDD.json` arşivine yazılır** (ilk/son oran; başlamış maç dondurulur) → oran geçmişi kendiliğinden birikir; `sonuc`, `ust45`, `dogruskor`, `sablon` bu arşivi kullanır. Arşivde aynı maç iki kez yer almaz (kimlik = `başlama_ms_Ev_Dep`).
- **Pazar adları:** Nesine `MTID` + `MST` (pazar alt tipi); `MST` = iddaa pazar yapılandırmasındaki `mst`. Adlar `data/nesine_pazar.json`'da saklanır (75 futbol MTID'si çözüldü); bilinmeyen MTID görülürse otomatik güncellenir (`--sozluk-yenile` ile zorla). MTID 1 = Maç Sonucu (iddaa `2_1` handikap adıyla karışır; kodda özel).
- **Çıktı etiketi (N numarası → seçenek):** iddaa arşivi (158 ortak maç, Nesine/iddaa oran oranı medyan 0.953, dar aralık) ve marjsız olasılık kontrolleriyle doğrulandı: MS `1,X,2`; ÇŞ `1X,12,X2`; A/Ü `Alt,Üst`; KG `Var,Yok`; Toplam Gol `0-1,2-3,4-5,6+`; İY/MS `1/1,1/X,1/2,X/1,X/X,X/2,2/1,2/X,2/2`; MS&A/Ü ve İY&A/Ü `1A,XA,2A,1Ü,XÜ,2Ü`; MS&KG `1V,1Y,XV,XY,2V,2Y`; A/Ü&KG `Alt&Var,Üst&Var,Alt&Yok,Üst&Yok`; Maç Skoru 29 çıktı (N15 = 2:2; `bwm-nesine-odds`). Etiketi çözülemeyen pazar `N1…Nk` görünür (ör. "Hangi Takım Kaç Farkla Kazanır?", korner aralığı); "1. Yarı ve 2. Yarıda KG Olur" çözüldü (Yok/Yok, Var/Yok, Var/Var, Yok/Var); 46 çıktılı "İY/MS Skorları" atlanır.
- **Nesine marjı:** ana pazarlarda ≈ %17-18 (MS %18, Toplam Gol %22, Maç Skoru %49-51, İY/MS %31); her satırda `marj %` yazar. Çifte şansta olasılık toplamı 2 kabul edilir.
- **Oran 1.00 = büyük olasılıkla kilitli taraf (Nesine arayüzünde doğrulanmadı):** uç pazarlarda (ör. Alt 3.5 favori maçında, Handikap +2) bir taraf 1.00 gelir; satırda "1.00 taraf var (muhtemelen kilitli), marj yok" yazar. O tarafı oynanabilir sayma ve olasılık hesabına katma; karşı taraf tek başına okunur, marjı bilinmez.
- **Nesine ≈ iddaa API × 0.953** (bu çalışmada); iddaa API tabanlı EV (`derin`, `mac`) Nesine'de ~4-5 puan daha kötüdür.

## Kurallar
- Oran-aralığı taktiklerine uyan maçlar için `bwm-taktik-tara` (`python bwm.py taktik`).
- "Oynanmayan maç / bülten / Nesine oranı" isteklerinde önce `nesine`; `bul` (iddaa) yalnız iddaa kimliği gerektiğinde. Maç bulunca analiz için `bwm.py derin "Ev,Dep"` (iddaa tabanlı model) çalıştır; oranları Nesine'ye çevirmeyi ve marjı söyle.
- Geçmiş (bitmiş) maçların Nesine oranı bu araçla ALINAMAZ → `bwm-bulten-arsiv` (arşiv/Mackolik/PDF).
- Liste 40 satırdan uzunsa süzgeç ekle (`--gun`, `--saat`, `--lig`, `--acik`); tümünü dökme. Tek maçta önce çekirdek, gerekirse `--pazar`, en son `--tam`.
- Cevap: tablo + Nihai Karar (CLAUDE.md); araç çıktısını tekrarlama.
