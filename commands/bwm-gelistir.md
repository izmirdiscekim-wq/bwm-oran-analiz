---
description: BWM futbol analiz motorunu derin araştırma + geçmiş karar denetimi + oyuncu sağlık/erişilebilirlik modellemesiyle ölçülebilir şekilde geliştirir ve test eder
argument-hint: "[odak: olcum|saglik|model|hepsi] (varsayılan hepsi)"
---

# BWM Futbol Analiz Geliştirme Döngüsü — Derin Araştırma

Rolün: bu depoyu yazan kıdemli geliştirici **ve** Billy Walters metodolojisiyle çalışan nicel bahis analisti. Görev: BWM **futbol** analizlerini, kanıta dayalı ve ölçülebilir şekilde iyileştirmek. Odak: `$ARGUMENTS` (boşsa `hepsi`).

Kapsam dışı: basketbol / buz hokeyi / tenis (CLAUDE.md: basketbol beklemede, çoklu-spor motoru ayrı). Onlara dokunma.

---

## 0. Değiştirilemez kurallar

1. **Kanıtsız katsayı yok.** Yeni bir katsayı/ağırlık eklersen yanına kaynağını yaz ve `CALIBRATED=False` muadili bir işaret bırak. "Literatürde böyle" tek başına yeterli değil — bizim veriyle ölçülmediyse VARSAYIM olarak etiketle.
2. **Eşik gevşetmek yasak.** `OYNA` sinyali çıkmıyorsa bu bir hata değil, iddaa marjının (ölçülen %13–17) doğru sonucudur. Sinyal üretmek için `EV_ESIK`, `MIN_ORAN=1.30`, Kelly tavanı veya güven eşiklerini düşürme. Sinyal sayısını artırmayı başarı sayma.
3. **n < 30 ise karar yok.** Hiçbir taktik/katman hakkında 30 yerleşmiş kayıttan az veriyle "çalışıyor/çalışmıyor" demeyeceksin (Buchdahl ~50 önerir). Sayıyı yaz, kararı erteliyorsan ertele.
4. **Uydurma veri yok.** Erişilemeyen kaynak = "erişilemedi" diye raporlanır; vekil (proxy) kullanıyorsan proxy olduğunu söyle.
5. **Token cimriliği** (CLAUDE.md kalıcı kuralı): ham HTML/JSON/bülten dosyalarını bağlama okutma, hepsini script'le işle ve yalnız özet sayıları yaz. Alt ajan açma — kendi araçlarınla çalış. Web aramalarını tek turda topluca gönder.
6. **Push yok, CLAUDE.md'ye dokunma.** Commit mesajı önerebilirsin; `git push` ve CLAUDE.md değişikliği için açık onay istersin. Render autoDeploy açık olduğu için push = canlıya çıkmak demektir.
7. Belge/skill düzenlersen sonunda `python bwm.py senkron --yaz` çalıştır.

---

## 1. Yeniden keşfetmeyeceğin ölçümler (token yakma)

Bunlar bu depoda daha önce **ölçüldü**; tekrar araştırma, üzerine inşa et:

| Ölçüm | Değer |
|---|---|
| iddaa overround (ana pazarlar) | %13–17 · Nesine ~%13.1, iddaa'dan ~%4.5 ucuz |
| Konsensüs kontrolü | 73 seçenekte pozitif EV: **0/73** |
| Oran ≥ 1.30 ∧ güven ≥ 7 | Hiç birlikte görülmedi; ≥1.30'da ulaşılabilir tavan **6/10** |
| 15 aktif taktiğin backtest'i | Hiçbiri n≥30'da ROI > %3 değil; 10 taktik kapatıldı, 5 aktif |
| En iyi CLV kaydı | Kontrivo ev favorisi: CLV **+%3.83**, n=34, ROI +%0.5 |
| Harman olasılık | p = 0.70·piyasa(Shin) + 0.30·model — model≈piyasa, gol toplamında model kötü |
| Kırılım eşiği | `|Δoran| ≥ %10` (futbol `odds_watcher`, çoklu-spor `COEF["KIRILIM"]`) |
| Kapalı veri kaynakları | football-data.co.uk, ClubElo, Understat, Pinnacle, Betfair, Sackmann, tennis-data: TR'den **kapalı/404**. Açık: Flashscore, API-Football, Transfermarkt, Mackolik, iddaa statisticsv2, Nesine, okooo, odds-api.io |

