# api/main.py
import os
import json
import requests
from flask import Flask, request, jsonify, abort

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # set in Vercel env
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # your Telegram numeric id (optional)
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")  # optional admin secret for extra safety

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

# ensure data dir exists
os.makedirs(DATA_DIR, exist_ok=True)
for fp in (JOBS_FILE, USERS_FILE):
    if not os.path.exists(fp):
        with open(fp, "w") as f:
            json.dump([], f)

app = Flask(__name__)

# ---------- Helpers ----------
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def telegram_send(chat_id, text, parse_mode=None, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    resp = requests.post(f"{API_URL}/sendMessage", data=payload, timeout=10)
    return resp.ok, resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else None

def is_admin(user_id, secret_in_msg=""):
    if ADMIN_ID and user_id == ADMIN_ID:
        # if ADMIN_SECRET set, require it in secret_in_msg
        if ADMIN_SECRET:
            return ADMIN_SECRET and ADMIN_SECRET == secret_in_msg
        return True
    return False

# ---------- Bot message processors ----------
def handle_start(chat_id, first_name):
    text = (
        f"👋 আসসালামু আলাইকুম {first_name}!\n\n"
        "আমি *Job Alert BD* — নতুন চাকরির বিজ্ঞপ্তি পেতে /subscribe করুন।\n\n"
        "Commands:\n"
        "/jobs - আজকের চাকরি দেখুন\n"
        "/subscribe - নতুন চাকরি পেতে সাবস্ক্রাইব করুন\n"
        "/help - সাহায্য\n\n"
        "Admin: সরাসরি টেলিগ্রামে `/addjob|title|location|deadline|link|type` পাঠিয়ে নতুন চাকরি যোগ করতে পারবেন (admin only)."
    )
    telegram_send(chat_id, text, parse_mode="Markdown")

def handle_help(chat_id):
    text = (
        "📚 *Help*\n\n"
        "/jobs - আজকের তালিকা\n"
        "/subscribe - সাবস্ক্রাইব\n"
        "/unsubscribe - সাবস্ক্রাইব বাতিল\n\n"
        "Admin commands:\n"
        "`/addjob|Title|Location|Deadline|Link|Type`\n"
        "Type could be: Govt, Private, NGO ইত্যাদি.\n\n"
        "Example (admin):\n"
        "`/addjob|Assistant Teacher|Dhaka|30-10-2025|https://example.com/job|NGO`"
    )
    telegram_send(chat_id, text, parse_mode="Markdown")

def list_jobs(chat_id):
    jobs = load_json(JOBS_FILE)
    if not jobs:
        telegram_send(chat_id, "❌ আজ কোনো চাকরি পোস্ট করা হয়নি।")
        return
    parts = []
    for j in jobs[::-1]:  # latest first
        parts.append(f"🏢 *{j.get('title')}*\n📍 {j.get('location')}\n🗓️ শেষ: {j.get('deadline')}\n🔗 {j.get('link')}\n🏷️ {j.get('type')}")
    text = "📢 *আজকের চাকরির তালিকা:*\n\n" + "\n\n".join(parts)
    telegram_send(chat_id, text, parse_mode="Markdown")

def subscribe_user(chat_id):
    users = load_json(USERS_FILE)
    if chat_id in users:
        telegram_send(chat_id, "🔔 আপনি আগেই সাবস্ক্রাইব করেছেন।")
        return
    users.append(chat_id)
    save_json(USERS_FILE, users)
    telegram_send(chat_id, "✅ আপনি সফলভাবে সাবস্ক্রাইব করেছেন! নতুন চাকরির পোস্টে নোটিফিকেশন পাবেন।")

def unsubscribe_user(chat_id):
    users = load_json(USERS_FILE)
    if chat_id in users:
        users.remove(chat_id)
        save_json(USERS_FILE, users)
        telegram_send(chat_id, "✅ সাবস্ক্রাইব সাফল্যজনকভাবে বাতিল হয়েছে।")
    else:
        telegram_send(chat_id, "আপনি সাবস্ক্রাইব করে নেননি।")

def broadcast_new_job(job):
    users = load_json(USERS_FILE)
    text = f"📢 *নতুন চাকরি পোস্ট হয়েছে!*\n\n🏢 *{job.get('title')}*\n📍 {job.get('location')}\n🗓️ শেষ: {job.get('deadline')}\n🔗 {job.get('link')}\n🏷️ {job.get('type')}"
    for uid in users:
        try:
            telegram_send(uid, text, parse_mode="Markdown")
        except Exception:
            pass

def add_job_from_text(chat_id, user_id, text):
    # expected format: /addjob|title|location|deadline|link|type|[admin_secret_if_set]
    parts = text.split("|")
    # Remove command portion if combined, allow both "/addjob|..." and "/addjob | ..."
    if parts[0].strip().startswith("/addjob"):
        # if the first element is exactly "/addjob" then drop it
        if parts[0].strip() == "/addjob":
            parts = parts[1:]
        else:
            # first piece may be like "/addjob" or "/addjob something"
            # remove leading "/addjob" token
            parts[0] = parts[0].replace("/addjob", "", 1).lstrip()
    if len(parts) < 5:
        telegram_send(chat_id, "⚠️ ফরম্যাট ভুল। সঠিক ফরম্যাট:\n`/addjob|Title|Location|Deadline|Link|Type|[ADMIN_SECRET]`", parse_mode="Markdown")
        return

    # optional admin secret at end
    provided_secret = ""
    if len(parts) >= 6:
        provided_secret = parts[5].strip() if len(parts) == 6 else (parts[6].strip() if len(parts) > 6 else "")
    # check admin rights
    if not is_admin(user_id, provided_secret):
        telegram_send(chat_id, "❌ আপনি অ্যাডমিন নন বা ADMIN_SECRET মেলেনি।")
        return

    title = parts[0].strip()
    location = parts[1].strip()
    deadline = parts[2].strip()
    link = parts[3].strip()
    type_ = parts[4].strip() if len(parts) >=5 else "General"

    job = {"title": title, "location": location, "deadline": deadline, "link": link, "type": type_}
    jobs = load_json(JOBS_FILE)
    jobs.append(job)
    save_json(JOBS_FILE, jobs)
    telegram_send(chat_id, "✅ নতুন চাকরি যোগ করা হয়েছে এবং সাবস্ক্রাইবারদের জানানো হবে।")
    broadcast_new_job(job)

# ---------- Webhook endpoint ----------
@app.route("/api/bot", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    # handle only message updates and callback queries simply
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify({"ok": True})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user = message.get("from", {})
    user_id = user.get("id")
    text = message.get("text", "").strip()
    # Commands handling
    if text.startswith("/start"):
        first_name = user.get("first_name") or ""
        handle_start(chat_id, first_name)
        return jsonify({"ok": True})
    if text.startswith("/help"):
        handle_help(chat_id)
        return jsonify({"ok": True})
    if text.startswith("/jobs"):
        list_jobs(chat_id)
        return jsonify({"ok": True})
    if text.startswith("/subscribe"):
        subscribe_user(chat_id)
        return jsonify({"ok": True})
    if text.startswith("/unsubscribe"):
        unsubscribe_user(chat_id)
        return jsonify({"ok": True})
    if text.startswith("/addjob"):
        add_job_from_text(chat_id, user_id, text)
        return jsonify({"ok": True})

    # fallback: simple echo or instruction
    telegram_send(chat_id, "আবার চেষ্টা করুন — সাহায্যের জন্য /help লিখুন।")
    return jsonify({"ok": True})

# simple root
@app.route("/", methods=["GET"])
def index():
    return "Job Alert Bot (Webhook) is running."

# health
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

# run locally
if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
