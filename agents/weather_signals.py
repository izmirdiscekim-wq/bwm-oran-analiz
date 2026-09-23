"""
Hava durumu sinyali (Open-Meteo, ucretsiz, API key gerektirmez).

Kanit notu: Saat/gun bazli Over/Under etkisi buyuk olcude karistirici (TV slot
secimi takim kalitesiyle korele). Hava (sicaklik/ruzgar/yagis) ise mekanizmasi
makul tek degisken - ama etki kucuk. Bu yuzden burada sadece KUCUK bir xG
duzeltme carpani ve aciklayici not uretilir, guven puanini tek basina tasimaz.
"""
import os
import json
import requests
from datetime import datetime, timezone

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HEAVY_RAIN_MM = 2.0     # saatlik yagis (mm)
STRONG_WIND_KMH = 35.0
EXTREME_WIND_KMH = 50.0
EXTREME_COLD_C = -2.0
EXTREME_HOT_C = 34.0


def _load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def geocode(session, data_dir, city, country=None):
    if not city:
        return None
    cache_path = os.path.join(data_dir, "geo_cache.json")
    cache = _load(cache_path)
    key = f"{city}|{country or ''}".lower()
    if key in cache:
        return cache[key]

    try:
        r = session.get(GEOCODE_URL, params={"name": city, "count": 5, "language": "en"}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as e:
        print(f"  Uyarı: geocoding başarısız ({city}): {e}")
        return None

    pick = None
    for res in results:
        if country and res.get("country", "").lower() == country.lower():
            pick = res
            break
    pick = pick or (results[0] if results else None)
    out = None
    if pick:
        out = {"lat": pick["latitude"], "lon": pick["longitude"], "tz": pick.get("timezone")}
    cache[key] = out
    _save(cache_path, cache)
    return out


def weather_at_kickoff(session, data_dir, city, country, kickoff_iso):
    """kickoff_iso: '2026-09-19T12:00:00+03:00' (Iddaa, TR saati). Yerel saate cevrilir (timezone=auto)."""
    geo = geocode(session, data_dir, city, country)
    if not geo:
        return None

    try:
        kickoff = datetime.fromisoformat(kickoff_iso)
    except (TypeError, ValueError):
        return None

    kickoff_utc = kickoff.astimezone(timezone.utc)
    day = kickoff_utc.strftime("%Y-%m-%d")
    try:
        r = session.get(FORECAST_URL, params={
            "latitude": geo["lat"], "longitude": geo["lon"],
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "UTC", "start_date": day, "end_date": day,
        }, timeout=15)
        r.raise_for_status()
        hourly = r.json().get("hourly") or {}
    except Exception as e:
        print(f"  Uyarı: hava durumu alınamadı ({city}): {e}")
        return None

    utc_hour = kickoff_utc.strftime("%Y-%m-%dT%H:00")
    times = hourly.get("time") or []
    if utc_hour not in times:
        return None
    i = times.index(utc_hour)
    return {
        "city": city,
        "temp_c": hourly["temperature_2m"][i],
        "precip_mm": hourly["precipitation"][i],
        "wind_kmh": hourly["wind_speed_10m"][i],
        "source": "open-meteo-forecast",
    }


def weather_adjustment(weather):
    """(xg_carpani, not).

    Kanit: 7.528 maclik Poisson regresyonunda yagis-gol iliskisi anlamsiz (p=0.86).
    Bu yuzden yagis tek basina duzeltme yapmaz. Sadece ASIRI kosullar (cok guclu
    ruzgar, yogun yagis+ruzgar birlikte, ekstrem sicaklik) icin en fazla %3 dusus.
    Diger durumlar sadece bilgi notudur.
    """
    if not weather:
        return 1.0, None
    rain, wind, temp = weather["precip_mm"], weather["wind_kmh"], weather["temp_c"]
    desc = f"{temp}°C, {rain} mm/sa, {wind} km/sa"
    extreme = []
    if wind >= EXTREME_WIND_KMH:
        extreme.append(f"aşırı rüzgar ({wind} km/sa)")
    if rain >= HEAVY_RAIN_MM and wind >= STRONG_WIND_KMH:
        extreme.append("yoğun yağış + güçlü rüzgar")
    if temp <= EXTREME_COLD_C or temp >= EXTREME_HOT_C:
        extreme.append(f"ekstrem sıcaklık ({temp}°C)")
    if extreme:
        return 0.97, f"Aşırı hava koşulu: {', '.join(extreme)} -> xG x0.97 (kanıt zayıf, küçük düzeltme). [{desc}]"
    return 1.0, f"Hava: {desc} (düzeltme yok; yağış/saat etkisi için istatistiksel kanıt zayıf)."
