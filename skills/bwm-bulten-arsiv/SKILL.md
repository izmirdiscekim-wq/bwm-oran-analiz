---
name: bwm-bulten-arsiv
description: Katman A - eski/geçmiş bülten ve oran arşivi (son ~1 ay). YouTube oran taktiklerini geçmiş maçlarla eşleştirmek için maçlara AÇILMIŞ TÜM oranları (Nesine odaklı) ve sonuçları bulur/saklar. "20.09.2026 bülteni", "dünkü/geçen haftaki maçların oranları", "İY/MS oranları açılmış mıydı", "bu taktiği geçmişte dene", "bülteni arşivle", "PDF bülten ver", "mackolik/nesine'den geçmiş maçlar" isteklerinde kullan.
---

# Bülten arşivi (Katman A)
> Başlamamış (ileri tarihli) maçların Nesine oranları/pazarları için `bwm-nesine-bulten` (`python bwm.py nesine`); her sorgusu bu arşive yazar.
`cd C:\Users\Tuffy\.claude\agents; python bwm.py arsiv <komut>` (ajan: `agents/bulten-arsivci.md`; `bwm.py arsiv X` = `bulten_arsiv.py X`; kod ve veri Oran analiz klasöründedir).

## Amaç
Kullanıcı YouTube'daki oran taktiklerini bitmiş maçlarla sınar. Gerekenler: (1) maça AÇILMIŞ tüm oranlar (İY/MS dahil) — kullanıcı **Nesine**'de oynar, (2) sonuç (MS + İY skoru). Geriye en fazla ~1 ay yeterli; kaynaklar `data/arsiv/` altında KALICI saklanır (bellek).

## Hangi kaynak ne verir (21.09.2026'da doğrulandı)
| Kaynak | Geriye | Oran | Sonuç | Not |
| :--- | :--- | :--- | :--- | :--- |
| **Nesine bülteni** (`kaydet`) | kaydettiğimiz günden ileri | TÜM pazar (İY/MS, skor, …) = kullanıcının gerçek oranı | `sonuc` ile eklenir | biten maçı siler → ileriye dönük kayıt şart |
| **Mackolik programı** | ~16.09'dan (≈5 gün) + ~9 gün ileri | **Nesine oranıyla birebir aynı** (72 maçın 69'unda MS ±0.01) ama YALNIZ MS, ÇŞ, A/Ü 2.5, KG, handikaplı MS, İY 1,5 | MS + İY skoru | İY/MS ve diğer pazarlar YOK |
| **Kendi iddaa API kaydı** (`kaydet`/`ice-aktar`) | 18.09'dan | TÜM pazar, Nesine'den ~%4 yüksek | `sonuc` | eski anlık dosyalarda başlamış maçlar CANLI oran içerir → `[KAPANIŞ SONRASI ANLIK]` |
| **Basılı iddaa PDF'i** (`pdf-aktar`) | kullanıcı indirirse istediği hafta | TÜM pazar (88/191 futbol satırı 11 pazar), iddaa oranı, basıldığı andaki (KAPANIŞ DEĞİL) | yok (Mackolik/Flashscore yalnız ≤7 gün) | iddaa.com robots.txt klasörü ve yapay zekâ ajanlarını yasaklar → OTOMATİK İNDİRME YOK; kullanıcı tarayıcıyla indirir |
| Flashscore günlük liste | 7 gün | ORAN YOK | yalnız SKOR (yedek): İY = MS − BC/BD | kullanıcı Flashscore oranı İSTEMİYOR; yalnız skor |
| Sahadan | yalnız bugün | (Mackolik zaten aynı oranı geriye de veriyor) | – | tarih parametresi yok sayılır |
| iddaa stats servisi | – | – | biten maç için skor/son maç DÖNMEZ | sonuç kaynağı değil |
| oranmerkezi.com | 2004'ten beri iddaa oranı+sonuç | üyelik/Excel aracı | üyelik | otomatikleştirilemez; kullanıcı isterse kendi dışa aktarımı içe alınır |
| football-data.co.uk, goapi.mackolik.com | – | – | – | bu makineden erişilemedi (DNS/bağlantı) |

**Açık boşluk:** 7 günden eski maçların SONUCU halka açık/otomatik hiçbir kaynaktan alınamıyor (Mackolik ~5, Flashscore 7 gün). Bir ay geriye tam veri (oran+sonuç) ancak `topla`'yı her gün çalıştırınca birikir; ya da kullanıcı üyelikli/API kaynak ekler (Oran Merkezi, API-Football anahtarı vb.).

