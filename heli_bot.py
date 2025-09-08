import os
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
# Quản lý User
# -------------------------------
ADMIN_ID = 2028673755
ALLOWED_USERS = {ADMIN_ID}

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"🆔 User ID của bạn: {user.id}\n👤 Username: @{user.username}")

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Bạn không có quyền thêm user.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ Dùng: /grant <user_id>")
        return
    try:
        new_id = int(context.args[0])
        ALLOWED_USERS.add(new_id)
        await update.message.reply_text(f"✅ Đã cấp quyền cho user {new_id}")
    except ValueError:
        await update.message.reply_text("⚠️ User ID không hợp lệ.")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Bạn không có quyền xoá user.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ Dùng: /revoke <user_id>")
        return
    try:
        rem_id = int(context.args[0])
        if rem_id in ALLOWED_USERS:
            ALLOWED_USERS.remove(rem_id)
            await update.message.reply_text(f"✅ Đã xoá quyền user {rem_id}")
        else:
            await update.message.reply_text("⚠️ User này chưa được cấp quyền.")
    except ValueError:
        await update.message.reply_text("⚠️ User ID không hợp lệ.")

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

def _get_validators_list():
    """Trả về danh sách valoper của tất cả validator bonded."""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED&pagination.limit=2000"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        vals = r.json().get("validators", [])
        return [v.get("operator_address") for v in vals if v.get("operator_address")]
    except Exception as e:
        logging.error(f"Lỗi lấy validators: {e}")
        return []

def _sum_unbonding_for_validator(valoper: str) -> int:
    """Trả về tổng unbonding (uheli) từ tất cả delegator trong 1 validator."""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators/{valoper}/unbonding_delegations?pagination.limit=2000"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return 0
        data = r.json().get("unbonding_responses", [])
        total = 0
        for item in data:
            for entry in item.get("entries", []):
                bal = entry.get("balance", "0")
                try:
                    total += int(bal)
                except:
                    try:
                        total += int(float(bal))
                    except:
                        pass
        return total
    except Exception as e:
        logging.error(f"Lỗi lấy unbonding cho {valoper}: {e}")
        return 0

# -------------------------------
# Commands
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Xin chào! Bot Heli đã sẵn sàng.\nGõ /help để xem danh sách lệnh.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 Danh sách lệnh khả dụng:

