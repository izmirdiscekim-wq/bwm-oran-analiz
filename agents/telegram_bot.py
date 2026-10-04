"""Telegram polling bot for the existing BWM odds-tactic CLI."""
from dotenv import load_dotenv
load_dotenv()

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import requests


AGENT_DIR = Path(__file__).resolve().parent
MAX_MESSAGE_LENGTH = 3900
SCAN_TIMEOUT_SECONDS = 480
AUTO_INTERVAL_SECONDS = 5 * 60
RADAR_ASAMALARI = (("60", 60 * 60, 30 * 60, "Maçın başlamasına 1 saat kaldı. Oran analize uyuyor."),
                   ("30", 30 * 60, 15 * 60, "Maçın başlamasına yarım saat kaldı. Oran analize uyuyor."),
                   ("15", 15 * 60, 0, "Maçın başlamasına 15 dakika kaldı. Oran analize uyuyor."))
ANALYZED_FILE = AGENT_DIR / "analyzed_matches.json"
TEST_TARA = "__test_tara__"
TAKTIK_ACILIS = "__taktik_acilis__"
OPENING_TRACKER = AGENT_DIR / "opening_sent_tracker.json"
OPENING_TITLE = "🎯 Haftalık Bülten Açılış Oranları Analizi"
_tracker_lock = threading.Lock()


def parse_allowed_user_ids(value):
    user_ids = {int(part.strip()) for part in value.split(",") if part.strip()}
    if not user_ids:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS en az bir sayısal kullanıcı ID'si içermeli.")
    return user_ids


def parse_command(text):
    parts = shlex.split(text)
    if not parts:
        return "", []
    command = parts[0].split("@", 1)[0].lower()
    return command, parts[1:]


