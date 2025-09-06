import os
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

# Lấy token từ biến môi trường (set BOT_TOKEN trước khi chạy)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_URL = "https://lcd.helichain.com"

# Danh sách validator để tính unbond cụ thể
VALIDATORS = [
    "helivaloper189q4atq095x22lpcrg0s0yxryeekm26pjgrem5",
    "helivaloper18ce7rgzq0tw24jdm6qvqvjsg0uy7tj5p37r3tk",
    "helivaloper1gqazv3nh9nz8y5xsv6kfl7dq6lwlnetzj6kchu",
    "helivaloper1s8krq9x24lfcsjel7du37rfq3wtymp9udncv9m",
    "helivaloper13qahyd99m6e0ag4vt30tqfkyvleugj56yzvt3n",
    "helivaloper13na36j5qek0l98jhs72v8yzf0lszngtmfuupz7",
    "helivaloper1hjkvj9lys2a58672wghae58ywkrrckf9879lxz",
    "helivaloper1ulxs5qafeuszuzfetfrappxalms335ctyfe90d",
    "helivaloper172vwf05zweuj6g2lpcq0etywgk0ccs5gtru5tp",
    "helivaloper17vvnar3rn66f8hlrkznxp4xt23xapu0l893jvn"
]

# --- Các hàm xử lý lệnh ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Xin chào 👋! Bot HELI đã sẵn sàng.")

def ping(update: Update, context: CallbackContext):
    update.message.reply_text("Pong! ✅ Bot đang online.")

def staked(update: Update, context: CallbackContext):
    try:
        r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/pool")
        data = r.json()
        bonded = int(data["pool"]["bonded_tokens"]) / 1e6
        update.message.reply_text(f"🔒 Tổng HELI đang staked: {bonded:,.0f} HELI")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy dữ liệu stake: {e}")

def unstake(update: Update, context: CallbackContext):
    try:
        total_unbond = 0
        for val in VALIDATORS:
            r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/validators/{val}/unbonding_delegations")
            data = r.json()
            entries = data.get("unbonding_responses", [])
            for entry in entries:
                for balance in entry.get("entries", []):
                    total_unbond += int(balance.get("balance", "0"))
        total_unbond = total_unbond / 1e6
        update.message.reply_text(f"🔓 Tổng HELI đang unstake (unbonding): {total_unbond:,.0f} HELI")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy dữ liệu unstake: {e}")

def validator(update: Update, context: CallbackContext):
    try:
        r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED")
        data = r.json()
        total = len(data.get("validators", []))
        jailed = sum(1 for v in data.get("validators", []) if v.get("jailed"))
        update.message.reply_text(f"👨‍💻 Tổng số validator: {total}\n🚨 Jailed: {jailed} ({jailed/total*100:.2f}%)")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy dữ liệu validator: {e}")

def ratio(update: Update, context: CallbackContext):
    try:
        r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/pool")
        data = r.json()
        bonded = int(data["pool"]["bonded_tokens"]) / 1e6
        not_bonded = int(data["pool"]["not_bonded_tokens"]) / 1e6
        ratio_val = bonded / (bonded + not_bonded) * 100 if bonded + not_bonded > 0 else 0
        update.message.reply_text(f"📈 Tỷ lệ stake hiện tại: {ratio_val:.2f}%")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi tính tỷ lệ stake: {e}")

def status(update: Update, context: CallbackContext):
    try:
        # Pool info
        r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/pool")
        pool = r.json()["pool"]
        bonded = int(pool["bonded_tokens"]) / 1e6
        not_bonded = int(pool["not_bonded_tokens"]) / 1e6
        ratio_val = bonded / (bonded + not_bonded) * 100 if bonded + not_bonded > 0 else 0

        # Validator info
        r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED")
        validators = r.json().get("validators", [])
        total_val = len(validators)
        jailed = sum(1 for v in validators if v.get("jailed"))

        # Unstake từ unbonding_delegations
        total_unbond = 0
        for val in VALIDATORS:
            r = requests.get(f"{API_URL}/cosmos/staking/v1beta1/validators/{val}/unbonding_delegations")
            data = r.json()
            entries = data.get("unbonding_responses", [])
            for entry in entries:
                for balance in entry.get("entries", []):
                    total_unbond += int(balance.get("balance", "0"))
        total_unbond = total_unbond / 1e6

        msg = (
            f"📊 HELI Network Status\n\n"
            f"🔒 Đang stake: {bonded:,.0f} HELI\n"
            f"🔓 Đang unstake: {total_unbond:,.0f} HELI\n"
            f"💤 Not bonded: {not_bonded:,.0f} HELI\n"
            f"📈 Tỷ lệ stake: {ratio_val:.2f}%\n\n"
            f"👨‍💻 Tổng validator: {total_val}\n"
            f"🚨 Validator jailed: {jailed} ({jailed/total_val*100:.2f}%)"
        )

        update.message.reply_text(msg)
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy dữ liệu status: {e}")

# --- Khởi chạy bot ---
def main():
    if not BOT_TOKEN:
        print("❌ Lỗi: chưa cấu hình biến môi trường BOT_TOKEN")
        return

    req = Request(connect_timeout=10, read_timeout=20)
    updater = Updater(BOT_TOKEN, use_context=True, request_kwargs={'read_timeout': 20, 'connect_timeout': 10})

    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("staked", staked))
    dp.add_handler(CommandHandler("unstake", unstake))
    dp.add_handler(CommandHandler("validator", validator))
    dp.add_handler(CommandHandler("ratio", ratio))
    dp.add_handler(CommandHandler("status", status))

    print("🤖 Bot HELI đã khởi động...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
