# -*- coding: utf-8 -*-
"""
ELO_TRACKER - multi_sport_analyzer icin lokal ELO + SoS (Strength of Schedule) motoru.

Dis veri kaynagina bagimli DEGIL: iddaa recent-matches'teki (msa_kaynak.oyunlar) own/pf/pa/rakip/ts
alanlarindan takim/oyuncu ELO'sunu hesaplar ve data/multi/elo_ratings.json'da sport-namespace'li
saklar (spor adi basketbol/hokey icin takim, tenis icin "tenis_<zemin>" icin oyuncu).

YAKLASIKLIK (durustce belirtilir, CALIBRATED tarzinda): rakibin ELO'su, konu takimin
replay'inden guncellenir; rakip kendi taramasinda SUBJECT olunca ayni mac bir kez daha
sayilabilir (kucuk cift-sayim riski, K dusuk tutuldugu icin etkisi sinirli). Bu, merkezi bir
fikstur/sonuc veritabani olmadan (ucretsiz kaynak yok) elde edilebilecek en iyi yaklasiklik.

Initial ELO Seed: bir takimin/oyuncunun hic ELO gecmisi yoksa (ilk kez goruluyor), duz 1500
yerine o anki MAC SONUCU (Mac Sonucu / win) piyasa oranindan (Shin fair p) turetilen bir
baslangic ELO'su kullanilir (seed_from_market) - "ligdeki ortalama oran degerinden hizli seed".
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msa_kaynak as K

BASE = 1500.0
K_FAKTOR = 20.0


def _yol():
    return os.path.join(K.VERI, "elo_ratings.json")


def _tum_oku():
    return K._oku(_yol(), {})


def _tum_yaz(d):
    K._yaz(_yol(), d)


def beklenen(elo_a, elo_b):
    """A'nin B'ye karsi kazanma beklentisi (standart ELO lojistigi)."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def oku(spor, takim):
    """-> mevcut ELO (float) ya da None (hic kaydi yok)."""
    k = _tum_oku().get(spor, {}).get(K.fold(takim))
    return k.get("elo") if k else None


def seed_from_market(p_fair_home):
    """Piyasanin (Shin) ev sahibi kazanma olasiligindan (p_fair_home) hizli baslangic ELO'su
    uretir: 1500 +- 400*logit(p)/2. p yoksa (None) (None, None) doner (seed uygulanmaz, duz 1500)."""
    if p_fair_home is None:
        return None, None
    p = min(max(p_fair_home, 0.03), 0.97)
    fark = 400.0 * math.log10(p / (1.0 - p))
    return BASE + fark / 2.0, BASE - fark / 2.0


def guncelle(spor, ev, dep, skor_h, skor_a, k=K_FAKTOR):
    """Tek bir bilinen sonucu iki takima da isler ve dosyaya yazar. -> (yeni_ev_elo, yeni_dep_elo)."""
    d = _tum_oku()
    blok = d.setdefault(spor, {})
    kh = blok.get(K.fold(ev), {"elo": BASE, "son_ts": 0, "n": 0})
    ka = blok.get(K.fold(dep), {"elo": BASE, "son_ts": 0, "n": 0})
    beklenen_h = beklenen(kh["elo"], ka["elo"])
    sonuc_h = 1.0 if skor_h > skor_a else (0.5 if skor_h == skor_a else 0.0)
    kh["elo"] += k * (sonuc_h - beklenen_h)
    ka["elo"] += k * ((1.0 - sonuc_h) - (1.0 - beklenen_h))
    kh["n"], ka["n"] = kh.get("n", 0) + 1, ka.get("n", 0) + 1
    blok[K.fold(ev)], blok[K.fold(dep)] = kh, ka
    _tum_yaz(d)
    return kh["elo"], ka["elo"]


def gecmisten_kur(spor, takim, oyunlar, ilk_seed=None):
    """Takimin kendi recent-matches listesini (msa_kaynak.oyunlar formatinda, 'rakip' alanli)
    kronolojik (eskiden yeniye) replay eder, SADECE henuz islenmemis (son_ts'den yeni) maclari
    isler (tekrarli tarama cift saymasin), rakip ELO'larini da yan etki olarak guncelleyip
    dosyaya yazar. ilk_seed: takim hic kayitli degilse kullanilacak baslangic ELO'su (bkz.
    seed_from_market); verilmezse 1500. -> guncel ELO (float)."""
    d = _tum_oku()
    blok = d.setdefault(spor, {})
    anahtar = K.fold(takim)
    kayit = blok.get(anahtar)
    if kayit is None:
        kayit = {"elo": ilk_seed if ilk_seed is not None else BASE, "son_ts": 0, "n": 0}
    if not oyunlar:
        blok[anahtar] = kayit
        return kayit["elo"]
    sira = sorted(oyunlar, key=lambda g: g.get("ts") or 0)
    degisti = False
    for g in sira:
        ts = g.get("ts") or 0
        if ts <= kayit.get("son_ts", 0):
            continue
        rakip = g.get("rakip") or "?"
        ra = K.fold(rakip)
        rk = blok.get(ra, {"elo": BASE, "son_ts": 0, "n": 0})
        beklenen_h = beklenen(kayit["elo"], rk["elo"])
        sonuc_h = 1.0 if g["pf"] > g["pa"] else (0.5 if g["pf"] == g["pa"] else 0.0)
        kayit["elo"] += K_FAKTOR * (sonuc_h - beklenen_h)
        rk["elo"] += K_FAKTOR * ((1.0 - sonuc_h) - (1.0 - beklenen_h))
        rk["n"] = rk.get("n", 0) + 1
        blok[ra] = rk
        kayit["n"] = kayit.get("n", 0) + 1
        kayit["son_ts"] = ts
        degisti = True
    blok[anahtar] = kayit
    if degisti:
        _tum_yaz(d)
    return kayit["elo"]


def sos(spor, oyunlar):
    """Takimin SON MACLARINDA karsilastigi rakiplerin ORTALAMA ELO'su - BASE (1500) farki.
    Pozitif -> takim ortalamadan guclu rakiplerle oynamis (kendi skor-ortalamasi hafife alinmis
    olabilir); negatif -> zayif rakiplerle oynamis. -> (delta, bilinen_rakip_sayisi)."""
    blok = _tum_oku().get(spor, {})
    elolar = [blok.get(K.fold(g.get("rakip") or ""), {}).get("elo", BASE)
              for g in (oyunlar or []) if g.get("rakip")]
    if not elolar:
        return 0.0, 0
    return (sum(elolar) / len(elolar)) - BASE, len(elolar)
