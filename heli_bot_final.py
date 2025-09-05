import os
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ===== Config =====
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Đặt token trong biến môi trường
LCD = "https://lcd.helichain.com"

# ===== Helper function =====
def get_json(url):
    try:
        r = requests.get(url)
        if r.status_code == 200:
            return r.json()
        else:
            return None
    except:
        return None

# ===== Command Handlers =====
def start(update: Update, context: CallbackContext):
    update.message.reply_text("🤖 Xin chào! Tôi là HELI Bot. Gõ /help để xem danh sách lệnh.")

def help_cmd(update: Update, context: CallbackContext):
    commands = [
        "/unbonding - Tổng số HELI đang unbonding",
        "/validator - Số lượng validator & % jailed",
        "/staked - Tổng số HELI đã bonded",
        "/supply - Tổng cung, circulating, bonded ratio",
        "/apr - Ước tính APR staking",
        "/validator_info <valoper> - Thông tin một validator",
        "/delegations <wallet> - Delegations của ví",
        "/rewards <wallet> - Phần thưởng staking của ví",
        "/network - Thông tin mạng"
    ]
    update.message.reply_text("\n".join(commands))

def unbonding(update: Update, context: CallbackContext):
    data = get_json(f"{LCD}/cosmos/staking/v1beta1/pool")
    if data:
        bonded = int(data["pool"]["bonded_tokens"]) / 1e6
        not_bonded = int(data["pool"]["not_bonded_tokens"]) / 1e6
        update.message.reply_text(f"🔥 Unbonding HELI: {not_bonded:,.0f}")
    else:
        update.message.reply_text("❌ Lỗi khi lấy dữ liệu unbonding.")

def validator(update: Update, context: CallbackContext):
    data = get_json(f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED")
    jailed_data = get_json(f"{LCD}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_UNBONDED")
    if data and jailed_data:
        total = len(data["validators"]) + len(jailed_data["validators"])
        jailed = len([v for v in jailed_data["validators"] if v["jailed"]])
        update.message.reply_text(f"✅ Tổng validator: {total}\n🚨 Jailed: {jailed} ({jailed/total*100:.2f}%)")
    else:
        update.message.reply_text("❌ Không thể lấy danh sách validator.")

def staked(update: Update, context: CallbackContext):
    data = get_json(f"{LCD}/cosmos/staking/v1beta1/pool")
    if data:
        bonded = int(data["pool"]["bonded_tokens"]) / 1e6
        update.message.reply_text(f"🔒 Tổng staked: {bonded:,.0f} HELI")
    else:
        update.message.reply_text("❌ Không thể lấy dữ liệu staking.")

def supply(update: Update, context: CallbackContext):
    data = get_json(f"{LCD}/cosmos/bank/v1beta1/supply/heli")
    pool = get_json(f"{LCD}/cosmos/staking/v1beta1/pool")
    if data and pool:
        total_supply = int(data["amount"]["amount"]) / 1e6
        bonded = int(pool["pool"]["bonded_tokens"]) / 1e6
        ratio = bonded / total_supply * 100
        update.message.reply_text(
            f"🌍 Tổng cung: {total_supply:,.0f} HELI\n"
            f"🔒 Bonded: {bonded:,.0f} HELI ({ratio:.2f}%)"
        )
    else:
        update.message.reply_text("❌ Không thể lấy dữ liệu supply.")

def apr(update: Update, context: CallbackContext):
    params = get_json(f"{LCD}/cosmos/mint/v1beta1/inflation")
    pool = get_json(f"{LCD}/cosmos/staking/v1beta1/pool")
    supply = get_json(f"{LCD}/cosmos/bank/v1beta1/supply/heli")
    if params and pool and supply:
        inflation = float(params["inflation"])
        bonded = int(pool["pool"]["bonded_tokens"])
        total_supply = int(supply["amount"]["amount"])
        bonded_ratio = bonded / total_supply
        apr_val = (inflation / bonded_ratio) * 100
        update.message.reply_text(f"💹 APR staking ước tính: {apr_val:.2f}%")
    else:
        update.message.reply_text("❌ Không thể tính APR.")

def validator_info(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("⚠️ Dùng: /validator_info <valoper>")
        return
    val = context.args[0]
    data = get_json(f"{LCD}/cosmos/staking/v1beta1/validators/{val}")
    if data and "validator" in data:
        v = data["validator"]
        reply = (
            f"👤 {v['description']['moniker']}\n"
            f"Commission: {float(v['commission']['commission_rates']['rate'])*100:.2f}%\n"
            f"Status: {'Jailed' if v['jailed'] else 'Active'}\n"
            f"Tokens: {int(v['tokens'])/1e6:,.0f} HELI"
        )
        update.message.reply_text(reply)
    else:
        update.message.reply_text("❌ Validator không tồn tại.")

def delegations(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("⚠️ Dùng: /delegations <wallet>")
        return
    wallet = context.args[0]
    data = get_json(f"{LCD}/cosmos/staking/v1beta1/delegations/{wallet}")
    if data and "delegation_responses" in data:
        delegs = data["delegation_responses"]
        msg = [f"🔑 Ví {wallet} delegating:"]
        for d in delegs:
            val = d["delegation"]["validator_address"]
            amt = int(d["balance"]["amount"]) / 1e6
            msg.append(f"→ {amt:,.0f} HELI tới {val}")
        update.message.reply_text("\n".join(msg))
    else:
        update.message.reply_text("❌ Không có delegations.")

def rewards(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("⚠️ Dùng: /rewards <wallet>")
        return
    wallet = context.args[0]
    data = get_json(f"{LCD}/cosmos/distribution/v1beta1/delegators/{wallet}/rewards")
    if data and "total" in data:
        total = sum(int(r["amount"]) for r in data["total"]) / 1e6 if data["total"] else 0
        update.message.reply_text(f"💰 Rewards có thể claim: {total:,.2f} HELI")
    else:
        update.message.reply_text("❌ Không thể lấy dữ liệu rewards.")

def network(update: Update, context: CallbackContext):
    block = get_json(f"{LCD}/cosmos/base/tendermint/v1beta1/blocks/latest")
    pool = get_json(f"{LCD}/cosmos/staking/v1beta1/pool")
    supply = get_json(f"{LCD}/cosmos/bank/v1beta1/supply/heli")
    inflation = get_json(f"{LCD}/cosmos/mint/v1beta1/inflation")
    if block and pool and supply and inflation:
        height = block["block"]["header"]["height"]
        bonded = int(pool["pool"]["bonded_tokens"]) / 1e6
        total_supply = int(supply["amount"]["amount"]) / 1e6
        ratio = bonded / total_supply * 100
        apr_val = (float(inflation["inflation"]) / (bonded/total_supply)) * 100
        reply = (
            f"⛓ Block: {height}\n"
            f"🔒 Bonded: {bonded:,.0f} HELI ({ratio:.2f}%)\n"
            f"💹 APR ước tính: {apr_val:.2f}%"
        )
        update.message.reply_text(reply)
    else:
        update.message.reply_text("❌ Không thể lấy dữ liệu network.")

# ===== Main =====
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(CommandHandler("unbonding", unbonding))
    dp.add_handler(CommandHandler("validator", validator))
    dp.add_handler(CommandHandler("staked", staked))
    dp.add_handler(CommandHandler("supply", supply))
    dp.add_handler(CommandHandler("apr", apr))
    dp.add_handler(CommandHandler("validator_info", validator_info))
    dp.add_handler(CommandHandler("delegations", delegations))
    dp.add_handler(CommandHandler("rewards", rewards))
    dp.add_handler(CommandHandler("network", network))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
