---
name: bwm-capraz-oran
description: Katman A4 - ÇAPRAZ-FİRMA oran taktiği backtest. Bir pazarın oranını tek bir bahis firmasına bakarak değil, aynı maç için birden fazla firmanın açtığı açılış VE kapanış oranlarına birlikte bakarak filtreler; her firma için ayrı ayrı "bu oranı kaç maça açmış, kaçı gerçekten tutmuş" ölçer ve firmaları karşılaştırır. "2,5 üst 1,60 açılmış, Nesine'de 1,85, başka yerde 1,95 — hepsinde tuttu mu", "bu oran taktiği başka sitelerde de başarılı mı", "%80 gelme ihtimali olan bant var mı", "açılış ve kapanış oranını kıyasla" isteklerinde kullan. Nesine bülteninde İLERİYE dönük tarama değil, GEÇMİŞ maçlarda doğrulama aracıdır (ileriye dönük tarama: bwm-taktik-tara).
---

# Çapraz-firma oran taktiği backtest

## Komut

```
bwm.py capraz --bugun --firma pinnacle|williamhill   # ADLI FIRMA sorgusu (asagi bak)
bwm.py capraz --bugun --cok-firma                   # 24 dis firmanin bugunku orani
bwm.py capraz --bugun [--gun YYYY-MM-DD] [--pencere 0,10] [--onay 3] [--onay-ou 1]
                      [--min-n 200] [--ev 0] [--min-oran 80] [--n 20] [--en-iyi]
                      [--profil ms2,a25]
bwm.py capraz --kesif [--min-n 400] [--onay 3]       # TÜM oran bantlarını otomatik tara, sırala
bwm.py capraz --firmalar <pazar>                     # bu pazarda veride hangi firmalar var
bwm.py capraz --kir <pazar> [--faz ac|kap] [--bant 1,55-1,95] [--esik 80] [--goster N]
bwm.py capraz --izgara <pazar> [--faz ac|kap] [--min-n 30] [--esik 80]
bwm.py capraz --kural "<pazar> <ac|kap> <seçici> <alt-üst>" [--kural ..] [--hedef p] [--ad "Ad"]
```

Pazarlar: `ms1` `msx` `ms2` `u25` (2,5 Üst) `a25` (2,5 Alt)
Seçiciler: `min` `max` `avg` `med` `n` `spread` (=max/min) veya firma adı (`pinnacle`, `bet365`, …)
Faz: `ac` = açılış oranı, `kap` = kapanış oranı.
Kaynak: `--kaynak cok` (varsayılan) = **161.039 maç / 11 ülke / 1993-2021, Türkiye dahil**,
1X2'de 12 firma. `--kaynak epl` = 12.705 maç Premier Lig (kapanış oranı daha zengin).
Filtreler: `--lig turkey`, `--yil 2010-2021` (örneklem-dışı doğrulama için).

## İLERİYE DÖNÜK KULLANIM: `--bugun` (oran profili eşleştirme)

Asıl kullanım budur. Bugünün Nesine+iddaa kaydından her maçın **oran profilini** alır,
161 bin maçlık geçmişte aynı profile benzeyen maçları bulur, **tüm sonuç dağılımını** verir
(MS1 %x, MS X %y, MS 2 %z, 2,5 Üst %u, 2,5 Alt %a) ve her seçenek için **bugünün gerçek
fiyatıyla EV** hesaplar.

**Profil tek bir kombinasyona bağlı değildir** — her maç için 14 kombinasyon taranır:
`ms1/msx/ms2` × `u25/a25` (6), 1X2 içi çiftler `ms1+ms2 / ms1+msx / msx+ms2` (3) ve tek
bacaklar (5). `--profil ms2,a25` ile tek bir profile sabitlenir. Çıktıda her satırın yanında
hangi profilden geldiği yazar.

Çıktı kolonları: `gecmis` (bu profilde o sonucun tarihsel frekansı), `basbs` (=1/gecmis,
başabaş oran), `fiyat` (bugünün en iyi Nesine/iddaa fiyatı), `EV` (= gecmis × fiyat − 1),
`ROI_t` (o seçeneğin bu alt kümedeki geçmiş kârlılığının t değeri).

**Aday sayılması için iki şart birlikte:** `EV > 0` **VE** `|ROI_t| ≥ 2`. Yalnız EV pozitif
olması yetmez — EV, doğrulanmamış bir frekans tahminini bugünün fiyatıyla çarpar.