def split_message(text, limit=MAX_MESSAGE_LENGTH):
    chunks = []
    while len(text) > limit:
        boundary = text.rfind("\n", 0, limit)
        if boundary < limit // 2:
            boundary = limit
        chunks.append(text[:boundary])
        text = text[boundary:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def run_cli(arguments):
    command = [sys.executable, str(AGENT_DIR / "bwm.py"), *arguments]
    try:
        result = subprocess.run(
            command,
            cwd=AGENT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Tarama zaman aşımına uğradı. Daha dar bir maç aralığıyla tekrar deneyin."

    output = result.stdout.strip()
    if result.returncode != 0:
        error = result.stderr.strip() or "Bilinmeyen CLI hatası."
        return f"Tarama başarısız (çıkış kodu {result.returncode}):\n{error}"
    return output or "Komut tamamlandı; gösterilecek maç bulunamadı."


def response_for_update(update, allowed_user_ids):
    message = update.get("message", {})
    user = message.get("from", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private":
        return None
    if user.get("id") not in allowed_user_ids:
        return f"Bu Telegram hesabının ID'si {user.get('id')} izin listesinde değil. agents/.env içindeki TELEGRAM_ALLOWED_USER_IDS değerini kontrol edin."

    text = message.get("text", "")
    try:
        command, arguments = parse_command(text)
    except ValueError:
        return "Komut biçimi okunamadı. /yardim yazın."

    if command in ("/start", "/yardim", "/help"):
        return "Komutlar:\n/taktik - aktif oran taktiklerini Nesine bülteninde tara\n/taktik <ad> - adı verilen taktiği tara\n/taktikler - kayıtlı taktikleri listele\n/taktik_acilis - bültendeki açılış oranı taktik maçlarını raporla"
    if command == "/test_tara":
        return TEST_TARA
    if command == "/taktik_acilis":
        return TAKTIK_ACILIS
    if command == "/taktikler":
        return run_cli(["taktik", "--liste"])
    if command == "/taktik":
        cli_arguments = ["taktik", "--telegram"]
        if arguments:
            cli_arguments.extend(["--ad", " ".join(arguments)])
        return run_cli(cli_arguments)
    return "Bilinmeyen komut. /yardim yazın."


def telegram_call(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=10)
    except requests.exceptions.SSLError:
        raise RuntimeError("Telegram TLS sertifika bağlantısı başarısız.") from None
    except requests.exceptions.Timeout:
        raise RuntimeError("Telegram API zaman aşımı (10 saniye).") from None
    except requests.exceptions.ProxyError:
        raise RuntimeError("Telegram API proxy bağlantısı başarısız.") from None
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Telegram API ağına bağlanılamadı; DNS, ağ veya güvenlik duvarını kontrol edin.") from None
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Telegram HTTP isteği başarısız ({type(error).__name__}).") from None

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"Telegram geçerli JSON döndürmedi (HTTP {response.status_code}).") from None
    if not data.get("ok"):
        description = data.get("description", "API isteği reddedildi.")
        raise RuntimeError(f"Telegram API HTTP {response.status_code}: {description}")
    return data["result"]


def send_message(token, chat_id, text):
    for chunk in split_message(text):
        telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": chunk})


def validate_bot_token(token):
    try:
        return telegram_call(token, "getMe", {})
    except RuntimeError as error:
        raise SystemExit(f"Telegram API ön kontrolü başarısız: {error}") from None


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def saglik_sunucusu_baslat():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        print(f"HTTP Saglik sunucusu {port} portunda baslatildi.", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"HTTP Sunucu Hatasi: {e}", flush=True)


def match_key(event):
    return f"{event['esd_ms']}_{event['hn']}_{event['an']}"


def radar_asamasi(event, now):
    kalan = event["esd"] - now
    for ad, ust, alt, _ in RADAR_ASAMALARI:
        if alt < kalan <= ust:
            return ad
    return None


def load_analyzed():
    try:
        return json.loads(ANALYZED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_analyzed(analyzed):
    ANALYZED_FILE.write_text(json.dumps(analyzed, ensure_ascii=False, indent=1), encoding="utf-8")


def otomatik_tara(token, allowed_user_ids):
    import taktik

    print("[TARAMA] Otomatik mac taramasi baslatildi...", flush=True)
    analyzed = load_analyzed()
    now = time.time()
    veri, _ = taktik.N.bulten(True)
    adaylar = []
    for e in veri["olaylar"]:
        asama = radar_asamasi(e, now)
        if asama and asama not in analyzed.get(match_key(e), []):
            adaylar.append(e)
    maclar = {}
    if adaylar:
        tum_taktikler = [t for t in taktik.dosya_oku()[1] if t["aktif"] and t.get("spor", "futbol") == "futbol" and t["kurallar"]]
        maclar = taktik.telegram_adaylari(adaylar, tum_taktikler)
    bildirim = 0
    for ad, _, _, baslik in RADAR_ASAMALARI:
        grup = {k: v for k, v in maclar.items() if radar_asamasi(v[0], now) == ad}
        if not grup:
            continue
        metin = taktik.telegram_metni(grup, veri["cekim"], baslik)
        for user_id in allowed_user_ids:
            send_message(token, user_id, metin)
        for e, _ in grup.values():
            analyzed.setdefault(match_key(e), []).append(ad)
        save_analyzed(analyzed)
        bildirim += len(grup)
    print(f"[TARAMA] {bildirim} mac bildirildi. ({len(adaylar)} aday mac, 60 dk penceresinde)", flush=True)


def load_tracker():
    try:
        return json.loads(OPENING_TRACKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"gonderilen": [], "son_gun": None}


def save_tracker(tracker):
    OPENING_TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=1), encoding="utf-8")


def acilis_raporu(token, hedef_ids, otomatik):
    import taktik

    tr = taktik.N.TR
    with _tracker_lock:
        tracker = load_tracker()
        veri, _ = taktik.N.bulten(True)
        now = time.time()
        gonderilen = set(tracker["gonderilen"])
        if otomatik:
            bugun = datetime.fromtimestamp(now, tr).date()
            olaylar = [e for e in veri["olaylar"] if e["esd"] > now and datetime.fromtimestamp(e["esd"], tr).date() == bugun and match_key(e) not in gonderilen]
        else:
            olaylar = [e for e in veri["olaylar"] if e["esd"] > now]
        tum_taktikler = [t for t in taktik.dosya_oku()[1] if t["aktif"] and t.get("spor", "futbol") == "futbol" and t["kurallar"]]
        maclar = taktik.telegram_adaylari(olaylar, tum_taktikler, ilk_zorla=True) if olaylar else {}
        if maclar:
            metin = taktik.telegram_metni(maclar, veri["cekim"], OPENING_TITLE, oran_etiketi="ilk görülen oran", tarihli=True)
            for user_id in hedef_ids:
                send_message(token, user_id, metin)
            if otomatik:
                tracker["gonderilen"] = sorted(gonderilen | {match_key(e) for e, _ in maclar.values()})
                save_tracker(tracker)
        elif not otomatik:
            for user_id in hedef_ids:
                send_message(token, user_id, "Açılış oranı analizine uyan yeni maç yok.")
    print(f"[ACILIS] {len(maclar)} mac raporlandi (otomatik={otomatik})", flush=True)


def acilis_dongu(token, allowed_user_ids):
    import taktik

    tr = taktik.N.TR
    while True:
        try:
            simdi = datetime.fromtimestamp(time.time(), tr)
            with _tracker_lock:
                son_gun = load_tracker().get("son_gun")
            if simdi.time() >= dtime(0, 1) and son_gun != simdi.date().isoformat():
                acilis_raporu(token, allowed_user_ids, otomatik=True)
                with _tracker_lock:
                    tracker = load_tracker()
                    tracker["son_gun"] = simdi.date().isoformat()
                    save_tracker(tracker)
        except Exception as error:
            print(f"Açılış taraması hatası: {error}", file=sys.stderr, flush=True)
        time.sleep(60)


def otomatik_dongu(token, allowed_user_ids):
    while True:
        try:
            otomatik_tara(token, allowed_user_ids)
        except Exception as error:
            print(f"Otomatik tarama hatası: {error}", file=sys.stderr, flush=True)
        time.sleep(AUTO_INTERVAL_SECONDS)


def main():
    threading.Thread(target=saglik_sunucusu_baslat, daemon=True).start()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN ortam değişkeni ayarlanmamış.")
    try:
        allowed_user_ids = parse_allowed_user_ids(allowed_ids)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    bot_info = validate_bot_token(token)
    print(f"Telegram botu doğrulandı: @{bot_info.get('username', 'kullanici-adi-yok')}", flush=True)
    offset = None
    threading.Thread(target=otomatik_dongu, args=(token, allowed_user_ids), daemon=True).start()
    threading.Thread(target=acilis_dongu, args=(token, allowed_user_ids), daemon=True).start()
    print("BWM Telegram botu çalışıyor.", flush=True)
    while True:
        try:
            updates = telegram_call(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 5, "allowed_updates": ["message"]},
            )
            for update in updates:
                offset = update["update_id"] + 1
                reply = response_for_update(update, allowed_user_ids)
                if reply == TAKTIK_ACILIS:
                    try:
                        acilis_raporu(token, [update["message"]["chat"]["id"]], otomatik=False)
                        reply = None
                    except Exception as error:
                        reply = f"Açılış analizi hatası: {error}"
                if reply == TEST_TARA:
                    try:
                        otomatik_tara(token, allowed_user_ids)
                        reply = "Manuel tarama tamamlandı, logları kontrol edin."
                    except Exception as error:
                        reply = f"Manuel tarama hatası: {error}"
                if reply:
                    chat_id = update["message"]["chat"]["id"]
                    send_message(token, chat_id, reply)
        except (RuntimeError, KeyError, TypeError) as error:
            print(f"Bot döngüsü hatası: {error}", file=sys.stderr, flush=True)
            if isinstance(error, RuntimeError) and "HTTP 409" in str(error):
                raise SystemExit("HTTP 409: başka bir bot süreci veya Telegram webhook'u getUpdates ile çakışıyor.") from None
            time.sleep(5)


if __name__ == "__main__":
    main()