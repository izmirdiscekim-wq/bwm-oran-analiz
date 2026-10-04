---
name: bwm-canli-bahis
description: Katman 5 - şu an OYNANAN (canlı) futbol maçı için oyun-durumu modeli + de-vig'li canlı piyasa karşılaştırması; OYNA / OYNAMA kararı. "canlı maçta ne yapayım", "şu an oynanan X maçı", "cash out/ek bahis" isteklerinde kullan.
---

# Canlı bahis (Katman 5)
`cd C:\Users\Tuffy\.claude\agents; python bwm.py canli <takım>` (≈12 satır). Geçmiş: `canli --gecmis`. Kayıt: `data/live_log.jsonl`.

## Dürüst çerçeve (kullanıcıya söyle)
- "Kanıtlanmış yüksek başarı oranlı canlı taktik" YOKTUR. Canlı piyasa hızlı ve marjı yüksek; tutarlı avantaj bulunursa nadirdir. **OYNAMA en sık doğru sonuçtur.**
- Temel yöntem literatürdendir (Dixon & Robinson 1998 zamana bağlı gol yoğunluğu, Shin de-vig, fraksiyonel Kelly); oyun-durumu/kırmızı kart çarpanları (±%7-25) VARSAYIMDIR, kanıtlanmış değildir.

## Nasıl çalışır
1. Canlı durum (dk, skor, korner, kart) → 2. canlı 1X2 + Alt/Üst + KG oranları Shin ile arındırılıp Poisson(kalan) modeline uydurulur (piyasa-türevli kalan gol) → 3. kayıtlı ön maç λ/μ, kalan süre ve oyun durumuna göre ölçeklenir → 4. harman p = %70 piyasa + %30 ön maç; EV = p×oran−1.

## Filtreler (hepsi zorunlu; biri tutmazsa OYNAMA)
- Oran ≤1.01 (askıda), set marjı 1.02-1.30 dışı (bayat), çözülmüş çizgi → küme elenir. Piyasa uyum RMSE >0.03 → tutarsız.
- Piyasa p <%10 (uzun şans) ya da ön maç p piyasadan göreli >%30 ayrışıyor → "ÇELİŞKİ", değer sayılmaz.
- Dakika 5-75 dışı; ön maç modeli yoksa (bağımsız bilgi yok); harman EV <%3.
- Stake ≤0.5 Unit (canlı tavan), çeyrek-Kelly. Ekrandaki oran çıktıdan farklıysa OYNAMA (akış gecikebilir).

## YOK
Canlı korner/kart pazarları fiyatlanmaz; şut/xG akışı yok; gol dakikası ve sakatlık/oyuncu değişikliği modele girmez. Kalibrasyon: kayıtlara maç sonu skoru henüz işlenmiyor (sonraki adım).
