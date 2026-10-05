# BALİNA-ORAN BİRLEŞİK ANALİZ (BOA) — v0.1 taslak prompt
Durum: ARAŞTIRMA/KAYIT modu. Bahis üretmez; sinyal üretir ve loglar. (05.10.2026)

## 0. Temel tez (bunu değiştirmeden uygulama)
Balina parası doğrudan görünmez; **fiyatın paraya verdiği tepki** görünür.
Kazanç kaynağı "balinayı taklit etmek" DEĞİL, **dünya keskin pazarı yeniden fiyatlarken
Nesine'nin geç kalması** (bayat çizgi). Sinyal = dünya ile Nesine arasındaki FARK, hacmin kendisi değil.

## 1. Girdi (maç başına)
| Alan | Kaynak | Yok ise |
|---|---|---|
| Nesine açılış + güncel oran, zaman damgası | `nesine.py`, `data/iz/`, `kapanis_bekle.py` | katman çalışmaz |
| Dünya keskin pazar açılış + güncel | Betfair Exchange / sharp panel | katman çalışmaz |
| Eşleşen hacim (toplam + seçenek), dağılım %, ısı | okooo 必发指数 / odds-api.io | `hacim=YOK` etiketi, D hücresi kuralları |
| Haber: kadro, sakat/cezalı, hava | `fetch_tm.py`, `weather_signals.py` | `haber=bilinmiyor` |

## 2. Normalizasyon (atlanamaz)
1. Her kaynağı ayrı ayrı de-vig et (oransal; Nesine marjı 1X2'de ölçülen %13-17 → seçenek başına ~%5-7).
2. `p_w0, p_w1` = dünya keskin pazarın adil olasılığı (açılış, güncel).
3. `p_n1` = Nesine'nin güncel örtük olasılığı (marjlı) — EV için **oran** kullan, olasılık değil.
4. `Δp_w = p_w1 − p_w0` (yüzde PUAN). `Δp_n` aynı şekilde Nesine için.
5. `EV = p_w1 × O_n1 − 1`. Karar yalnız EV'ye bakar.

## 3. Hacim sınıfı
`V_pay` = seçeneğin hacmi / maçın toplam hacmi. `Z` = (toplam hacim − lig medyanı) / IQR.
NORMAL (Z<1) · YÜKSEK (1≤Z<2.5) · AŞIRI (Z≥2.5). Hacim yoksa sınıf = YOK.

## 4. Karar matrisi (çekirdek)
| # | Para | Dünya fiyatı | Nesine | Yorum | Aksiyon |
|---|---|---|---|---|---|
| **A** | YÜKSEK/AŞIRI | `Δp_w ≥ +2 puan` | kıpırdamadı (`\|Δp_n\| < 1`) | Bilgili para + bayat yerel çizgi | **Tek oynanabilir hücre.** EV ≥ +%2 ise OYNA adayı |
| **B** | YÜKSEK/AŞIRI | `Δp_w ≥ +2` | aynı yönde takip etti | Bilgi doğru, fiyat gitti | OYNAMA (EV ≤ 0) — sadece logla |
| **C** | YÜKSEK/AŞIRI | `\|Δp_w\| < 1` (emildi) | farketmez | Para bilgili DEĞİL (perakende yığılması); kitap memnun | NÖTR. Ters tarafa bahis kanıtsız → en fazla 0.25 Unit ESNEK ADAY |
| **D** | YOK/DÜŞÜK | `Δp_w ≥ +2` | farketmez | Hacimsiz hareket: limit düşürme, kadro haberi veya fantom | Haber doğrulanırsa A gibi işle; doğrulanmazsa BEKLE |

## 5. Zamanlama — ne zaman tara, ne zaman oyna
**Steam 2-10 dakikada biter.** Keskin kitabı saniyede izleyip saniyede bahis yapamayız; o yarışı
ücretsiz veriyle kaybederiz. Bizim saatimiz farklı: **Nesine'nin gecikmesi saatler sürer** (devlet
tekeli, yüksek marj, yavaş fiyatlama). Yani kovaladığımız şey steam değil, steam'in ardından
**açık kalan bayat fiyat**.