## 2. Bilinen açık uçlar (Faz 1'e buradan başla)

- `bets_log.jsonl` **boş** → `bwm.py sonuc` hiçbir yerleşmiş bahis göremiyor. Karar üretiliyor ama kaydedilmiyor: zincirin koptuğu yeri bul.
- `clv.rapor()` → n=4 (ÖLÇÜM kontrol grubu, +%3.00). CLV hattı çalışıyor ama örneklem yok.
- `data/iz/iz_19700112.json` → `esd_ms: 1`, takımlar "A"/"B": bir **birim testi üretim veri dizinine yazmış**. Hem dosyayı hem testi düzelt.
- `watchlist` tablosunda `odds_acilis = NULL` olan aktif satırlar (Çifte Şans / "Valueless Bet: SKIP" kararları) → `odds_watcher.check_all()` bunları sessizce atlıyor, o maçlarda kırılım hiç ölçülmüyor.
- `balina.py` dünya konsensüsü yalnız **3 tur** veri biriktirdi → `dp_w ≥ 2 puan` şartı için geçmiş yok, bugüne dek **0 A–D hücresi**. Kredi sınırı 12/gün.
- `player_impact_matrix.py`: sakatlık etkisi Transfermarkt **piyasa değeri vekili** üzerinden; `DAMPEN=0.6` ve `MAX_IMPACT=0.35` **backtest edilmedi**, pozisyon ayrımı yok (kaleci kaybı = forvet kaybı).

---

## 3. FAZ 0 — Ölçüm hattını onar (KAPI: bu bitmeden Faz 3'e geçme)

Ölçülemeyen iyileştirme, iyileştirme değildir. Önce karar → kayıt → yerleşim → metrik zincirini kapat.

1. Karar üreten yolları izle (`decision.py`, `ranker.py`, `telegram_report.py`, `telegram_bot.py`, `taktik.py`, `balina.py`) ve her **OYNA / SPEKÜLATİF / ESNEK ADAY** kararının tek bir yere — `bets_log.jsonl` — otomatik yazıldığından emin ol. Alanlar en az: `ts, gun, esd_ms, lig, ev, dep, pazar, secim, oran, p_model, p_piyasa, ev_yuzde, kasa_pct, guven, risk, kaynak(katman), kapanis_orani, sonuc, kar_zarar`.
2. Yerleşim (settlement): mevcut arşiv (`data/arsiv/mackolik_*.json`, `bwm.py arsiv sonuc`) üzerinden sonucu otomatik işaretleyen akışı çalıştır/onar. Hangi pazar için hangi kural kullanıldığını kodda tek yerden okunur yap.
3. Kapanış oranı kapsamı: `clv.kapanis_yakala()` kaç kararın kapanışını yakalıyor, kaçını kaçırıyor? Sayıyı ölç, kaçanların nedenini sınıflandır.
4. `data/iz/` epoch kirliliğini temizle; testlerin üretim dizinine yazmasını engelle (tempfile).
5. Çıktı: `python bwm.py sonuc` ve `clv.rapor()` artık gerçek satır gösteriyor olmalı (bugün n küçük olabilir — önemli olan hattın akması).

## 4. FAZ 1 — Geçmiş analizlerin denetimi (neyi doğru, neyi yanlış yaptık)

Yalnız sayıyla konuş. Kaynaklar: `data/history/sinyal_log.jsonl`, `data/iz/*.jsonl`, `watchlist.db` (`odds_movement_log`, `clv_log`), `data/arsiv/*`, `backtest_taktik.py`, `data/acilis/*`.

