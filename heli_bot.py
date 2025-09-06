import os
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ====== ENVIRONMENT VARIABLE ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("⚠️ Chưa thiết lập biến môi trường BOT_TOKEN")

LCD_ENDPOINT = "https://lcd.helichain.com"

# ====== TELEGRAM COMMANDS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 HELI Bot đã khởi động trên Render Free!")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong")

async def validator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators")
        data = resp.json().get("validators", [])
        total = len(data)
        jailed = sum(1 for v in data if v.get("jailed"))
        msg = f"📊 Tổng Validator: {total}\n🚫 Bị jail: {jailed} ({jailed/total:.2%})"
    except Exception as e:
        msg = f"❌ Lỗi: {e}"
    await update.message.reply_text(msg)

async def staked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/pool")
        pool = resp.json().get("pool", {})
        bonded = int(pool.get("bonded_tokens", 0)) / 1e6
        msg = f"🔒 Tổng HELI đã bonded: {bonded:,.2f} HELI"
    except Exception as e:
        msg = f"❌ Lỗi: {e}"
    await update.message.reply_text(msg)

async def unstake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Danh sách validator cần tính
    validators = [
        "helivaloper189q4atq095x22lpcrg0s0yxryeekm26pjgrem5",
        "helivaloper18ce7rgzq0tw24jdm6qvqvjsg0uy7tj5p37r3tk",
        "helivaloper1gqazv3nh9nz8y5xsv6kfl7dq6lwlnetzj6kchu",
        "helivaloper1s8krq9x24lfcsjel7du37rfq3wtymp9udncv9m",
        "helivaloper13qahyd99m6e0ag4vt30tqfkyvleugj56yzvt3n",
        "helivaloper13na36j5qek0l98jhs72v8yzf0lszngtmfuupz7",
        "helivaloper1hjkvj9lys2a58672wghae58ywkrrckf9879lxz",
        "helivaloper1ulxs5qafeuszuzfetfrappxalms335ctyfe90d",
        "helivaloper172vwf05zweuj6g2lpcq0etywgk0ccs5gtru5tp",
        "helivaloper17vvnar3rn66f8hlrkznxp4xt23xapu0l893jvn",
    ]
    total_unbond = 0
    try:
        for val in validators:
            url = f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators/{val}/unbonding_delegations"
            resp = requests.get(url)
            if resp.status_code == 200:
                data = resp.json().get("unbonding_responses", [])
                for d in data:
                    entries = d.get("entries", [])
                    for e in entries:
                        balance = int(e.get("balance", 0)) / 1e6
                        total_unbond += balance
        msg = f"🔓 Tổng HELI đang unstake (unbonding): {total_unbond:,.2f} HELI"
    except Exception as e:
        msg = f"❌ Lỗi: {e}"
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp_val = requests.get(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators")
        data = resp_val.json().get("validators", [])
        total = len(data)
        jailed = sum(1 for v in data if v.get("jailed"))

        resp_pool = requests.get(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/pool")
        pool = resp_pool.json().get("pool", {})
        bonded = int(pool.get("bonded_tokens", 0)) / 1e6

        msg = (
            f"📊 Hệ thống HELI\n"
            f"- Tổng Validator: {total}\n"
            f"- Jail: {jailed} ({jailed/total:.2%})\n"
            f"- 🔒 Bonded: {bonded:,.2f} HELI"
        )
    except Exception as e:
        msg = f"❌ Lỗi: {e}"
    await update.message.reply_text(msg)

# ====== BOT RUNNER ======
def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("validator", validator))
    app.add_handler(CommandHandler("staked", staked))
    app.add_handler(CommandHandler("unstake", unstake))
    app.add_handler(CommandHandler("status", status))
    app.run_polling()

# ====== FLASK SERVER ======
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "🚀 HELI Bot đang chạy trên Render Free!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ====== MAIN ======
if __name__ == "__main__":
    t1 = threading.Thread(target=run_bot)
    t1.start()
    run_flask()