/help - Xem hướng dẫn
/whoami - Hiển thị User ID của bạn
/grant <id> - Cấp quyền cho user (admin)
/revoke <id> - Thu hồi quyền user (admin)
/staked - Xem tổng HELI đã staking
/unstake - Xem tổng HELI đang unstake
/unbonding_wallets - Xem số ví đang unstake
/validator - Danh sách validator & trạng thái jail
/status - Trạng thái mạng HeliChain
/price - Giá HELI hiện tại
/sendprice - Gửi giá HELI ngay lập tức
/supply - Tổng cung HELI
/apy - Tính APY staking (đã trừ commission)
"""
    await update.message.reply_text(msg)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot đang hoạt động!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
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
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    """Tính tổng HELI unbonding từ tất cả delegator trên toàn bộ validators."""
    sent = await update.message.reply_text("⏳ Đang tính tổng unbonding từ tất cả validators...")
    loop = asyncio.get_running_loop()

    def compute_total():
        vals = _get_validators_list()
        if not vals:
            return None, "Không lấy được danh sách validator"
        total = 0
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(_sum_unbonding_for_validator, v): v for v in vals}
            for fut in as_completed(futures):
                try:
                    total += fut.result()
                except Exception as e:
                    logging.error(f"Lỗi khi cộng unbonding: {e}")
        return total, None

    total_uheli, err = await loop.run_in_executor(None, compute_total)
    if err:
        await sent.edit_text(f"⚠️ {err}")
        return

    heli_amount = (total_uheli or 0) / 1e6
    await sent.edit_text(f"🔓 Tổng HELI đang unbonding trên toàn mạng: {heli_amount:,.6f} HELI")

async def unbonding_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    """Đếm tổng số ví đang unbonding trên toàn bộ validators."""
    try:
        vals_url = f"{LCD}/cosmos/staking/v1beta1/validators?pagination.limit=2000"
        vals = requests.get(vals_url, timeout=15).json().get("validators", [])
        wallets = set()

        for v in vals:
            valoper = v.get("operator_address")
            url = f"{LCD}/cosmos/staking/v1beta1/validators/{valoper}/unbonding_delegations?pagination.limit=2000"
            r = requests.get(url, timeout=15).json()
            for resp in r.get("unbonding_responses", []):
                wallets.add(resp.get("delegator_address"))

        count = len(wallets)
        await update.message.reply_text(f"🔓 Tổng số ví đang unbonding: {count}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy danh sách unbonding: {e}")


async def bonded_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
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
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
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
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
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
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    try:
        # Ưu tiên lấy giá từ MEXC
        url = "https://api.mexc.com/api/v3/ticker/price?symbol=HELIUSDT"
        r = requests.get(url, timeout=10).json()
        price_usd = float(r.get("price", 0))

        if price_usd > 0:
            await update.message.reply_text(f"💲 Giá HELI hiện tại (MEXC): ${price_usd:,.4f}")
            return

        # Fallback CoinGecko
        url_cg = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "heli", "vs_currencies": "usd"}
        r = requests.get(url_cg, params=params, timeout=10).json()
        price_usd = r.get("heli", {}).get("usd")

        if price_usd:
            await update.message.reply_text(f"💲 Giá HELI hiện tại (CoinGecko): ${price_usd:,.4f}")
        else:
            await update.message.reply_text("⚠️ Không lấy được giá HELI từ API.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy giá: {e}")

async def staked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    pool = get_pool()
    bonded = int(pool.get("bonded_tokens", 0)) / 1e6
    await update.message.reply_text(f"💎 Tổng HELI đang staking: {bonded:,.2f} HELI")

async def validator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    """Thống kê tổng số validator và số node bị jail."""
    try:
        url = f"{LCD}/cosmos/staking/v1beta1/validators?pagination.limit=2000"
        r = requests.get(url, timeout=15).json()
        vals = r.get("validators", [])

        total = len(vals)
        jailed = sum(1 for v in vals if v.get("jailed", False))
        bonded = sum(1 for v in vals if v.get("status") == "BOND_STATUS_BONDED" and not v.get("jailed", False))

        msg = (
            f"🖥️ Tổng số validator: {total}\n"
            f"✅ Đang hoạt động (bonded): {bonded}\n"
            f"🚨 Bị jail: {jailed}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy thông tin validator: {e}")

async def sendprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    url = "https://api.mexc.com/api/v3/ticker/price?symbol=HELIUSDT"
    r = requests.get(url, timeout=10).json()
    price_usd = float(r.get("price", 0))
    await update.message.reply_text(f"📢 Giá HELI hiện tại: ${price_usd:,.4f}")


# -------------------------------
# Main
# -------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Lệnh quản lý user
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))

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
    app.add_handler(CommandHandler("staked", staked))
    app.add_handler(CommandHandler("validator", validator))
    app.add_handler(CommandHandler("sendprice", sendprice))


    # Scheduler: gửi giá HELI hằng ngày
    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")

    async def send_daily_price():
        try:
            r = requests.get("https://api.mexc.com/api/v3/ticker/price?symbol=HELIUSDT", timeout=10).json()
            price_usd = float(r.get("price", 0))
            for uid in ALLOWED_USERS:
                await app.bot.send_message(chat_id=uid, text=f"📢 Giá HELI hôm nay: ${price_usd:,.4f}")
        except Exception as e:
            logging.error(f"Lỗi gửi giá: {e}")

    scheduler.add_job(send_daily_price, "cron", hour=9, minute=0)
    scheduler.start()

    logging.info("🚀 Bot HeliChain đã khởi động...")

    # ✅ Chạy webhook nếu trên Render, còn không thì dùng polling
    if os.getenv("RENDER") == "true":
        port = int(os.environ.get("PORT", "10000"))
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