1. `python backtest_taktik.py --esnet` ile aktif 5 taktiği yeniden ölç (n / isabet / ROI / CLV). Bant genişletmenin ROI'yi düzeltip düzeltmediğini **ayrı** raporla.
2. Kırılım/balina katmanı: `odds_movement_log`'daki 28 tarihsel kırılımın kaçı maç sonucunda doğru yöne işaret etmiş? Kırılım yönü (düşen oran = para geldi) ile sonuç arasındaki ilişkiyi ölç; örneklem küçükse söyle.
3. **Hata taksonomisi** çıkar — her hatayı şu 4 kovadan birine koy ve her kova için sayı + örnek ver:
   - **Veri hatası** (yanlış pazar eşleşmesi, bayat oran, eksik kadro verisi)
   - **Model hatası** (olasılık yanlı: hangi pazarda hangi yöne)
   - **Eşik/karar hatası** (filtre ya çok geçirgen ya çok dar)
   - **Yerleşim/kayıt hatası** (sonuç yanlış işaretlendi veya hiç kaydedilmedi)
4. **Doğru çıkanları da yaz.** Hangi katman kanıt üretti (ör. Shin devigging'in marj düzeltmesi, pazar-farkında `_pazar_orani()` ile sahte kırılımların kapanması, `ana_hat()` ile winner's curse'ün önlenmesi)? Bunları koru ve regresyon testiyle kilitle.
5. Kalibrasyon temeli: mevcut `bwm.py kalibre` çıktısını al; model ile piyasanın hangi pazarda ayrıştığını ölç (Brier skoru ve log-loss hesapla; yoksa ekle).

## 5. FAZ 2 — Derin araştırma (WebSearch, kaynaklı)

Üç başlıkta dünya literatürünü/pratiğini tara. Nadir/teknik konularda `mode: "extended"` kullan, aramaları tek turda topluca gönder. **Her bulgu için zorunlu üç satır:** (a) kaynak linki, (b) iddia ettiği etki büyüklüğü, (c) **bizim veriyle ölçülebilir mi?** — ölçülemiyorsa "uygulanmaz" diye ele.

**A) Futbol tahmin modelleri — güncel durum**
Dixon-Coles ve türevleri (zaman ağırlığı, düşük skor düzeltmesi), bivariate/çift Poisson, xG tabanlı güç dereceleri, Elo/Glicko varyantları, piyasa-harmanı (market blending) literatürü, Kelly ve kesirli Kelly pratiği, kapanış oranının (CLV) neden gerçek ölçüt olduğu.

**B) Oyuncu sağlığı / kadro erişilebilirliği (availability) — dünyadaki yaklaşımlar**
Bu başlık özellikle istendi, derinlemesine git:
- Pozisyon ağırlıklı erişilebilirlik (kaleci, stoper, 10 numara, forvet farklı ağırlık), **dakika ağırlıklı** katkı (oynanan dakika payı), xG/xA/xGI veya xT tabanlı oyuncu katkısı ile kayıp gücün hesaplanması.
- Sakatlıktan dönüş eğrisi (dönen oyuncunun ilk maçlardaki düşük katkısı), sakatlık süresi/tipi sınıflandırması, yeniden sakatlanma riski.
- Fikstür yoğunluğu / yorgunluk (maç aralığı, seyahat mesafesi ve saat dilimi, milli ara dönüşü), rotasyon olasılığı, kart-ceza kaynaklı yokluk.
- Hastalık/salgın ve hava koşullarının takım performansına etkisi üzerine ölçülmüş çalışmalar (varsa; yoksa "kanıt zayıf" yaz).
- Kaynak testi: hangi ücretsiz kaynak **TR'den** gerçekten kadro/sakatlık/ilk 11 veriyor? Transfermarkt (elimizde), API-Football, Flashscore dizilişleri, kulüp resmi siteleri, Sofascore. Her birini **canlı istekle** test et (HTTP kodu + örnek alan), robots.txt ve giriş duvarını kontrol et. Daha önce kapalı ölçülenleri tekrar denemek için 1 istekten fazla harcama.
- Mevcut `player_impact_matrix.py` ile karşılaştır: pozisyon ağırlığı ve dakika ağırlığı eklenirse bizim veriyle ölçülebilir mi? `DAMPEN`/`MAX_IMPACT` için literatürde bir çapa var mı?