### Fiyatlanabilirlik kuralı (en kritik kural)
Bir seçenek **ancak kendi pazarının oranı profilde sabitlenmişse** fiyatlanır. 1X2 üçlüsünün
toplamı 1 olduğu için iki bacak üçüncüyü de sabitler; 2,5 Alt ile Üst birbirinin tümleyenidir.
Bu kural olmadan "MS X 3,66 bandına koşullanıp MS 1'i fiyatlamak" gibi satırlar çıkıyordu:
koşul maçın ev sahibi gücünü hiç kısıtlamadığı için P(MS1) koşulsuz ortalamaya (%50) düşüyor,
bugünün 5,23 fiyatıyla çarpılınca **sahte +%165 EV** üretiyordu.

### Bu araçta düzeltilmiş üç tuzak
1. **Gol piyasası koşula girmediyse 2,5 EV'si hesaplanmaz.** Yalnız MS bandına koşullanmış
   bir 2,5 frekansı maçın gol fiyatını görmezden gelir ve sahte +%30 EV üretir. Birleşik
   koşul kurulamazsa (`yalniz MS bandi`) 2,5 satırları çıktıdan düşer.
2. **2,5 pazarında gerçek firma kapsamı ince** (bet365 8 bin, pinnacle 3,4 bin maç), bu yüzden
   o bacakta ~40 firma üzerinden türetilmiş `market_avg/market_max` (83 bin maç) da
   koşullandırmaya katılır; eşiği ayrı: `--onay-ou` (varsayılan 1).
3. **Bozuk kayıt süzgeci:** 1,00 gibi oranlar ve 15 üstü fiyatlar atılır (arşivde bayat
   kayıtlar "2,5 Alt 18,30 → EV +%430" gibi saçma satırlar üretiyordu).

### Geçerlilik sınırı
Geçmiş havuz **11 Avrupa ülkesinin lig maçları (1993-2021)**. Bugünün bülteninde U21,
Afrika Uluslar Kupası, CONCACAF gibi maçlar varsa profil eşleşmesi oran benzerliğine
dayanır, aynı futbol popülasyonuna değil. Bu maçlarda sonuç daha zayıf yorumlanır.

## Üç çıktı

1. **`--kir` FİRMA KIRILIMI** — "bu firma bu oranı kaç maça açmış, kaçı tutmuş". Satırlar
   **aynı maç kümesi değildir**; her firma farklı maçlara o oranı açmıştır. Asıl soru bir
   alttaki tabloda.
2. **MUTABAKAT tablosu** (`--kir` çıktısının içinde) — aynı maça kaç firma birlikte o bandı
   açmış ve o maçlar tutmuş mu. `>= 1 / >= 2 / >= 3 firma` satırları. **Çapraz-firma
   onayının bilgi taşıyıp taşımadığı burada görülür:** onay sayısı arttıkça edge büyümüyorsa
   mutabakatın değeri yoktur.
3. **`--kesif` OTOMATİK TARAMA** — kayan pencereyle (genişlik 0,15 / adım 0,05) 1,20-4,00
   arası tüm bantlar × tüm pazarlar × tüm onay seviyeleri. **Bonferroni düzeltmesi** uygular
   ve `ROI_t`'ye göre sıralar. Tek bant elle denemek yerine bu kullanılır.
4. **`--izgara`** — her firma × her oran bandı ızgarası; eşiği (`--esik`, varsayılan %80)
   geçen hücreler `*`, ayrıca z≥2 olanlar `**` ile işaretlenir.

## Kolonlar

| Kolon | Anlamı |
|---|---|
| `n` | bu firmanın bu bandı açtığı maç sayısı |
| `tutan` / `isabet` | gerçekten o sonuçla biten maç sayısı ve yüzdesi |
| `basbs` | başabaş oran = 1/isabet. Bu isabet yüzdesi **ancak bu orandan yüksek fiyatta** kâr eder |
| `ort.oran` | o maçlarda ortalama oran |
| `edge/nEdge` | isabet% − piyasanın ima ettiği olasılık (puan) / kaç maç üzerinden hesaplandığı |
| `ROI` | maçın **en iyi Avrupa fiyatıyla** birim bahis getirisi |
| `ROI_t` | **KARAR BUNA GÖRE VERİLİR.** ROI'nin kendi getiri dağılımından hesaplanan t değeri; de-vig varsayımı taşımaz |
| `z` | edge'in anlamlılığı — **karar için KULLANILMAZ**, bilgi olarak basılır (aşağıdaki yanlılık) |

