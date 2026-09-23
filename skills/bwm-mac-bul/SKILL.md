---
name: bwm-mac-bul
description: Katman 1 - İddaa'da belirli gün, saat aralığı, lig veya takım(lar) için futbol maçlarını bulur (kimlik+saat+lig). "yarınki maçlar", "20:30-00:00 arası", "Barcelona ve Ajax maçları hangileri" isteklerinde kullan. Analiz yapmaz.
---

# Maç bulma (Katman 1)
> Nesine oranı / "oynanmayan maçları bul" / Nesine'de açık pazarlar için `bwm-nesine-bulten` (`python bwm.py nesine`); bu skill iddaa tabanlıdır.
`cd C:\Users\Tuffy\.claude\agents; python bwm.py bul [--takim "A,B"] [--lig "Lig"] [--saat 20:30-00:00] [--tarih YYYY-MM-DD] [--n 40] [--canli]`

- Filtre yoksa yalnızca başlamamış maçlar; `--takim/--lig` ile başlamış (canlı olmayan) maçlar da gelir; `--canli` canlıları ekler.
- `--saat 20:30-00:00`: bitiş ≤ başlangıçsa ertesi gün (00:00 = gece yarısı). Virgüllü `--takim` birden fazla maçı tek seferde bulur.
- Çıktı satırı: `id saat Ev - Dep (Lig)`; ≈12 token/maç. Ham veri okuma.
- Sonraki adım: veri için `bwm-veri-topla`, analiz için `bwm-mac-analizi` (aynı filtre bayrakları `mac` komutunda da geçerli: `mac 100 --saat 20:30-00:00`).
