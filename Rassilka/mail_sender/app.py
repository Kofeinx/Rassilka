import email.policy
import json
import logging
import random
import smtplib
import socket
import ssl
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mail_sender")

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------- job state ----------------
job = {
    "running": False,
    "stop_requested": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "failed": 0,
    "results": [],
    "started": None,
    "finished": None,
    "current_email": None,
    "next_at": None,
}
job_lock = threading.Lock()

DEFAULT_CONFIG = {
    "accounts": [],
    "message": {
        "to": "",
        "subject": "Тест от {email}",
        "body": "Привет!\n\nЭто тестовое письмо от {email}.",
        "delay_min_sec": 15,
        "delay_max_sec": 45,
        "batch_size": 10,
        "batch_pause_sec": 300,
    },
}


def load_config():
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"config load: {e}")
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def build_message(from_email, to_email, subject, body):
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8", cte="base64")
    msg.as_string().encode("ascii")
    return msg


def send_one(account, to_email, subject, body):
    email_addr = account["email"]
    password = account["password"]
    server_addr = account["smtp_server"]
    port = account["smtp_port"]
    use_ssl = account["use_ssl"]

    subj = subject.replace("{email}", email_addr)
    bd = body.replace("{email}", email_addr)

    try:
        msg = build_message(email_addr, to_email, subj, bd)
    except Exception as e:
        return False, f"build: {e}"

    server = None
    try:
        context = get_ssl_context()
        if use_ssl:
            server = smtplib.SMTP_SSL(
                server_addr, port, context=context, timeout=30,
                local_hostname="localhost",
            )
        else:
            server = smtplib.SMTP(
                server_addr, port, timeout=30, local_hostname="localhost"
            )
            server.starttls(context=context)
        server.login(email_addr, password)
        server.send_message(msg)
        return True, "OK"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"auth: {e}"
    except smtplib.SMTPException as e:
        return False, f"smtp: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


def run_send(accounts, to_email, subject, body,
             delay_min, delay_max, batch_size, batch_pause):
    with job_lock:
        job["running"] = True
        job["stop_requested"] = False
        job["total"] = len(accounts)
        job["current"] = 0
        job["success"] = 0
        job["failed"] = 0
        job["results"] = []
        job["started"] = datetime.now().isoformat()
        job["finished"] = None

    try:
        for i, acc in enumerate(accounts, 1):
            if job["stop_requested"]:
                logger.info("Stop requested")
                break

            with job_lock:
                job["current"] = i
                job["current_email"] = acc["email"]

            # pause between messages
            if i > 1:
                delay = random.uniform(delay_min, delay_max)
                with job_lock:
                    job["next_at"] = time.time() + delay
                end = time.time() + delay
                while time.time() < end:
                    if job["stop_requested"]:
                        break
                    time.sleep(0.5)

            if job["stop_requested"]:
                break

            # batch pause
            if batch_size > 0 and i > 1 and (i - 1) % batch_size == 0:
                with job_lock:
                    job["next_at"] = time.time() + batch_pause
                end = time.time() + batch_pause
                while time.time() < end:
                    if job["stop_requested"]:
                        break
                    time.sleep(0.5)

            if job["stop_requested"]:
                break

            with job_lock:
                job["next_at"] = None

            logger.info(f"[{i}/{len(accounts)}] {acc['email']} → {to_email}")
            ok, reason = send_one(acc, to_email, subject, body)

            with job_lock:
                job["results"].append(
                    {"email": acc["email"], "ok": ok, "reason": reason}
                )
                if ok:
                    job["success"] += 1
                else:
                    job["failed"] += 1
    finally:
        with job_lock:
            job["running"] = False
            job["finished"] = datetime.now().isoformat()
            job["current_email"] = None
            job["next_at"] = None


# ---------------- routes ----------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.route("/sw.js")
def sw():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json() or {}
    cfg = load_config()
    if "message" in data:
        cfg["message"] = {**cfg.get("message", {}), **data["message"]}
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = request.get_json() or {}
    email_addr = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not email_addr or "@" not in email_addr:
        return jsonify({"ok": False, "error": "неверный email"}), 400
    if not password:
        return jsonify({"ok": False, "error": "пароль обязателен"}), 400
    try:
        email_addr.encode("ascii")
        password.encode("ascii")
    except UnicodeEncodeError:
        return jsonify({"ok": False, "error": "только латиница"}), 400

    domain = email_addr.split("@", 1)[1].lower()
    preset = {
        "gmail.com":  ("smtp.gmail.com",    587, False),
        "mail.ru":    ("smtp.mail.ru",      465, True),
        "yandex.ru":  ("smtp.yandex.ru",    465, True),
        "yandex.com": ("smtp.yandex.com",   465, True),
        "outlook.com": ("smtp.office365.com", 587, False),
        "hotmail.com": ("smtp.office365.com", 587, False),
    }.get(domain)
    if not preset:
        return jsonify({"ok": False, "error": f"неизвестный домен: {domain}"}), 400

    cfg = load_config()
    cfg["accounts"].append({
        "email": email_addr,
        "password": password,
        "smtp_server": preset[0],
        "smtp_port": preset[1],
        "use_ssl": preset[2],
    })
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/accounts/<int:idx>", methods=["DELETE"])
def delete_account(idx):
    cfg = load_config()
    if idx < 0 or idx >= len(cfg["accounts"]):
        return jsonify({"ok": False, "error": "invalid index"}), 400
    cfg["accounts"].pop(idx)
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/send", methods=["POST"])
def start_send():
    if job["running"]:
        return jsonify({"ok": False, "error": "уже запущено"}), 400

    cfg = load_config()
    msg_cfg = cfg.get("message", {})
    accounts = [
        a for a in cfg.get("accounts", [])
        if not a.get("password", "").startswith(("REPLACE_", "PASTE_"))
    ]
    if not accounts:
        return jsonify({"ok": False, "error": "нет реальных аккаунтов"}), 400

    raw_to = (msg_cfg.get("to") or "").strip()
    if not raw_to:
        return jsonify({"ok": False, "error": "не указан получатель"}), 400

    _, to_email = parseaddr(raw_to)
    if not to_email or "@" not in to_email:
        return jsonify({"ok": False, "error": "неверный получатель"}), 400

    threading.Thread(
        target=run_send,
        args=(
            accounts,
            to_email,
            msg_cfg.get("subject", "Без темы"),
            msg_cfg.get("body", ""),
            float(msg_cfg.get("delay_min_sec", 15)),
            float(msg_cfg.get("delay_max_sec", 45)),
            int(msg_cfg.get("batch_size", 10)),
            float(msg_cfg.get("batch_pause_sec", 300)),
        ),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "total": len(accounts)})


@app.route("/api/status")
def get_status():
    with job_lock:
        return jsonify(job)


@app.route("/api/send/stop", methods=["POST"])
def stop_send():
    with job_lock:
        if not job["running"]:
            return jsonify({"ok": False, "error": "не запущено"}), 400
        job["stop_requested"] = True
    return jsonify({"ok": True})


if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "<IP-компьютера>"

    print("=" * 60)
    print("  📧 Mail Sender Mobile")
    print("=" * 60)
    print(f"  На компьютере:  http://127.0.0.1:5000")
    print(f"  На телефоне:    http://{local_ip}:5000")
    print("=" * 60)
    print("  Телефон должен быть в той же Wi-Fi сети.")
    print("=" * 60)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)