## Komutlar
- `topla` — GÜNLÜK TEK KOMUT: `kaydet` (iddaa+Nesine tüm pazar) + `sonuc` (son 7 gün) + eski dosya raporu. Maçlardan önce/gün içinde 3-4 kez çalıştırılırsa "son" kayıt kapanışa yaklaşır (zamanlama için kullanıcı onayı gerekir).
- `kapsam [--gun 30]` — gün gün hangi kaynakta kaç maç, kaçı sonuçlu, eksik gün notu.
- `mackolik YYYY-MM-DD [--takim A] [--n 30]` — o günün Nesine-oranlı programı + skorlar (önbelleğe yazar).
- `goster YYYY-MM-DD --takim A --tam` — bir maçın tüm pazarları (arşiv/PDF).
- `sonuc [--gun 7]` — arşivdeki maçlara MS+İY sonucunu işler (Mackolik, yedek Flashscore skoru).
- `disari [--gun 30] [--cikti yol]` — `data/disari/tam_veri.jsonl`: maç başına `acilis`/`kapanis` (pazar→sonuç→oran) + `sonuc` + `kaynak` + `oran_turu`. Aynı maç birden çok kaynaktan gelebilir; öncelik: nesine-bulten > mackolik > iddaa-api (×0.962 ≈ Nesine) > basılı-pdf.
- `pdf-liste [--gun 30]` — son N gün için gereken PDF adları + adresleri (iddaa Salı ve Cuma programı yayımlar; `GG-AA-YYYY-Bulten.pdf`); kullanıcı elle indirip `data/pdf_gelen/` içine koyar; `pdf-aktar` (argümansız) yeni PDF'leri işler ve kayıt defterine (`data/arsiv/kaynaklar.json`) yazar. Orijinal PDF'ler saklanır.
- `sablon YYYY-MM-DD` — "oran tekrarı" taktiğini günde tarar (Nesine > iddaa > PDF, tekil maç; canlı/1.0/oran tavanı ayıklanır; sonuç varsa gerçek İY/MS ve TUTTU/tutmadı yazar). `sablon-hafta [--gun 7]` — Mackolik (Nesine oranı, ≤6 gün) ile MS1=MS2 gruplarında gerçek 1/X-2/X sıklığını tüm maçlarla (taban) karşılaştırır; İY/MS oranı Mackolik'te olmadığından ayna koşulu için `sablon GÜN` gerekir. Yeni taktikler için aynı kalıp: `disari` verisi + sonuç → isabet sıklığı; örneklem ve marjı (Nesine ~%4 daha düşük oran) belirt.
- `dogruskor [--gun 14] [--tol 0.5] [--yakin 1.0]` — "doğru skor oranı" taktiği (2.5 Üst / KG Var): favori 2-1 ≈ 7.00, 1-1 ile 2-1 yakın, 0-0 > 10.00. YALNIZ Nesine arşivi (MTID 777; bültende ~%10 maçta açık; iddaa feed'i, Mackolik ve PDF'te doğru skor YOK; arşiv 21.09.2026'da başladı → öncesi geri getirilemez). Kuralları sağlayan maçlar için aynı skor tablosundan piyasa-örtük KG Var/Üst 2.5 olasılığı ve Nesine oranlarıyla EV'yi yazar; sonuç işlenince (`sonuc`) KG/Üst sonucunu gösterir. Yan faktörler (form, sakat, ceza) ayrıca bakılır.
- `ust45 [--gun 7]` — "4,5 Gol Üstü" taktiği: (1) 4,5 Üst 4.20-5.10, (2) "İlk yarı 1 ve 1,5 gol üstü" 5.40-6.20 (= İY sonucu 1 + İY 1.5 Üst KOMBİNE pazar; düz İY 1.5 Üst ~2-3 oranlıdır, bu aralığa girmez), (3) Toplam Gol 4-5 gol 2.80-3.10. Nesine MTID'leri (21.09.2026'da marjsız olasılıkla doğrulandı): 155/SOV 4.5 = A/Ü 4.5 (N2 Üst; Alt çoğu kez 1.00'a kilitli → marjsız EV hesaplanamaz), 459/SOV 1.5 = İY sonucu ve İY 1.5 A/Ü (sıra 1A, XA, 2A, 1Ü, XÜ, 2Ü; N4 = İY 1 & Üst, N6 = İY 2 & Üst), 43 = Toplam Gol (N1 0-1, N2 2-3, N3 4-5, N4 6+), 342/SOV 1.5 = MS ve A/Ü 1.5. iddaa API kaydında total 4,5 Üst ve kombine İY pazarı YOK (yalnız Ev/Dep 4.5 ve Toplam Gol); Mackolik ve PDF'te de yok → tam test yalnız Nesine arşivinde; iddaa arşivinde yalnız kural 3 (×0.962) kısmen sınanır ve tabanla (≥5 gol sıklığı) karşılaştırılır.
- `temizle [--gun 35] [--sil]` — eski arşiv dosyaları (varsayılan listeler, silmez).
- `kaydet`, `durum`, `ice-aktar` — alt komutlar.

## Taktik testi kuralları
- Sonuç yalnız arşivde `sonuc` alanı olan maçlarla değerlendir; örneklem sayısını her zaman yaz. Yüksek oran + az örneklem = kanıt değil.
- Oran türünü belirt: kapanışa yakın Nesine/Mackolik ≠ basılı PDF (basıldığı andaki) ≠ iddaa API (~%4 yüksek).
- Taktik "kapanış oranı" diyorsa PDF verisini KULLANMA (kapanış değil).
- İY/MS taktiği: İY hanesi (a>b:1, a=b:0, a<b:2) + MS hanesi. Mackolik'te İY/MS oranı yok → Nesine kaydı/PDF gerekir.

## Yorumlama uyarıları
- `[KAPANIŞ SONRASI ANLIK]` etiketli kayıt canlı oran içerir (1.0/27/27 gibi); kullanma.
- Oran tavanı (~37.0/36.5): 1/2=2/1 gibi eşitlikler çoğu kez tavan artefaktıdır.
- Mackolik oranı = Nesine oranı (iddaa API × ~0.962).

## Token kuralları
Ham JSON/HTML okuma; yalnız komut çıktısı. Tek maçta `goster --takim A --tam`; çok maçta özet/tablo. Cevap: tablo + en fazla 1-2 cümle.
