"""
ORTAK KARAR CEKIRDEGI (live.py + derin.py + settle.py): harman olasilik, OYNA/SPEKULATIF/PASS kademeleri, bahis kaydi.

NEDEN: eski kural = harman p (%70 piyasa + %30 model) + model piyasadan en fazla %30 (goreli) ayrisabilir + OYNA icin harman EV >= +%3.
Uc kosul birlikte ULASILAMAZ: en yuksek harman EV = (1 + 0.30*0.30) / M - 1  (M = pazarin marj carpani) -> M=1.06 icin +%2.8 < %3.
Sonuc: 170 canli + 162 gunluk analizin HICBIRINDE OYNA cikmadi (test_decisions.py bunu ve yeni kuralin farkini olcer).

YENI KURAL (shrinkage / Bayes mantigi; esikler simulasyonla secildi, test_decisions.py):
  - Model agirligi KANITA baglidir: iki bagimsiz model (A: xG'li Dixon-Coles, B: zaman agirlikli Poisson) piyasaya gore AYNI yonde
    (>= %3 goreli) sapiyorsa 'teyitli' %40; ters yondeyse 'celiskili' %15; tek model %30. `scale` (0-1): zaman / orneklem carpani
    (canlida kalan sure ile azalir: mac ilerledikce piyasa canli bilgiyi zaten icerir).
  - OYNA: teyitli iki model + harman EV >= +%0.5, ya da tek model + harman EV >= +%1 (tek model daha gurultulu; derin.py cok sayida
    iliskili secenek taradigi icin tek-model secenegi OYNA yapmaz, en fazla SPEKULATIF: en-iyi-secim yanliligi); model-piyasa sapmasi <= %30.
  - EV > %25 = veri hatasi supheli (bayat skor/oran), oynanmaz. SPEKULATIF: harman EV >= -%3 (model celiskili degilse). Digeri PASS.
  - Kasa: risk seviyesi (Dusuk/Orta/Yuksek) -> kesirli Kelly (Yuksek 1/8, digerleri 1/4); stake yalniz harman EV > 0 iken (kullanici prompt'u: 'sadece pozitif value oner');
    OYNA icin min oran = EV esiginin korundugu en dusuk oran.
  - ESNEK ADAY (hic OYNA yokken, kullanici istegi): ayri kademe, model EV >= +%2 + harman EV >= -%6 (canli -%8, marj engeli yok), 0.25 Unit sabit; bkz. flex_ok.
DURUST SINIR: pazar marji (~%4-8) bir modelin gercek ustunlugu olmadan asilamaz; OYNA'nin BASARISI yalnizca `bwm.py sonuc` ile
(kayitli bahisler + Flashscore skorlari) olculebilir. Simulasyonda bile bilgili modellerin gercek ROI'si ~ -%2..+%3'tur.
"""
import io
import os
import json
import math
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BETS_LOG = os.path.join(DATA_DIR, "bets_log.jsonl")

MAX_REL_DIV = 0.30
MIN_DEV = 0.03
W_BASE, W_CONFIRMED, W_CONFLICT = 0.30, 0.40, 0.15
EV_PLAY_PAIR, EV_PLAY_SINGLE, EV_SPEC, EV_SUSPECT = 0.005, 0.01, -0.03, 0.25


def blend(pm, models, scale=1.0):
    """pm: piyasa adil olasiligi; models: bagimsiz model olasiliklari (None'lar atilir); scale: agirlik carpani (0-1).
    Doner (p_harman, w, teyit, p_model); teyit: 'teyitli' | 'celiskili' | 'tek' | 'notr' | 'yok'."""
    ms = [m for m in models if m is not None]
    if not ms or not pm:
        return pm, 0.0, "yok", None
    pmod = sum(ms) / len(ms)
    if len(ms) == 1:
        st, w = "tek", W_BASE
    else:
        d = [(m - pm) / pm for m in ms[:2]]
        if d[0] * d[1] > 0 and min(abs(d[0]), abs(d[1])) >= MIN_DEV:
            st, w = "teyitli", W_CONFIRMED
        elif d[0] * d[1] < 0 and max(abs(d[0]), abs(d[1])) >= MIN_DEV:
            st, w = "celiskili", W_CONFLICT
        else:
            st, w = "notr", W_BASE
    w *= scale
    return (1 - w) * pm + w * pmod, w, st, pmod


def rel_div(pmod, pm):
    return abs(pmod - pm) / pm if pm and pmod is not None else None


