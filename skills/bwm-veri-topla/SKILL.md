---
name: bwm-veri-topla
description: Katman 2 - tek bir konuda maç verisi getirir - İddaa oranları/marj, Flashscore xG-şut-korner-kart-H2H, Transfermarkt sakat/cezalı, hakem kart ortalaması. "X'in oranları", "sakat kim var", "hakem kaç kart veriyor", "xG'leri" isteklerinde kullan. Analiz/öneri üretmez.
---

# Veri toplama (Katman 2) — tek konu, birkaç satır
> `oran` iddaa API oranıdır (Nesine'den ~%4.5 yüksek). Nesine'nin açtığı oranlar/pazarlar için `bwm-nesine-bulten`.
`cd C:\Users\Tuffy\.claude\agents;`
| İstenen | Komut | Kaynak |
| :--- | :--- | :--- |
| oranlar, 1X2 marjı, Alt/Üst | `python bwm.py oran <takım>` | İddaa detay |
| son 6 maç xG/şut/korner/kart, H2H, hakem adı | `python bwm.py istat <takım>` | Flashscore |
| sakat/cezalı oyuncular | `python bwm.py sakat "A,B"` | Transfermarkt |
| hakem sarı/kırmızı/penaltı ort. | `python bwm.py hakem --takim <takım>` / `hakem "Soyad B."` | Transfermarkt |

## Notlar
- Yalnızca bugünün maçları (Flashscore listesi bugün); başka gün için `mac ... --tarih` analiz motorunu kullan.
- Transfermarkt eşleşen adı çıktıda görünür (yanlış takım/hakem riskini kontrol et); aynı soyad+baş harfle birden fazla hakem varsa kullanılmaz.
- Sakatlık listesi kitle kaynaklı; oyuncu önemi ve λ'ya etkisi hesaplanmaz. Hakem ortalaması ≥8 maç (son 2 sezon) gerektirir.
- Canlı maçta oranlar maç içidir. Yorum/öneri yazma; uydurma veri yok — yoksa "yok" de.
- Çok maçlı toplamada `veri-toplayici` ajanı (haiku) kullanılabilir; tek komutta doğrudan çalıştır.
