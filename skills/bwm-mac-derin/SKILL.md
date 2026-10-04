---
name: bwm-mac-derin
description: Katman 7 - kullanıcı gün vermeden belirli maç(lar) sorduğunda (tek veya grup; "X maçını/şu maçları derin analiz et", ekran görüntüsü) daha fazla veri çekip tüm pazarlardan oynanabilir en iyi seçenekleri bulur. Varsayılan yöntem budur.
---

# Derin maç araştırması (Katman 7) — belirli maç sorulunca VARSAYILAN
`cd C:\Users\Tuffy\.claude\agents; python bwm.py derin "Takım1,Takım2,..." [--tarih YYYY-MM-DD]` (≈330 token/maç, ≈4 sn/maç).
Gün verilmezse her takım için bugünden itibaren 4 gün içindeki ilk başlamamış maç bulunur; U23/kadın/rezerv takımları elenir. Ekran görüntüsündeki takım adlarını komuta çevir.

## Ne yapar (tek komutla)
1. Maçı bulur, tüm pazarları (66-105) fiyatlar. 2. Ek veri: Flashscore (xG, şut, korner, kart, H2H, hakem), Transfermarkt (sakat/cezalı, hakem sarı/kırmızı/penaltı ort.), Open-Meteo hava (yalnız aşırı koşul düzeltir), oran hareketi kaydı (`data/odds_moves.jsonl`; aynı maç ≥20 dk sonra tekrar sorulunca ilk kayda göre değişim).
3. Yerleşik yöntemler: Model A xG'li Dixon-Coles, Model B zaman ağırlıklı + saha ayrımlı Poisson (Dixon-Coles 1997 zaman azaltma), Shin de-vig piyasa; **mutabakat** = A, B ve piyasa aynı favoriyi/toplam golü gösteriyor mu (YÜKSEK/ORTA/DÜŞÜK).
4. Çıktı: sinyal satırı, veri satırı, uyarılar, tüm pazarlardan en iyi 3 (harman EV) + en güvenilir 2, karar.

## Günün tüm tekli maçları ("bugünkü maçlardan oynanabilirleri bul", kombinsiz)
`python bwm.py derin --gun [--tarih YYYY-MM-DD] [--n 10] [--detay 4]` (≈46 sn, ≈500 + 330/maç token): günün TÜM başlamamış futbol maçları 1. aşamada (Transfermarkt kapalı) tek satırla elenir, "sağlam sinyal"li en iyi `--detay` maç 2. aşamada tam derin analize girer (sakat/hakem dahil).

## Karar kuralı
Sağlam sinyal = mutabakat ≥ ORTA, xG çelişkisi yok, güven ≥ 4. OYNA: sağlam sinyal + harman EV ≥ +%3; harman EV ≥ −%3 + sağlam sinyal → SPEKÜLATİF (≤0.25 Unit); aksi SKIP (nedeni yazılır). Stake çeyrek-Kelly, 1 Unit = kasa %1.

## Token kuralları
Yalnızca bu komutun çıktısını oku; tabloların tamamı için ayrıca `mac --takim X --tam`, tek bölüm için `goster X --bolum N`. Yanıt: derin çıktının kararını + 1-2 cümle yorum; çıktıyı tekrar yazma.

## Dürüstlük / YOK
Harman EV çoğunlukla negatif (komisyon %14-17): SKIP geçerli sonuçtur. ClubElo API'si 502 verdi (kullanılamadı); Pinnacle/keskin oranlar yok; oyuncu önemi/λ'ya sakatlık etkisi yok; Asya handikapı yok; Model B iddaa son 10 maç skorlarına dayanır (küçük örneklem). Yeni yöntem/veri kaynağı bulunursa `derin.py`'ye eklenir ve bu skill güncellenir.
