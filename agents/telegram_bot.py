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
from pathlib import Path
import requests


AGENT_DIR = Path(__file__).resolve().parent
MAX_MESSAGE_LENGTH = 3900
SCAN_TIMEOUT_SECONDS = 480
AUTO_INTERVAL_SECONDS = 30 * 60
AUTO_WINDOW_SECONDS = 30 * 60
ANALYZED_FILE = AGENT_DIR / "analyzed_matches.json"


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
        return "Komutlar:\n/taktik - aktif oran taktiklerini Nesine bülteninde tara\n/taktik <ad> - adı verilen taktiği tara\n/taktikler - kayıtlı taktikleri listele"
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


def match_key(event):
    return f"{event['esd_ms']}_{event['hn']}_{event['an']}"


def pencere_adaylari(olaylar, now, analyzed):
    return [e for e in olaylar if 0 < e["esd"] - now <= AUTO_WINDOW_SECONDS and match_key(e) not in analyzed]


def load_analyzed():
    try:
        return set(json.loads(ANALYZED_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_analyzed(analyzed):
    ANALYZED_FILE.write_text(json.dumps(sorted(analyzed), ensure_ascii=False, indent=1), encoding="utf-8")


def otomatik_tara(token, allowed_user_ids):
    import taktik

    analyzed = load_analyzed()
    veri, _ = taktik.N.bulten(True)
    adaylar = pencere_adaylari(veri["olaylar"], time.time(), analyzed)
    if not adaylar:
        return
    tum_taktikler = [t for t in taktik.dosya_oku()[1] if t["aktif"] and t.get("spor", "futbol") == "futbol" and t["kurallar"]]
    maclar = taktik.telegram_adaylari(adaylar, tum_taktikler)
    if maclar:
        metin = taktik.telegram_metni(maclar, veri["cekim"], "OTOMATİK: başlamasına 30 dk veya daha az kalan taktik maçları")
        for user_id in allowed_user_ids:
            send_message(token, user_id, metin)
    analyzed.update(match_key(e) for e in adaylar)
    save_analyzed(analyzed)


def otomatik_dongu(token, allowed_user_ids):
    while True:
        try:
            otomatik_tara(token, allowed_user_ids)
        except Exception as error:
            print(f"Otomatik tarama hatası: {error}", file=sys.stderr, flush=True)
        time.sleep(AUTO_INTERVAL_SECONDS)


def main():
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