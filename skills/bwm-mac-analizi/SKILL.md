---
name: bwm-mac-analizi
description: İddaa futbol maçlarının derin şablon analizi (Dixon-Coles adil oranlar, tüm pazarlar, korner/kart, İY/MS, BWM kuralları, +EV, kasa). "X maçını analiz et", "değerli bahis var mı", "nihai kararı göster" isteklerinde kullan.
---

# BWM Maç Analizi (şablon v2, 5 bölüm)

Tek giriş: `cd C:\Users\Tuffy\.claude\agents; python bwm.py mac ...`

## Katman 3-4 (analiz + karar). Maç bulma: `bwm-mac-bul`, tek konu veri: `bwm-veri-topla`.
Filtreli analiz: `bwm.py mac 100 --saat 20:30-00:00 [--tarih YYYY-MM-DD]`, `mac --takim "A,B"`.

## TOKEN KURALLARI (zorunlu)
1. Ham JSON ve rapor dosyasını ASLA okuma; yalnızca komut çıktısı.
2. Tek maç: `bwm.py mac --takim <ad>` (VARSAYILAN kısa: künye+kurallar+değerli+karar ≈ 500 token; pazar tabloları yok). Yalnızca karar: `goster <ad> --bolum 5`. Tabloları görmek: `--tam` ya da `goster <ad> --bolum 2`.
3. Çok maç: `bwm.py mac N` / `--lig "<ad>"` → yalnızca özet tablo (≈370 token / 8 maç). Ayrıntı: `bwm.py goster <takım> --bolum 3,4,5` (ağsız).
4. Yanıtta çıktıyı tekrar yazma: tablo + nihai karar + 1-2 cümle yorum.

## Veri kaynakları
- İddaa: maçlar, TÜM pazarlar ve oranlar (detay uç noktası), puan durumu, form, H2H, lig ortalaması.
- Flashscore (`fetch_flash.py`, herkese açık veri akışı): takımların son ≤6 maçının xG, şut, isabetli şut, korner, kart, gol; hakem adı; stadyum. İddaa maçlarının ~%88'i eşleşir; xG yalnızca istatistik kapsamındaki liglerde (test: büyük liglerde ~%40, diğerlerinde ~%15) vardır, yoksa İddaa lig maçlarına düşülür. Önbellek: `data/flash_stat_cache.json`.
- Transfermarkt (`fetch_tm.py`, herkese açık sayfalar, önbellek `data/tm_cache.json`): takımların sakat/cezalı oyuncuları (künyede listelenir, λ'ya OTOMATİK etkisi yok) ve hakemin son 2 sezon sarı/kırmızı/penaltı ortalaması (≥8 maç ise kart beklentisine %50 ağırlıkla katılır). Hakem adı belirsizse (aynı soyad+baş harfle birden fazla aday) kullanılmaz. Eşleşen TM adı raporda görünür; canlı maçta oran maç içi olduğu için rapor kaydedilmez.
- YOK: hakemin faul ortalaması, oyuncu önemi/kilit oyuncu ağırlığı, Asya handikapı (İddaa'da yok). SofaScore API 403 (engelli, aşılmadı); Goaloo'dan kullanılabilir istatistik alınamadı.

## Şablon → motor
- Bölüm 1 Künye: λ/μ (aynı ligdeki son ≤10 maç, ligin ortalamasına çekilmiş, ev avantajı 1.12), kaynak/örneklem, komisyon.
- Bölüm 2 Seçenekler: Dixon-Coles 8×8 (ρ=-0.10 varsayım), İY payı sabit %45, 1X2/ÇŞ/Alt-Üst/KG/aralık/skor/İY-MS/handikap(3'lü)/korner/kart adil oranları; tablolar EV'ye göre.
- Bölüm 3 Kural motoru: Valueless (<%42), handikap |H|-|F|>3, rövanş (3+ fark), Tier/zayıf takım, yorgunluk (dar fikstür/Avrupa dönüşü → λ x0.90), veri kalitesi.
- Bölüm 4 Değerli seçenekler: EV = p×oran−1 ≥ %5 VE ek süzgeçler (göreli sapma ≤%30, yarı bazlı ≤%15, piyasa olasılığı doğrulanabilir, xG çelişkisinde güven ≤4).
- Bölüm 5 Nihai: ana tercih, alternatif güvenli seçenek, güven 1-10, çeyrek-Kelly Unit (1 Unit = kasa %1).

## Dürüstlük (kullanıcıya söyle)
- YOK: hakem, faul, xG/şut, sakatlık/kilit oyuncu eksikliği, Asya handikapı (İddaa'da yok; 3'lü handikap var), oyuncu/şut pazarları fiyatlanmaz.
- Sezon başı örneklem 4-8 maç: model gürültülü, komisyon ~%14-17; "SKIP" geçerli sonuçtur, harman EV negatifse öneri spekülatiftir.
