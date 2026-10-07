# -*- coding: utf-8 -*-
"""
AKSIYON - oran kirilmasindan "NE OYNANMALI" yonunu cikaran saf-Python yardimci katman.

Tez (BWM / bayat cizgi): bir secenegin orani FIRLADIYSA o secenekten para cikmis, akilli para
(sharp money) TAM KARSI tarafa akmistir; oran DUSTUYSE para dogrudan o secenege akmistir.
Bu modul yalniz bu esleme + etiketlemeyi yapar; esik/karar odds_watcher.py'de kalir.
Hicbir ag cagrisi yoktur (0 token, 0 kredi).

    import aksiyon
    aksiyon.pazar_etiketi("Handikapli Mac Sonucu 1", "2")   -> "Handikapli Mac Sonucu 2 (1:0)"
    aksiyon.oneri("Handikapli Mac Sonucu 1", "2", +2.38, "Gnistan", "Inter Turku")
        -> ("HMS 1 (Gnistan +1) veya Cifte Sans 1-X", "Akilli para ... Gnistan lehine kaymistir.")
"""
import re

MS_TERS = {"1": "MS 2 veya Çifte Şans X2", "2": "MS 1 veya Çifte Şans 1X",
           "X": "Çifte Şans 12", "0": "Çifte Şans 12"}
CS_TERS = {"1X": "MS 2", "1-X": "MS 2", "X2": "MS 1", "X-2": "MS 1",
           "12": "MS X", "1-2": "MS X"}
IKILI = {"Alt": "Üst", "Üst": "Alt", "Var": "Yok", "Yok": "Var", "Tek": "Çift", "Çift": "Tek",
         "Evet": "Hayır", "Hayır": "Evet"}


GUVENLI = {"1": "Çifte Şans 1X", "2": "Çifte Şans X2", "X": "Çifte Şans 12"}


def guvenli_alternatif(outcome):
    """Onerilen 1/X/2 yonunun daha dusuk riskli cifte-sans karsiligi (yoksa None)."""
    return GUVENLI.get((outcome or "").strip())


def _sov(market):
    """Pazar adinin sonuna yazilan ozel deger (handikap / cizgi). Yoksa None."""
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*$", market or "")
    return m.group(1).replace(",", ".") if m else None


def _hk(sov):
    """'1' -> '(1:0)' | '-1' -> '(0:1)'."""
    try:
        v = float(sov)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "(%g:0)" % v
    if v < 0:
        return "(0:%g)" % abs(v)
    return "(0:0)"


def _hk_takim(sov, takim):
    """'Gnistan +1' - handikabin hangi takima ait oldugunu okunur yazar."""
    try:
        v = float(sov)
    except (TypeError, ValueError):
        return takim or ""
    return "%s %+g" % (takim or "?", v)


def pazar_etiketi(market, outcome):
    """Telegram'da gosterilecek okunur pazar adi (handikapli pazarlarda cizgiyi parantezde verir)."""
    m, o = (market or "").strip(), (outcome or "").strip()
    if not m:
        return o
    sov = _sov(m) if "handikap" in m.lower() else None
    if sov:
        taban = m[:m.rfind(sov)].strip()
        return " ".join(x for x in (taban, o, _hk(sov)) if x)
    return ("%s %s" % (m, o)).strip()


def _ters(market, outcome, home, away):
    """Karsi taraf -> (oynanacak_yon, paranin_aktigi_taraf). Temiz karsi taraf yoksa (None, None)."""
    m, o = (market or "").strip(), (outcome or "").strip()
    lm, ev, dep = m.lower(), home or "ev sahibi", away or "deplasman"

    if " ve " in o.lower() or " ve " in lm:          # kombine pazar: temiz karsi secenek yok
        return None, None

    if "handikap" in lm and o in ("1", "2", "X", "0"):
        sov = _sov(m)
        if o == "1":
            return ("HMS 2 (%s) veya Çifte Şans X2" % _hk_takim(sov, dep)), dep
        if o == "2":
            return ("HMS 1 (%s) veya Çifte Şans 1X" % _hk_takim(sov, ev)), ev
        return "HMS 1 veya HMS 2 (beraberlik dışı)", "net sonuç"

    if o in IKILI:                                   # Alt/Üst, Karşılıklı Gol, Tek/Çift
        hedef = IKILI[o]
        yon = ("%s %s" % (m, hedef)).strip()
        if hedef in ("Alt", "Üst"):
            return yon, ("az gol beklentisi" if hedef == "Alt" else "çok gol beklentisi")
        return yon, hedef

    if o.replace(" ", "").replace("-", "") in ("1X", "X2", "12"):
        hedef = CS_TERS.get(o.replace(" ", ""), CS_TERS.get(o.replace(" ", "").replace("-", "")))
        return hedef, (hedef or "karşı taraf")

    if o in MS_TERS:                                 # Maç Sonucu / İlk Yarı Sonucu
        metin = MS_TERS[o]
        if "yarı" in lm:                             # İlk Yarı Sonucu pazarı
            metin = metin.replace("MS ", "İY ").replace("Çifte Şans", "İY Çifte Şans")
        hedef = {"1": "%s / beraberlik" % dep, "2": "%s / beraberlik" % ev,
                 "X": "net sonuç"}.get(o, "karşı taraf")
        return metin, hedef

    return None, None


def oneri(market, outcome, delta, home=None, away=None):
    """Oran hareketine gore aksiyon tavsiyesi.

    delta > 0 (oran yukseldi) -> para karsi tarafta: karsi secenek onerilir.
    delta < 0 (oran dustu)    -> para bu secenege akti: ayni secenek onerilir (fiyatin bir kismi gitti).
    -> (onerilen_yon, tek_cumle_gerekce). Yon None ise mesaja 'uzak dur' uyarisi yazilir.
    """
    etiket = pazar_etiketi(market, outcome)
    if delta < 0:
        return etiket, ("Akıllı para doğrudan bu seçeneğe aktı (oran düştü); yön doğrulandı "
                        "ama fiyatın bir kısmı gitti.")
    yon, taraf = _ters(market, outcome, home, away)
    if not yon:
        return None, ("Akıllı para bu seçenekten çıktı; kombine/özel pazarda temiz karşı seçenek "
                      "yok, bu seçeneğe girmeyin.")
    return yon, ("Akıllı para (Sharp Money) yoğun bir şekilde %s lehine kaymıştır." % taraf)