## Zorunlu yorum kuralları

- **"15 maçta 14'ü geldi" kanıt DEĞİLDİR.** İki sebep: (a) bant zaten olasılığı ima eder —
  1,60-1,95 bandındaki 2,5 Üst ~%55-62 olasılık demektir, %58 tutması **sıfır** avantajdır;
  (b) n=15'te %93 isabet rahatlıkla şanstır. Her zaman `edge` ve `z` ile birlikte okunur.
- **Yüksek isabet ≠ değer.** `basbs` kolonu bunun testidir: isabet %80 ise başabaş oran 1,25'tir;
  fiyat 1,25'in altındaysa %80 isabetle bile zarar edilir.
- **Fiyat uyarısı:** ROI maçın **en iyi Avrupa fiyatıyla** hesaplanır. İddaa/Nesine fiyatı
  `--kir` çıktısındaki **en kötü (min)** satırının bile altındadır (ölçülen iddaa marjı %13-17,
  Nesine iddaa API'sinin ~%4,5 altında). +%4 ROI'li bir bant Nesine fiyatıyla eksiye düşer.
- Açılış ve kapanış **ayrı ayrı** çalıştırılır (`--faz ac`, sonra `--faz kap`) ve kıyaslanır.
  Bir bant yalnız bir fazda çalışıyorsa bu sağlam bir sinyal değildir.

## Veri ve sınırları

- Kaynak: football-data.co.uk verisinin GitHub aynası (`AnishKhetani/premier-league-data`).
  Asıl site TR'den **DNS ile kapalı**, ayna erişilebilir. Veri `data/capraz/` altına inilir,
  `--yenile` ile tazelenir.
- **12.705 maç, Premier Lig, 1993-94 → 2025-26**, açılış + kapanış oranı.
- Firma sayısı pazara göre değişir: `ms1/msx/ms2` için **20 gerçek firma** (açılış) / 14 (kapanış);
  `u25/a25` için yalnız **4** (bet365, pinnacle, betfair_ex, gamebookers).
- `market_*` ve `betbrain_*` satırları **gerçek firma değil**, ~40 firma üzerinden türetilmiş
  ortalama/maksimum sütunlarıdır; `*` ile işaretlenir.
- **Bu veride İddaa ve Nesine YOK.** Yöntem burada doğrulanır; İddaa/Nesine oranları ancak
  kendi ileriye dönük arşivimizle (`bwm.py arsiv`) eklenebilir.
- Yalnız 1X2 ve 2,5 Alt/Üst. İY/MS, doğru skor, korner, 4,5 Üst gibi pazarlar bu veride
  (ve satın alınabilen hiçbir geçmiş feed'inde) yok — onlar yalnız kendi arşivimizle test edilir.

## Ölçülen yanlılık: `edge`/`z` kolonlarına güvenilmez

Oransal de-vig (`p = (1/o) / Σ(1/oᵢ)`) favorinin olasılığını **sistematik olarak küçük
gösterir**, çünkü bahis firmaları marjı eşit dağıtmaz — uzun atış tarafına daha çok yükler
(favori–uzun atış yanlılığı). Sonuç: favori bantlarında **sahte +4-5 puan edge**. Ölçüldü:
MS1 açılış 1,30-1,45 bandı 161 bin maçta edge +4,8 puan ve **z=7,05** veriyor, ama aynı
bandın **ROI'si +%0,3** — yani kâr yok. Bu yüzden sıralama ve karar `ROI_t` üzerinden yapılır.

## Ölçülmüş sonuçlar (06.10.2026)

### 161.039 maçlık tam tarama
- `--kesif --min-n 400 --onay 3`: Bonferroni eşiği ~3,9. **En iyi ROI_t = 2,33 → geçen bant YOK.**
- Tutarlı tek küme: **MS2 (deplasman favorisi) açılış 1,40-1,65** — birden fazla örtüşen bant,
  hepsi pozitif. Literatürdeki ev sahibi yanlılığıyla uyumlu. Önceden belirlenmiş tek hipotez
  olarak örneklem-dışı sınandı:

| Küme | n | isabet | ROI | ROI_t |
|---|---|---|---|---|
| Tümü 1993-2021 (≥4 firma) | 2.444 | %64,6 | +%2,5 | +1,63 |
| Örneklem-içi 1993-2009 | 1.380 | %65,0 | +%3,6 | +1,74 |
| **Örneklem-dışı 2010-2021** | 1.064 | %64,1 | **+%1,1** | **+0,48** |
| **Türkiye Süper Lig** | 178 | %62,9 | +%0,8 | +0,14 |

  **Örneklem dışında eriyor, Türkiye'de hiç yok → oynanabilir değil.** Taktik kaydedilmedi.

### İleriye dönük ilk çalıştırma (06.10.2026 bülteni)
121 maç ölçüldü, 493 seçenek fiyatlandı. **EV > 0 olan 51 seçenek (%10,3)**, ama bunların
yalnız **2'si ROI_t ≥ 2** şartını da geçti:

| Maç | Seçenek | Profil | n | geçmiş | fiyat | EV | ROI_t |
|---|---|---|---|---|---|---|---|
| Angola - Malavi | 2,5 Üst | MS1 1,20 + 2,5Ü 2,02 | 380 | %55,3 | 2,18 | +%20,5 | +2,25 |
| Huddersfield - Rochdale | MS 1 | MS1 1,16 + 2,5Ü 1,26 | 374 | %89,8 | 1,18 | +%6,0 | +2,59 |

### Tüm profiller taranmış çalıştırma (06.10.2026 bülteni)
138 maç, **2.726 (maç × profil × seçenek)** test. EV > 0 olan 367 (%13,5), bunlardan
**ROI_t ≥ 2 olan 6 aday**. Şans eseri ~62 tanesinin t≥2 vermesi beklenirdi → **6, gürültünün
altında**, yani genel tablo sinyal göstermiyor.

| Maç | Seçenek | Profil | n | geçmiş | fiyat | EV | ROI_t |
|---|---|---|---|---|---|---|---|
| Angola - Malavi | 2,5 Üst | MS1 1,20 + 2,5Ü 2,02 | 380 | %55,3 | 2,18 | +%20,5 | +2,25 |
| Galler U21 - Avusturya U21 | MS 2 | MS2 1,55 + 2,5A 1,67 | 378 | %66,4 | 1,67 | +%10,9 | +2,09 |
| Ilkeston Town - Guiseley | MS 2 | MS2 1,58 + 2,5A 1,69 | 497 | %64,8 | 1,71 | +%10,8 | +2,08 |
| Newport - Plymouth | MS 2 | MS2 1,44 + 2,5A 2,13 | 625 | %70,4 | 1,51 | +%6,3 | +2,16 |
| Huddersfield - Rochdale | MS 1 | MS1 1,16 + 2,5Ü 1,26 | 374 | %89,8 | 1,18 | +%6,0 | +2,59 |
| Raufoss - IK Start | MS 2 | MS2 1,34 + 2,5A 2,01 | 409 | %74,6 | 1,36 | +%1,4 | +2,41 |

**Dikkat çeken:** 6 adayın **4'ü MS 2** ve hepsi "MS2 favorisi + 2,5 Alt" profilinden. Bu,
geçmiş taramadaki deplasman-favorisi kümesiyle aynı yöne işaret ediyor (ev sahibi yanlılığı).
Tutarlı ama sayı gürültü sınırının altında — ileriye doğrulanması gerekir.

### Premier Lig alt kümesi
- Hiçbir firma × bant hücresi **%80 isabete ulaşmıyor**. En yüksek: 2,5 Üst'te %67
  (pinnacle, 1,30-1,55 kapanış, edge +0), MS1'de %71 (1,30-1,55, edge +3). Yüksek isabetli
  hücreler zaten en düşük oran bantlarıdır — oran olasılığı fiyatlıyor.
- Tek tekrarlanan sinyal: **2,5 Üst kapanış 1,70-1,85** bandı. Onay arttıkça edge büyüyor:
  ≥1 firma +2,8 puan / ROI +%3,0 (n=660) → ≥2 firma +3,3 / +%4,0 (n=450) → ≥3 firma +6,6 / +%10,9 (n=75).
  **Ama z = 1,4 → istatistiksel kanıt yok** ve ROI en iyi Avrupa fiyatına dayanıyor;
  Nesine fiyatıyla eksiye düşer. Doğrulanmamış aday olarak kalır.