| Soru | Cevap |
|---|---|
| Sinyali görünce hemen mi oynayalım? | **Evet, aynı tarama turunda.** Beklemek = Nesine düzeltir ya da dünya daha da kayar; iki durumda da EV biter. |
| Maça yakın mı beklemeli? | Hayır, bekleme. Ama **T-6sa'ten önceki sinyale de girme**: erken piyasa düşük limitli ve gürültülü. |
| En verimli pencere | **T-2sa → T-30dk** (tüm hareketin ~%25'i burada) |
| Geçerli aralık | T-6sa … T-20dk. T-20dk'dan sonra Nesine zaten kapanışa yakın, fark kapanmış olur. |

Tarama planı (üniform 30 dk yerine kademeli, kredi dostu): **T-6sa, T-2sa, T-60dk, T-30dk**.
Mevcut otomasyonun radar kademeleri (60/30/15 dk) bu mantığa zaten uyuyor; balina için 15 yerine
T-6sa ve T-2sa kademeleri eklenir.

## 5b. Zamanlama ağırlığı (kanıt)
Fiyat hareketlerinin >%60'ı maç günü, ~%25'i son 2 saatte olur. Aynı büyüklükteki hareket
T-2h'te T-24h'ten daha bilgilidir. Anlık görüntü programı: T-24h, T-6h, T-2h, T-20dk, T-5dk (mevcut `kapanis_bekle.py`).
Haber tipi asimetrisi: piyasa SIRADAN habere az, SÜRPRİZ habere aşırı tepki verir → sürpriz haber sonrası A hücresi şişer, EV'yi 1 saat sonra tekrar ölç.

## 6. Pazar kapısı (çok önemli)
Katman YALNIZCA dünya pazarında likit olan pazarlarda çalışır: **1X2, Asya handikap, 2.5 Alt/Üst, KG**.
İY/MS, doğru skor, korner, kart, 4.5 Üst, İY 1.5 Üst → dünya hacmi/keskin fiyatı yok veya çöp.
Bu pazarlardaki taktikler **saf oran taktiği olarak kalır**, balina katmanı onlara UYGULANMAZ ve
"balina onayladı" denmez.

## 7. Sert kurallar
- Marj engeli: Nesine hiç kıpırdamasa bile `Δp_w` ~6 puandan küçükse EV genelde negatiftir. Hikâyeye değil EV'ye bak.
- "Balina var" güven puanını YÜKSELTMEZ. `hit_conf` formülü değişmez. Balina yalnız EV kapısına girer.
- Min oran 1.30; handikap kuralı (|H|−|F| > 3 = YÜKSEK RİSK); dengeli 1X2 = SKIP kuralları aynen geçerli.
- Tek kaynak hacim kanıt değildir. Kaynak sayısı < 2 ise etiket `ZAYIF`.
- ≥50 kayıtlı sinyalde CLV ölçülene kadar bu katman bahis açmaz (KAYIT modu); açarsa 0.25 Unit.

## 8. Çıktı tablosu (sabit format)
`Maç (tarih-saat) | Pazar | Nesine (aç→şu) | Dünya p (aç→şu) | Δp_w | Hacim/Dağılım | Hücre | EV | Durum`
Durum: `OYNA <kod> <oran>` · `KAYIT` · `OYNAMA` · `BEKLE`. Hücre yoksa satır yazılmaz.
Nihai Tahmin/Nihai Karar bölümü yok (bu bir tarama, BWM analizi değil).

## 9. Balina verisini okuma kılavuzu (必发指数 / Betfair endeksi)
Çin analiz ekolünün kullandığı dört alan — okooo çıktısındaki karşılıkları `price · volume · profit · hotIndex`:
| Alan | Ne demek | Nasıl okunur |
|---|---|---|
| 必发指数 (dağılım %) | Seçeneğin **gerçek eşleşen hacmi × anlık fiyat**, üç seçeneğe oranlanmış | Tek başına yön vermez; **değişimi** (初盘→临场, yani açılış→maç öncesi) okunur |
| 成交量 (volume) | Eşleşen para | Hacim sınıfı (bölüm 3) buradan; lig medyanına göre normalize **edilmeden** kullanılmaz |
| 盈亏指数 (profit) | Negatif = o seçeneği **satan** (lay) taraf kârda (genelde favori), pozitif = alan taraf kârda | Pozitif + yükselen hacim = sürpriz tarafına bilgili para olasılığı |
| 冷热指数 (hotIndex) | Avrupa ortalama oranı ile Betfair anlık fiyatının **iade-arındırılmış** farkı; düşük = piyasa o yöne daha sıcak | ≥5 "大热" (aşırı sıcak), 2–5 ılık, negatif soğuk |

**Dikkat — bu ekolün "大热必死" (aşırı sıcak favori ölür) kuralı folklordur.** Benimsenmez;
yalnız `hotIndex ≥ 5` olan seçenekleri etiketleyip sonuçlarını sayarız. ≥30 örnekte favorinin
gerçekleşme oranı piyasa örtük olasılığının altında kalmazsa kural **çöpe atılır**.
Çapraz doğrulama (ekolün tek sağlam tarafı): hacim + Asya handikap su seviyesi + Avrupa oranı
**aynı yöne** hareket ediyorsa sinyal, biri ters duruyorsa gürültü.

## 10. Ölçülen temel çizgi (05.10.2026, ilk anlık görüntü, n=60 seçenek)
- Nesine 1X2 marjı: **ort. %19,7** (bu örneklem Uluslar Ligi/FA Cup ağırlıklı; hafıza kaydındaki %13-17'nin üstünde).
- Dünya konsensüsüne göre Nesine EV'si: **ort. −%16,6**, pozitif olan **1/60**.
- Anlam: bayat çizgi sinyali için dünya adil olasılığının **çok** kayması gerekir; A hücresinin
  seyrek çıkması beklenen sonuçtur, eşik gevşetilmez.

## 11. Çalıştırma (v0.2, KAYIT modu)
`bwm.py balina tanila | kaydet [--lig 6] | rapor [--gun] [--hepsi] [--ev 2] | sinyal [--telegram]`
Kaynak: Nesine bülteni + The Odds API konsensüsü (`oddsapi.py`, Shin de-vig, pinnacle×3 / betfair_ex_eu×2).
Maliyet: 1 kredi/lig/anlık görüntü. İlk anlık görüntü temel çizgidir (tanım gereği sinyal üretmez).
Hacim tarafı kapalı → hiçbir satır OYNA demez.

## 12. Doğrulama protokolü
Her sinyal — oynanmasa bile — `history_log.py`'ye yazılır: `Δp_w`, hacim sınıfı, hücre, Nesine oranı,
dünya kapanış adil oranı. Metrik **CLV** (Buchdahl: ~50 bahiste anlamlılık), ikincil metrik ROI.
Yanlışlama koşulu: A hücresi sinyallerinin ortalama CLV'si ≤ 0 ise katman kapatılır.