def decide(ev, teyit, rdiv=None, blocked=None, allow_single=True):
    """(kademe, neden): kademe 'OYNA' | 'SPEKÜLATİF' | 'PASS'; neden yalniz PASS'ta dolu. allow_single=False: tek-model en fazla SPEKÜLATİF."""
    if blocked:
        return "PASS", blocked
    if teyit == "yok":
        return "PASS", "bağımsız model yok"
    if rdiv is not None and rdiv > MAX_REL_DIV:
        return "PASS", f"model-piyasa çelişkisi (%{rdiv * 100:.0f} > %{MAX_REL_DIV * 100:.0f})"
    if ev > EV_SUSPECT:
        return "PASS", f"EV {ev:+.0%} > %{EV_SUSPECT * 100:.0f}: veri hatası şüphesi (bayat skor/oran)"
    need = EV_PLAY_PAIR if teyit == "teyitli" else EV_PLAY_SINGLE
    if (teyit == "teyitli" or (teyit == "tek" and allow_single)) and ev >= need:
        return "OYNA", ""
    if ev >= EV_SPEC and teyit != "celiskili":
        return "SPEKÜLATİF", ""
    return "PASS", f"harman EV {ev:+.1%} < %{need * 100:.1f}"


def units(p, odds, cap=2.0, floor=0.25, frac=0.25):
    """Kesirli Kelly (varsayilan 1/4), 1 Unit = kasa %1."""
    k = (p * odds - 1) / (odds - 1) if odds > 1 else 0.0
    return max(floor, min(frac * k * 100, cap))


# --- Risk seviyesi + Kelly kesri (kullanici prompt'u: risk Dusuk/Orta/Yuksek; 1/4 veya 1/8 Kelly) ---
KELLY_FRAC = {"Düşük": 0.25, "Orta": 0.25, "Yüksek": 0.125}


def risk(p, odds, teyit, rdiv=None, conf=None, live=False):
    """'Düşük' | 'Orta' | 'Yüksek'. Puan: uzun sans (p<%35 ya da oran>=4: +3 = dogrudan Yuksek; p<%50 ya da oran>=2.5: +1), teyitsiz model (+1),
    model-piyasa sapmasi > %20 (+1), guven < 5 (+1), canli (+1). 0 = Dusuk, 1-2 = Orta, >=3 = Yuksek. Yuksek riskte kesir 1/8
    (tahmin iyimserse uzun sans stake'i en cok kasa dususu yaratir: test_decisions.py Kelly simulasyonu)."""
    pts = 3 if (p < 0.35 or odds >= 4.0) else 1 if (p < 0.50 or odds >= 2.5) else 0
    pts += (teyit != "teyitli") + (rdiv is not None and rdiv > 0.20) + (conf is not None and conf < 5) + bool(live)
    return "Düşük" if pts == 0 else "Orta" if pts <= 2 else "Yüksek"


def stake(p, odds, rk, cap=2.0):
    """Risk seviyesine gore kesirli Kelly (Dusuk/Orta 1/4, Yuksek 1/8); min 0.25 Unit."""
    return units(p, odds, cap=cap, frac=KELLY_FRAC[rk])


HC_CALIB = 0.02      # kalibrasyon payi: 348 sonuclanan bahiste p %70-80 bandinda gercek isabet ort. p'den 1-2 puan dusuk (canli %73 / %74); %60-70'te -7, %45-60'ta -20 (asiri iyimser)
HC_MIN = 8


def hc_calib(p):
    """Kalibrasyon payi: p >= %75'te HC_CALIB (%2); altinda p dustukce artar: %2 + 0.5 x (%75 - p) (p %65 -> %7, p %54 -> %12). KANIT (348 sonuclanan bahis, 2026-09-20): p %70-80 -> tahmin %74 /
    gercek %73; p < %70 (n=71) tahmin %61 / gercek %50 (-11 puan); oran >= 1.30 ve p %60-80 (n=44) tahmin %65 / gercek %55, ROI -%24: dusuk olasilikta tahminler ~10 puan iyimserdi."""
    return HC_CALIB + max(0.0, 0.75 - p) * 0.5


