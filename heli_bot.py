import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# -------------------------------
# Cấu hình
# -------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
LCD = "https://lcd.helichain.com"
PORT = int(os.getenv("PORT", 8080))  # Render cấp PORT
WEBHOOK_URL = os.getenv("RENDER_URL")  # https://<appname>.onrender.com

if not BOT_TOKEN:
    raise ValueError("⚠️ Chưa thiết lập biến môi trường BOT_TOKEN")

# -------------------------------
# Helper Functions
# -------------------------------
def get_pool():
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/pool"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json().get("pool", {})
    except Exception as e:
        logging.error(f"Lỗi lấy pool: {e}")
        return {}

def get_inflation():
    try:
        url = f"{LCD}/cosmos/mint/v1beta1/inflation"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return float(r.json().get("inflation", 0))
    except Exception as e:
        logging.error(f"Lỗi lấy inflation: {e}")
        return 0.0

def get_top_validator():
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        validators = r.json().get("validators", [])
        if not validators:
            return None
        validators.sort(key=lambda v: int(v.get("tokens", 0)), reverse=True)
        return validators[0]  # Top 1
    except Exception as e:
        logging.error(f"Lỗi lấy danh sách validator: {e}")
        return None

def get_unbonding_data():
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/unbonding_delegations"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error(f"Lỗi lấy dữ liệu unbonding: {e}")
        return {}

# -------------------------------
# Commands
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Xin chào! Bot Heli đã sẵn sàng.\nGõ /help để xem danh sách lệnh.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 Danh sách lệnh:\n\n"
        "/ping - Kiểm tra bot\n"
        "/status - Trạng thái mạng\n"
        "/unstake - Tổng HELI đang unbonding\n"
        "/unbonding_wallets - Liệt kê 10 ví unbonding\n"
        "/bonded_ratio - Tỷ lệ HELI bonded\n"
        "/apy - APY staking (theo validator top 1)\n"
        "/supply - Tổng cung HELI\n"
        "/price - Giá HELI hiện tại\n"
    )
    await update.message.reply_text(msg)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot đang hoạt động!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"{LCD}/cosmos/base/tendermint/v1beta1/blocks/latest"
        r = requests.get(url, timeout=10).json()
        height = r.get("block", {}).get("header", {}).get("height", "N/A")
        proposer = r.get("block", {}).get("header", {}).get("proposer_address", "N/A")
        await update.message.reply_text(
            f"📊 Trạng thái mạng HeliChain:\n⛓ Block height: {height}\n👤 Proposer: {proposer}"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy trạng thái mạng: {e}")

async def unstake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_unbonding_data()
    total_unbonding = 0
    try:
        unbondings = data.get("unbonding_responses", [])
        for ub in unbondings:
            for entry in ub.get("entries", []):
                total_unbonding += int(entry.get("balance", 0)) / 1e6
    except Exception as e:
        logging.error(f"Lỗi tính toán unstake: {e}")
    await update.message.reply_text(f"🔓 Tổng HELI đang unstake: {total_unbonding:,.2f} HELI")

async def unbonding_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_unbonding_data()
    unbondings = data.get("unbonding_responses", [])
    wallet_count = len(unbondings)
    if wallet_count == 0:
        await update.message.reply_text("✅ Hiện không có ví nào unbonding.")
        return
    reply = f"📋 Có {wallet_count} ví đang unbonding (hiển thị 10 ví đầu):\n\n"
    for ub in unbondings[:10]:
        delegator = ub.get("delegator_address", "N/A")
        total = sum(int(e.get("balance", 0)) for e in ub.get("entries", [])) / 1e6
        reply += f"🔹 {delegator}: {total:,.2f} HELI\n"
    if wallet_count > 10:
        reply += f"\n... và {wallet_count-10} ví khác."
    await update.message.reply_text(reply)

async def bonded_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = get_pool()
    bonded = int(pool.get("bonded_tokens", 0)) / 1e6
    not_bonded = int(pool.get("not_bonded_tokens", 0)) / 1e6
    total = bonded + not_bonded
    if total == 0:
        await update.message.reply_text("⚠️ Không có dữ liệu bonded ratio.")
        return
    ratio = bonded / total * 100
    await update.message.reply_text(
        f"📊 Bonded Ratio:\n🔒 {bonded:,.0f} HELI bonded\n🔓 {not_bonded:,.0f} HELI not bonded\n➡️ Tỷ lệ bonded: {ratio:.2f}%"
    )

async def apy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = get_pool()
    bonded = int(pool.get("bonded_tokens", 0))
    not_bonded = int(pool.get("not_bonded_tokens", 0))
    total = bonded + not_bonded
    if bonded == 0 or total == 0:
        await update.message.reply_text("⚠️ Không thể tính APY.")
        return
    bonded_ratio = bonded / total
    inflation = get_inflation()
    top_val = get_top_validator()
    if not top_val:
        await update.message.reply_text("⚠️ Không lấy được validator top 1.")
        return
    commission_rate = float(top_val.get("commission", {}).get("commission_rates", {}).get("rate", 0))
    val_name = top_val.get("description", {}).get("moniker", "Unknown")
    val_tokens = int(top_val.get("tokens", 0)) / 1e6
    apy_value = inflation / bonded_ratio * (1 - commission_rate) * 100
    await update.message.reply_text(
        f"💰 APY staking (theo validator top 1: {val_name})\n➡️ {apy_value:.2f}%/năm\n\n"
        f"(Inflation: {inflation*100:.2f}%, Bonded ratio: {bonded_ratio*100:.2f}%, "
        f"Commission: {commission_rate*100:.2f}%, Stake top 1: {val_tokens:,.0f} HELI)"
    )

async def supply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"{LCD}/cosmos/bank/v1beta1/supply"
        r = requests.get(url, timeout=10).json()
        heli_supply = 0
        for item in r.get("supply", []):
            if item["denom"] == "uheli":
                heli_supply = int(item["amount"]) / 1e6
                break
        await update.message.reply_text(f"💰 Tổng cung HELI: {heli_supply:,.0f} HELI")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy supply: {e}")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "heli", "vs_currencies": "usd"}
        r = requests.get(url, params=params, timeout=10).json()
        price_usd = r.get("heli", {}).get("usd")
        if price_usd:
            await update.message.reply_text(f"💲 Giá HELI hiện tại: ${price_usd:,.4f}")
        else:
            await update.message.reply_text("⚠️ Không lấy được giá HELI.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy giá: {e}")

# -------------------------------
# Main
# -------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Đăng ký command
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("unstake", unstake))
    app.add_handler(CommandHandler("unbonding_wallets", unbonding_wallets))
    app.add_handler(CommandHandler("bonded_ratio", bonded_ratio))
    app.add_handler(CommandHandler("apy", apy))
    app.add_handler(CommandHandler("supply", supply))
    app.add_handler(CommandHandler("price", price))

    logging.info("🚀 Bot HeliChain đã khởi động...")

    # Render: Webhook
    if WEBHOOK_URL:
        logging.info(f"🔗 Sử dụng webhook: {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
        )
    else:
        # Local: Polling
        logging.info("🔄 Chạy bằng polling (local mode)")
        app.run_polling()

if __name__ == "__main__":
    main()
