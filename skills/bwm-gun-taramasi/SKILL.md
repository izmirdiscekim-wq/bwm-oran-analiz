---
name: bwm-gun-taramasi
description: Katman 6 - istenen günün TÜM iddaa maçlarını (futbol, basketbol, buz hokeyi, tenis, hentbol) aynı mantıkla analiz edip en mantıklıdan başlayarak ilk N'i sıralar. "20.09.2026'daki maçları analiz et", "bugünün en iyi 50 maçı", "tüm sporları tara" isteklerinde kullan.
---

# Gün taraması, tüm sporlar (Katman 6)
`cd C:\Users\Tuffy\.claude\agents; python bwm.py gun YYYY-MM-DD [--n 50] [--futbol-n 150] [--spor futbol,basketbol,buz,tenis,hentbol]`
Kayıtlı sonucu ağsız yeniden göster: `gun YYYY-MM-DD --goster [--n 20]`. Tarih verilmezse bugün. ≈35 sn / 300 maç.

## Token kuralları
1. Çıktı tek tablo (50 satır ≈3.5k token). Daha az istiyorsan `--n 20`; tekrar göstermek için `--goster` (ağsız).
2. Tabloyu yanıtta tekrar yazma: ilk 5-10 satırı yorumla + nihai karar + dürüstlük notu. Ham veri/JSON okuma.
3. Tek maç ayrıntısı: futbolda `mac --takim X --tarih YYYY-MM-DD`; diğer sporlarda tablo satırı yeterli (ek katman yok).

## Mantık (her sporda aynı)
iddaa istatistik servisinden son ≤10 maç skoru → sporun skor modeli → pazar olasılıkları; oranlar Shin ile arındırılır (piyasa p); harman p = %70 piyasa + %30 model; EV = harman p×oran−1.
Süzgeçler: piyasa p ≥%10 ve tam küme, model-piyasa göreli sapma ≤%30, örnek ≥4 maç, model toplamı piyasadan çok ayrışırsa güven ≤4.
Modeller: futbol Dixon-Coles (`match_report.py`, xG/Flashscore); basketbol/hentbol Normal (fark+toplam); buz hokeyi Poisson (uzatma %50 varsayımı); tenis form-lojistik (zayıf). SD ve ev avantajı önselleri VARSAYIMDIR.
**Sıralama:** önce güven (futbol ≤6, basketbol/hokey/hentbol ≤5, tenis ≤3: model derinliği farkı), sonra harman EV.
Futbolda pazar sayısı en yüksek `--futbol-n` (150) maç derin analiz edilir, kalanı dipnotta sayılır; taramada Transfermarkt kapalıdır (sakat/hakem için tek maçta `mac --takim`).

## Dürüstlük
- Harman EV çoğunlukla NEGATİF (komisyon); `*` yalnızca ham EV ≥%5 demektir. Sıralama "en az kötü/en tutarlı"dır, tavsiye değil. SKIP geçerli sonuçtur.
- YOK: basketbol/hokey/hentbol/tenis için xG, sakatlık, hakem, dinlenme/yorgunluk, handikap pazarları (işaret belirsizliği nedeniyle fiyatlanmaz); voleybol vb. spor kodları bugün bültende yoksa sayılmaz (yeni spor: `SPORTS` tablosuna satır).
- Başlamış/canlı maçlar dışarıdadır (canlı için `canli`).
