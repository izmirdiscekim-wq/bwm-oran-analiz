---
name: bwm-basketbol-bulten
description: Katman B - Nesine.com'un AÇTIĞI basketbol oranları/seçenekleri ve başlamamış (oynanmayan) basketbol maçları. "basketbol maçlarını bul", "basketbol bülteni", "Nesine'de basketbol oranları", "yarınki basketbol maçları", "X basketbol maçının Nesine'deki pazarları", "sadece basketbol maçlarını çek/analiz et" isteklerinde kullan. "tüm maçlar" isteğinde bu araç `bwm-nesine-bulten` (futbol) ile BİRLİKTE çalıştırılır; futbol isteklerinde bu araç KULLANILMAZ.
---

# Basketbol bülteni (Katman B) — oynanmayan basketbol maçları + açılmış pazarlar
`cd C:\Users\Tuffy\.claude\agents; python bwm.py basketbol [bayraklar]` (ajan: `agents/basketbol-bulteni.md`; kod: `basketbol.py`, `nesine.py` ile BİREBİR aynı mimari). Ham JSON okuma; yalnız komut çıktısı. Kod ve veri: `Oran analiz\.claude\agents` (global bwm.py oraya yönlendirir).

## Spor seçimi kuralı (kullanıcı talimatı, 2026-09-23)
- "Tüm maçları çek/analiz et" → hem `nesine` (futbol) hem `basketbol` çalıştırılır, ikisi de listelenir/analiz edilir.
- "Sadece futbol maçlarını çek" → yalnız `nesine`.
- "Sadece basketbol maçlarını çek/analiz et" → yalnız `basketbol`.
- Taktik motoru (`taktik.py`) şu an yalnız futbol taktikleri içerir (`spor: futbol` varsayılan); kullanıcı basketbol taktiği yazarsa `taktik --ekle ... --spor basketbol --kural "bms1 ..."` ile kaydedilir (bkz. `bwm-taktik-tara`), kodlar "b" önekli (`taktik --kodlar`).

## Komutlar (nesine ile bayrak/biçim olarak aynı)
| İstek | Komut |
| :--- | :--- |
| Bugün/yarın basketbol maçları (liste: saat, lig, maç, MS 1/2, Handikap, Toplam Sayı A/Ü) | `basketbol [--gun yarin] --n 40` (varsayılan bugün+yarın) |
| X maçının basketbol oranları (çekirdek: MS, Handikap, Toplam Sayı, İY Handikap, İY Toplam) | `basketbol --takim "Ev,Dep"` |
| X maçının TÜM açık pazarları | `basketbol --takim X --tam` |
| Şu ligin maçları | `basketbol --lig "EuroLeague" --gun hepsi` |
| Şu pazarların AÇIK olduğu maçlar | `basketbol --acik handikap,toplam --gun hepsi` (kısayollar: ms handikap toplam iyhandikap iytoplam evtoplam deptoplam iysonuc ceyrek) |
| Yeniden çek / arşive yazma | `--yenile` (önbellek 3 dk) / `--kayitsiz` |

## Nasıl çalışır (23.09.2026'da doğrulandı)
- **Kaynak:** nesine.py ile AYNI bülten uç noktası (`bulten.nesine.com/api/bulten/getprebultenfull`), yalnız `TYPE == 2` süzülür (futbol `TYPE == 1`). Basketbolda beraberlik yoktur: Maç Sonucu 2 yönlü (1/2, X yok).
- **Pazar adları:** Nesine `MTID`/`MST` çiftleri, iddaa'nın basketbol pazar yapılandırmasından (`fetch_iddaa._fetch_market_config(session, st=2)`: önce `events?st=2` ile oturum basketbola kapsanır, sonra `get_market_config` çekilir — futboldakinden farklı, çünkü sunucu oturumu spora göre kapsar) çözülür; ayrı sözlük dosyası `data/basketbol_pazar.json`. Çekirdek MTID'ler doğrulandı: MS=142 (2 yönlü), Handikaplı MS=144, Toplam Sayı A/Ü=149, İY Handikap=148, İY Toplam=152, Ev/Deplasman Toplam=150/151, İY Sonucu=147.
- **Arşiv:** her taze çekim `data/arsiv/basketbol_YYYYMMDD.json`'a yazılır (futboldan AYRI dosyalar, aynı `bulten_arsiv.py` altyapısı); `taktik --arsiv` spor=basketbol taktiklerinde bu arşivi okur.
- **Nesine marjı** basketbolda da ana pazarlarda görünür (`marj %` her satırda); MS/Handikap/Toplam pazarlarında tipik %20-25 (futboldan biraz yüksek, örnekleme göre değişir).

## Kurallar
- "Basketbol maçı/bülten/oran" isteklerinde bu araç; futbol için `bwm-nesine-bulten`. İkisi karıştırılmaz — kullanıcı "tüm maçlar" demedikçe yalnız istenen spor çalıştırılır.
- Geçmiş (bitmiş) basketbol maçının sonucu/işlenmesi henüz yok (yalnız futbolda `arsiv sonuc`); basketbol arşivi şimdilik yalnız oran geçmişi biriktirir.
- Liste 40 satırdan uzunsa süzgeç ekle (`--gun`, `--saat`, `--lig`, `--acik`); tümünü dökme. Tek maçta önce çekirdek, gerekirse `--pazar`, en son `--tam`.
- Cevap: tablo + Nihai Karar (CLAUDE.md); araç çıktısını tekrarlama.
