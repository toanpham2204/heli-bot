import os
import asyncio
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------- Logging ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- Config ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
LCD_ENDPOINT = "https://lcd.helichain.com"

# Danh sách validator cần theo dõi unbonding
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

# ---------------- Helper ----------------
def get_json(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Lỗi khi gọi API {url}: {e}")
        return None

# ---------------- Commands ----------------
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot đang hoạt động!")

async def validator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED")
    jailed = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_UNBONDED")

    total = 0
    jailed_count = 0

    if data and "validators" in data:
        total += len(data["validators"])
    if jailed and "validators" in jailed:
        jailed_count += len(jailed["validators"])
        total += len(jailed["validators"])

    await update.message.reply_text(
        f"📊 Tổng validator: {total}\n"
        f"🚨 Validator jailed: {jailed_count} ({(jailed_count/total*100 if total else 0):.2f}%)"
    )

async def staked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/pool")
    if data and "pool" in data:
        bonded = int(data["pool"]["bonded_tokens"]) / 1e6
        not_bonded = int(data["pool"]["not_bonded_tokens"]) / 1e6
        total = bonded + not_bonded
        ratio = (bonded / total * 100) if total > 0 else 0
        await update.message.reply_text(
            f"💎 Đã stake: {bonded:,.0f} HELI\n"
            f"📌 Chưa stake: {not_bonded:,.0f} HELI\n"
            f"📈 Tỷ lệ stake: {ratio:.2f}%"
        )
    else:
        await update.message.reply_text("⚠️ Không lấy được dữ liệu staking.")

async def unstake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_unbond = 0
    for val in VALIDATORS:
        data = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators/{val}/unbonding_delegations")
        if data and "unbonding_responses" in data:
            for u in data["unbonding_responses"]:
                for entry in u["entries"]:
                    total_unbond += int(entry["balance"])

    heli_unbond = total_unbond / 1e6
    await update.message.reply_text(f"🔓 Tổng HELI đang unstake (unbonding): {heli_unbond:,.0f} HELI")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Gộp thông tin /validator + /staked
    pool = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/pool")
    bonded = not_bonded = ratio = 0
    if pool and "pool" in pool:
        bonded = int(pool["pool"]["bonded_tokens"]) / 1e6
        not_bonded = int(pool["pool"]["not_bonded_tokens"]) / 1e6
        total = bonded + not_bonded
        ratio = (bonded / total * 100) if total > 0 else 0

    val_data = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED")
    jailed_data = get_json(f"{LCD_ENDPOINT}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_UNBONDED")

    total_val = len(val_data["validators"]) if val_data and "validators" in val_data else 0
    jailed_count = len(jailed_data["validators"]) if jailed_data and "validators" in jailed_data else 0

    await update.message.reply_text(
        f"📊 Validator: {total_val} | 🚨 Jailed: {jailed_count}\n"
        f"💎 Đã stake: {bonded:,.0f} HELI ({ratio:.2f}%)"
    )

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        raise ValueError("⚠️ Chưa thiết lập biến môi trường BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("validator", validator))
    app.add_handler(CommandHandler("staked", staked))
    app.add_handler(CommandHandler("unstake", unstake))
    app.add_handler(CommandHandler("status", status))

    logger.info("🚀 Bot HELI đã khởi chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