def hit_conf(ps, flags=()):
    """ISABET GUVENI 1-10 (kullanici: '8/10 ve uzeri guvenli secenekler'): secenegin TUTMA olasiligina duyulan korumaci guven; DEGER/EV'den BAGIMSIZDIR (EV ayri raporlanir).
    ps = bagimsiz olasilik kestirimleri (piyasa de-vig, Model A, Model B, canlida model, varsa konsensus); p_c = min(ps) - hc_calib(min(ps)); puan = asagi yuvarlanmis p_c x 10
    (8/10 = her kaynak >= %82 -> korumaci p >= %80; 7/10 >= %73; 6/10 >= %66; 9/10 >= %92; 10 icin p_c >= %97). Ceza: kaynaklar arasi fark > %8: -2, > %5: -1. Bayrak (xG celiskisi, tutarsiz piyasa, kucuk
    orneklem, toplam gol ayrismasi...) varsa en fazla 6; 2 kaynak en fazla 8, 1 kaynak en fazla 5. KALIBRASYON: p %70-80 bandi dogrulandi (n=270, %74 -> %73); >= %80 bandi HENUZ dogrulanmadi
    (bets_log 'YUKSEK' kademesi biriktikce `bwm.py sonuc` puan basina isabeti olcer, n >= 30)."""
    ps = [p for p in ps if p is not None]
    if not ps:
        return 1
    pc = min(ps) - hc_calib(min(ps))
    c = 10 if pc >= 0.97 else int(pc * 10 + 1e-9)
    spread = max(ps) - min(ps)
    c -= 2 if spread > 0.08 else 1 if spread > 0.05 else 0
    if flags:
        c = min(c, 6)
    if len(ps) < 3:   # 9-10 icin >= 3 kestirim; 2 kestirim en fazla 8, tek kestirim en fazla 5
        c = min(c, 8 if len(ps) == 2 else 5)
    return max(1, min(10, c))


def hc_min_source(n):
    """n/10 isabet guveni icin her kaynagin gereken en dusuk olasiligi (kalibrasyon payi dahil), ornek 8 -> 0.82, 7 -> 0.73, 6 -> 0.66."""
    return next((round(i / 200, 3) for i in range(60, 200) if hit_conf([i / 200] * 3) >= n), 1.0)


def min_odds(p, teyit="teyitli"):
    """OYNA esiginin (harman EV >= +%0.5 / tek model +%1) korundugu en dusuk oran; oran bunun altina inerse OYNAMA."""
    need = EV_PLAY_PAIR if teyit == "teyitli" else EV_PLAY_SINGLE
    return math.ceil((1 + need) / p * 100 - 1e-9) / 100


def spec_units(ev):
    """'Sadece pozitif value oner': SPEKULATIF'te yalniz harman EV > 0 ise 0.25 Unit, aksi izleme (0). Kayit/olcum degismez."""
    return 0.25 if ev > 0 else 0.0


# --- ESNEK ADAY (kullanici istegi: hic OYNA yokken kriterleri BIRAZ esnet) ---
# SPEKULATIF tabani (harman EV >= -%3) yerine MODEL EV >= +%2 (kullanici prompt'unun 'value' tanimi: model p x oran - 1) ve harman EV >= -%6 (canli -%8:
# canli marj ~%17, piyasa payi tek basina ~-%14 EV); canlida marj engeli kalkar (bedel EV'de gorunur). Tek model (2. model yok) kabul, bu yuzden
# model-piyasa sapmasi siniri OYNA'dan (%30) SIKI: %25. KALAN guvenlik engelleri: celiskili model, veri hatasi suphesi (EV > %25), bayat/tutarsiz piyasa,
# canli dakika penceresi, uzun sans (p < %25). Stake sabit 0.25 Unit. Harman EV <= 0 => beklenen ROI ~0/negatif; ayri kademe olarak kaydedilir (`bwm.py sonuc`).
FLEX_MODEL_EV, FLEX_EV_PRE, FLEX_EV_LIVE, FLEX_REL_DIV, FLEX_MIN_P, FLEX_UNITS = 0.02, -0.06, -0.08, 0.25, 0.25, 0.25


def flex_ok(ev, pmod, p, odds, teyit, rdiv, live=False):
    """True: secenek ESNEK ADAY sartlarini saglar (blocked/kademe kontrolu cagirana ait). pmod = model olasiligi (modellerin ortalamasi)."""
    if pmod is None or rdiv is None or teyit in ("yok", "celiskili") or rdiv > FLEX_REL_DIV:
        return False
    return pmod * odds - 1 >= FLEX_MODEL_EV and (FLEX_EV_LIVE if live else FLEX_EV_PRE) <= ev <= EV_SUSPECT and p >= FLEX_MIN_P


def flex_min_odds(pmod):
    """Model EV'nin +%2'de kaldigi en dusuk oran; oran bunun altina inerse ESNEK aday da gecersiz."""
    return math.ceil((1 + FLEX_MODEL_EV) / pmod * 100 - 1e-9) / 100


_lock = threading.Lock()


def log_bet(entry):
    """OYNA/SPEKULATIF bahisleri data/bets_log.jsonl'e yazar (sonuc takibi: bwm.py sonuc)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock, io.open(BETS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
