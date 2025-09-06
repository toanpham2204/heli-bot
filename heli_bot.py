import os
import requests
from telegram.ext import Application, CommandHandler

# --- Biến môi trường ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")  # ví dụ: https://heli-bot.onrender.com

# --- Endpoint Helichain ---
LCD = "https://lcd.helichain.com"

# ================== Các lệnh bot ==================

async def ping(update, context):
    """Test bot phản hồi"""
    await update.message.reply_text("🏓 Pong!")

async def status(update, context):
    """Kiểm tra tình trạng bot"""
    await update.message.reply_text("✅ Bot đang hoạt động bình thường!")

async def validator(update, context):
    """Tổng số validator & % jail"""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED"
        res = requests.get(url, timeout=10).json()
        total_validators = len(res.get("validators", []))

        url2 = f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_UNBONDED"
        res2 = requests.get(url2, timeout=10).json()
        jailed = len([v for v in res2.get("validators", []) if v.get("jailed")])

        jailed_percent = (jailed / total_validators * 100) if total_validators else 0
        msg = f"🔹 Tổng Validator: {total_validators}\n🚫 Jailed: {jailed} ({jailed_percent:.2f}%)"
    except Exception as e:
        msg = f"⚠️ Lỗi lấy dữ liệu validator: {e}"

    await update.message.reply_text(msg)

async def staked(update, context):
    """Tổng HELI đã bonded"""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/pool"
        res = requests.get(url, timeout=10).json()
        bonded = int(res.get("pool", {}).get("bonded_tokens", 0)) / 1e6
        msg = f"💎 Tổng HELI đã bonded: {bonded:,.0f} HELI"
    except Exception as e:
        msg = f"⚠️ Lỗi lấy dữ liệu staked: {e}"

    await update.message.reply_text(msg)

async def unstake(update, context):
    """Tổng HELI đang unbonding"""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators?pagination.limit=2000"
        res = requests.get(url, timeout=15).json()
        validators = res.get("validators", [])

        total_unbond = 0
        for v in validators:
            val_addr = v.get("operator_address")
            u = requests.get(f"{LCD}/cosmos/staking/v1beta1/validators/{val_addr}/unbonding_delegations", timeout=15).json()
            for entry in u.get("unbonding_responses", []):
                for balance in entry.get("entries", []):
                    total_unbond += int(balance.get("balance", 0))

        total_unbond = total_unbond / 1e6
        msg = f"🔓 Tổng HELI đang unbonding: {total_unbond:,.0f} HELI"
    except Exception as e:
        msg = f"⚠️ Lỗi lấy dữ liệu unstake: {e}"

    await update.message.reply_text(msg)

# ================== Main ==================

def main():
    if not BOT_TOKEN:
        raise ValueError("⚠️ Chưa thiết lập biến môi trường BOT_TOKEN")

    application = Application.builder().token(BOT_TOKEN).build()

    # --- Đăng ký lệnh ---
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("validator", validator))
    application.add_handler(CommandHandler("staked", staked))
    application.add_handler(CommandHandler("unstake", unstake))

    # --- Kiểm tra môi trường ---
    if RENDER_URL:
        # Render → dùng webhook
        port = int(os.environ.get("PORT", 5000))
        webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"

        print(f"🚀 Chạy bot bằng Webhook tại {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        # Local → dùng polling
        print("🖥️ Chạy bot bằng polling (local mode)")
        application.run_polling()

if __name__ == "__main__":
    main()