**C) Model sağlığı teşhisi (analizleri "sağlıklı" yapan şey)**
Kalibrasyon ölçütleri (Brier, log-loss, güvenilirlik eğrisi, ECE), walk-forward/ileriye dönük doğrulama, aşırı uyum (overfit) ve çoklu-karşılaştırma tuzağı, winner's curse, kapanış oranına karşı ölçüm (CLV) ve "piyasayı yenmek" için gereken minimum örneklem, bahis boyutlandırmada varyans ve iflas riski, veri sızıntısı (look-ahead) kontrolleri.

## 6. FAZ 3 — Uygulama (yalnız ölçülebilir olanlar)

- Faz 2'de "ölçülebilir" çıkan maddeleri etki/maliyet sırasına koy, **en çok 3 tanesini** uygula. Kalanı "sırada" listesine yaz.
- Her değişiklik: ayrı, geri alınabilir, **varsayılan KAPALI bayrak** arkasında (A/B karşılaştırması yapılabilsin). Mevcut davranış bayrak kapalıyken **bit düzeyinde aynı** kalmalı.
- Render kısıtı: numpy/pandas/scipy yok, kalıcı disk yok. Ağır hesap yerelde eğitilir, buluta **snapshot** gider (`dixon_coles_model.snapshot_yaz` kalıbı). Yeni bağımlılık eklemeden önce gerekçelendir.
- Dosya yerleşimi: futbol kodu/verisi `Oran analiz/.claude/agents` altında; global `bwm.py` yönlendirir.

## 7. FAZ 4 — Test ve kanıt

1. `python -m unittest discover -s . -p "test_*.py"` → **131 test yeşil kalmalı** (Render build komutu bu). Kırarsan düzeltene kadar devam et.
2. Her yeni davranış için ağ bağımsız birim testi yaz (tempfile, üretim verisine yazma yok).
3. **Walk-forward backtest:** arşivdeki günleri kronolojik böl, geçmişle eğit/ayarla, sonraki günde ölç. Aynı veride ayar + ölçüm yapma.
4. **Önce/sonra tablosu** (zorunlu çıktı):

   | Metrik | Önce | Sonra | n | Yorum |
   |---|---:|---:|---:|---|
   | Brier | | | | |
   | Log-loss | | | | |
   | Ortalama CLV % | | | | |
   | Pozitif CLV oranı | | | | |
   | ROI % (yerleşmiş) | | | | |
   | OYNA sayısı | | | | |

   `n` küçükse sonucu **"KANIT YOK"** diye etiketle — iyileştirme iddiası etme.
5. Canlı duman testi: bugünün bülteninde uçtan uca bir tur (`bwm.py derin --gun` veya ilgili katman), hata/çökme yok, çıktı beklenen biçimde.

## 8. Rapor (terminale, kompakt)

Sırayla: **(1)** Faz 0 sonucu — ölçüm hattı akıyor mu, hangi zincir onarıldı · **(2)** Faz 1 hata taksonomisi tablosu + korunan doğrular · **(3)** Faz 2 araştırma bulguları tablosu (kaynak | iddia | bizde ölçülebilir mi | karar) · **(4)** uygulanan 3 değişiklik + bayrak adları · **(5)** Faz 4 önce/sonra tablosu · **(6)** **dürüst sınırlar** — neyi ölçemedik, hangi katsayı hâlâ varsayım, hangi kaynak kapalı · **(7)** önerilen commit mesajı (push etme).

Dolgu yoktur. "Banko/garanti" dili yasaktır. Yüksek güven ≠ değer; her tahminin yanında EV'yi yaz.
