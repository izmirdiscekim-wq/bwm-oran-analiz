---
name: bwm-gunluk-tarama
description: Bugünün tüm İddaa futbol bültenini çekip toplu BWM taraması yapar (istatistik, model, komisyon, SKIP nedenleri). "bugünün maçlarını çek/tara", "neden skip", "tüm maçlar" isteklerinde kullan.
---

# BWM Günlük Tarama

Tek giriş: `cd C:\Users\Tuffy\.claude\agents; python bwm.py <komut>`.

| İstek | Komut | Süre |
| :--- | :--- | :--- |
| Bugünün bültenini çek ve tara | `python bwm.py tara` (varsayılan 150 maç; `tara 300` daha fazla) | ~6 sn |
| Neden çoğu maç SKIP? | `python bwm.py neden` | 1 sn |
| Maçları tüm pazarlarıyla çek (örnek) | `python bwm.py detay 50` | ~10 sn |
| Ayrıntılı analiz için tarama sonrası seç | `python bwm.py mac --takim <ad>` | 1 sn |

`tara` seçenekleri: `--api` (API-Football H2H+hava, YAVAŞ, dakikada 10 istek, API_FOOTBALL_KEY gerekir), `--dc` (Dixon-Coles; data/history/*.csv gerekir). Varsayılanda ikisi de kapalıdır.

## TOKEN KURALLARI (zorunlu)
1. Ham JSON ve rapor dosyalarını okuma; komut zaten kısa özet basar (durum satırları + nihai tablo).
2. Toplu sonuçta yalnızca tabloyu ve 2-3 cümlelik yorumu ver. Onaylı maç yoksa "SKIP" de; bu geçerli bir sonuçtur.
3. Belirli bir maç sorulursa ayrı komutla o maçı çalıştır (`bwm.py mac --takim ...`), tüm listeyi tekrar yazdırma.
4. Bülten gün içinde küçülür (biten maçlar API'den çıkar); tam gün için sabah çek.

## Notlar
Onay eşiği: bağımsız veri (İddaa istatistiği modeli) + güven ≥7; EV doğrulanamayan seçimler "EĞİLİM" (%0.5 kasa). Canlı maç, askıdaki (1.0 oranlı) pazar ve dengeli oran (<%42) otomatik SKIP.
