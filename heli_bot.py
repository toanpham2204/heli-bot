import pandas as pd
import ta
import json
import time
import aiohttp
import os
import re
import asyncio
from collections import deque, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging, requests, json
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime, timedelta, timezone
from dateutil import parser
from bs4 import BeautifulSoup

# Khởi tạo order_memory lưu tối đa 12 lần check ≈ 1 phút
order_memory = deque(maxlen=60)  # lưu 60 lần check ≈ 1 giờ nếu check mỗi phút
THRESHOLD_COUNT = 8  # ngưỡng spam lệnh
CHECK_INTERVAL = 60  # giây

THRESHOLD_WALL = 1_000_000   # 1 triệu HELI
MAX_PRICEDISPLAY = 10              # số mức giá hiển thị

# Lưu chat_id của user khi /start
user_chats = set()

# -------------------------------
# Cấu hình
# -------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ===========================
# 1. Lấy BOT_TOKEN từ biến môi trường
# ===========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
LCD = "https://lcd.helichain.com"
PORT = int(os.getenv("PORT", 8080))  # Render cấp PORT
WEBHOOK_URL = os.getenv("RENDER_URL")  # https://<appname>.onrender.com
EXPLORER_URL = "https://explorer.helichain.com/Helichain/tokens/native/uheli"

# ====== API Helpers ======
BASE_URL = "https://api.mexc.com/api/v3"
API_URL = "https://api.mexc.com/api/v3/depth?symbol=HELIUSDT&limit=500"

if not BOT_TOKEN:
    raise ValueError("⚠️ Chưa thiết lập biến môi trường BOT_TOKEN")

CORE_WALLETS = {
    "heli1ve27kkz6t8st902a6x4tz9fe56j6c87w92vare": "Ví Incentive Ecosystem",
    "heli1vzu8p83d2l0rswtllpqdelj4dewlty6r4kjfwa": "Ví Core Team",
    "heli13w3en6ny39srs23gayt7wz9faayezqwqekzwmt": "Ví DAOs treasury",
    "heli196slpj6yrqxj74ftpqspuzd609rqu9wl6j6fde": "Ví nhận từ DAOs"
}

# Bộ nhớ tạm để lưu snapshot
last_snapshot = {"asks": 0, "bids": 0, "time": 0}

# -------------------------------
# Quản lý User
# -------------------------------
ADMIN_ID = 2028673755
# Đọc danh sách ID từ biến môi trường ALLOWED_IDS
env_ids = os.getenv("ALLOWED_IDS", "")
ALLOWED_USERS = set()

if env_ids.strip():
    # loại bỏ khoảng trắng khi split
    ALLOWED_USERS = set(int(uid.strip()) for uid in env_ids.split(",") if uid.strip())

# Luôn đảm bảo ADMIN_ID nằm trong danh sách
ALLOWED_USERS.add(ADMIN_ID)

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_allowed(user_id):
        await update.message.reply_text(
            f"👤 ID của bạn là `{user_id}` và đã có quyền.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚠️ ID của bạn là `{user_id}` nhưng *chưa được cấp quyền.*\n"
            f"👉 Hãy gửi ID này cho admin để thêm vào biến `ALLOWED_IDS`.",
            parse_mode="Markdown"
        )

async def showusers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn không có quyền dùng lệnh này.")
        return

    if not ALLOWED_USERS:
        await update.message.reply_text("⚠️ Hiện chưa có ID nào được cấp quyền.")
        return

    ids_list = "\n".join(f"- `{uid}`" for uid in sorted(ALLOWED_USERS))
    msg = f"👥 *Danh sách ALLOWED_USERS:*\n{ids_list}"
    await update.message.reply_text(msg, parse_mode="Markdown")


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
# Supertrend helper
def supertrend(df, period=10, multiplier=3):
    hl2 = (df['h'] + df['l']) / 2
    atr = ta.volatility.AverageTrueRange(df['h'], df['l'], df['c'], window=period).average_true_range()
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)

    final_upperband = upperband.copy()
    final_lowerband = lowerband.copy()

    for i in range(1, len(df)):
        if df['c'].iloc[i-1] <= final_upperband.iloc[i-1]:
            final_upperband.iloc[i] = min(upperband.iloc[i], final_upperband.iloc[i-1])
        else:
            final_upperband.iloc[i] = upperband.iloc[i]

        if df['c'].iloc[i-1] >= final_lowerband.iloc[i-1]:
            final_lowerband.iloc[i] = max(lowerband.iloc[i], final_lowerband.iloc[i-1])
        else:
            final_lowerband.iloc[i] = lowerband.iloc[i]

    st = pd.Series(index=df.index)
    for i in range(len(df)):
        if df['c'].iloc[i] > final_upperband.iloc[i]:
            st.iloc[i] = 1   # tăng
        elif df['c'].iloc[i] < final_lowerband.iloc[i]:
            st.iloc[i] = -1  # giảm
        else:
            st.iloc[i] = st.iloc[i-1] if i > 0 else 1
    return st

# Hàm phân tích kỹ thuật cho 1 timeframe
def analyze_tf(df):
    if df.empty:
        return ["⚠️ Không có dữ liệu"], "❓ Không xác định"

    ema5 = ta.trend.EMAIndicator(df['c'], 5).ema_indicator().iloc[-1]
    ema20 = ta.trend.EMAIndicator(df['c'], 20).ema_indicator().iloc[-1]
    ma50 = ta.trend.SMAIndicator(df['c'], 50).sma_indicator().iloc[-1]
    ma200 = ta.trend.SMAIndicator(df['c'], 200).sma_indicator().iloc[-1]

    macd = ta.trend.MACD(df['c'])
    macd_val = macd.macd().iloc[-1]
    macd_sig = macd.macd_signal().iloc[-1]

    rsi = ta.momentum.RSIIndicator(df['c'], 14).rsi().iloc[-1]

    sar = ta.trend.PSARIndicator(df['h'], df['l'], df['c']).psar().iloc[-1]
    close = df['c'].iloc[-1]

    vol = df['v'].iloc[-1]
    vol_avg = df['v'].rolling(20).mean().iloc[-1]

    st = supertrend(df).iloc[-1]

    signals = []
    score_up = score_down = 0

    # EMA
    if ema5 > ema20:
        signals.append("📈 EMA: Tăng (EMA5 > EMA20)")
        score_up += 1
    else:
        signals.append("📉 EMA: Giảm (EMA5 < EMA20)")
        score_down += 1

    # MA
    if ma50 > ma200:
        signals.append("📈 MA: Tăng (MA50 > MA200)")
        score_up += 1
    else:
        signals.append("📉 MA: Giảm (MA50 < MA200)")
        score_down += 1

    # MACD
    if macd_val > macd_sig:
        signals.append("📈 MACD: Tăng")
        score_up += 1
    else:
        signals.append("📉 MACD: Giảm")
        score_down += 1

    # RSI
    if rsi > 70:
        signals.append(f"⚠️ RSI {rsi:.1f} → Quá mua")
        score_down += 1
    elif rsi < 30:
        signals.append(f"⚠️ RSI {rsi:.1f} → Quá bán")
        score_up += 1
    else:
        signals.append(f"⚖️ RSI {rsi:.1f} → Trung tính")

    # SAR
    if close > sar:
        signals.append("🔵 SAR: Hỗ trợ (Tăng)")
        score_up += 1
    else:
        signals.append("🔴 SAR: Kháng cự (Giảm)")
        score_down += 1

    # Volume
    if vol > vol_avg * 1.2:
        signals.append("📊 Volume: Tăng mạnh")
        score_up += 1
    elif vol < vol_avg * 0.8:
        signals.append("📊 Volume: Giảm")
        score_down += 1
    else:
        signals.append("📊 Volume: Trung bình")

    # Supertrend
    if st == 1:
        signals.append("🟢 Supertrend: MUA")
        score_up += 1
    else:
        signals.append("🔴 Supertrend: BÁN")
        score_down += 1

    # Nhận định
    if score_up >= score_down * 1.5:
        summary = "⬆️ Xu hướng TĂNG"
    elif score_down >= score_up * 1.5:
        summary = "⬇️ Xu hướng GIẢM"
    else:
        summary = "↔️ Xu hướng SIDEWAY"

    return signals, summary

def format_qty(qty: float) -> str:
    def trim(num):
        return f"{num:.1f}".rstrip("0").rstrip(".")
    if qty >= 1_000_000_000:
        return f"{trim(qty/1_000_000_000)}B"
    elif qty >= 1_000_000:
        return f"{trim(qty/1_000_000)}M"
    elif qty >= 1_000:
        return f"{trim(qty/1_000)}K"
    else:
        return str(int(qty))

def make_ascii_chart(data, label, total):
    lines = []
    if not data:
        return f"{label} Không có\n"
    max_qty = max(qty for _, qty in data)
    for price, qty in data:
        bar_len = int((qty / max_qty) * 10)  # max 10 ô
        bar = "█" * bar_len
        qty_str = format_qty(qty)
        percent = (qty / total * 100) if total else 0
        # Bảng: Giá | Bar | KL | %
        lines.append(f"{label} {price:.8f} | {bar:<10} | {qty_str:<6} | {percent:>4.1f}%")
    # Thêm dòng tổng = 100%
    lines.append(f"{label} {'Tổng':<10} | {'-':<10} | {'-':<6} | 100.0%")
    return "\n".join(lines)



def get_unbonding_heatmap():
    """Trả về heatmap HELI unbonding theo số ngày còn lại."""
    try:
        base = "https://lcd.helichain.com/cosmos/staking/v1beta1"
        vals = requests.get(f"{base}/validators?pagination.limit=200", timeout=20).json()
        validators = vals.get("validators", [])
        heatmap = {i: 0 for i in range(15)}  # 0..14 ngày

        now = datetime.now(timezone.utc)

        for val in validators:
            valoper = val.get("operator_address")
            if not valoper:
                continue
            page_key = None
            while True:
                url = f"{base}/validators/{valoper}/unbonding_delegations"
                params = {"pagination.limit": 200}
                if page_key:
                    params["pagination.key"] = page_key
                r = requests.get(url, params=params, timeout=20).json()

                for ub in r.get("unbonding_responses", []):
                    for entry in ub.get("entries", []):
                        bal = int(entry.get("balance", "0"))
                        ts = entry.get("completion_time")
                        if not ts:
                            continue
                        try:
                            dt = parser.isoparse(ts)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            else:
                                dt = dt.astimezone(timezone.utc)
                            days_left = (dt - now).days
                            if 0 <= days_left <= 14:
                                heatmap[days_left] += bal
                        except Exception as e:
                            logging.warning(f"Lỗi parse completion_time: {e}")

                page_key = r.get("pagination", {}).get("next_key")
                if not page_key:
                    break

        # Chuyển về HELI
        for d in heatmap:
            heatmap[d] = heatmap[d] / 1e6
        return heatmap
    except Exception as e:
        logging.error(f"Lỗi khi lấy heatmap unbonding: {e}")
        return {}

def get_total_unbonding_with_top10():
    """Tính tổng HELI unbonding và top 10 ví unbonding nhiều nhất."""
    try:
        base = "https://lcd.helichain.com/cosmos/staking/v1beta1"
        vals = requests.get(f"{base}/validators?pagination.limit=200", timeout=20).json()
        validators = vals.get("validators", [])
        total = 0
        wallets = {}

        for val in validators:
            valoper = val.get("operator_address")
            if not valoper:
                continue
            page_key = None
            while True:
                url = f"{base}/validators/{valoper}/unbonding_delegations"
                params = {"pagination.limit": 200}
                if page_key:
                    params["pagination.key"] = page_key
                r = requests.get(url, params=params, timeout=20).json()

                for ub in r.get("unbonding_responses", []):
                    delegator = ub.get("delegator_address")
                    for entry in ub.get("entries", []):
                        bal = int(entry.get("balance", "0"))
                        total += bal
                        wallets[delegator] = wallets.get(delegator, 0) + bal

                page_key = r.get("pagination", {}).get("next_key")
                if not page_key:
                    break

        # Sắp xếp top 10 ví
        top10 = sorted(wallets.items(), key=lambda x: x[1], reverse=True)[:10]
        return total / 1e6, [(addr, bal / 1e6) for addr, bal in top10]

    except Exception as e:
        logging.error(f"Lỗi khi lấy unbonding: {e}")
        return None, []

def get_total_supply_uheli():
    """Trả về tổng cung HELI (uheli, int)."""
    try:
        url = "https://lcd.helichain.com/cosmos/bank/v1beta1/supply"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        amounts = r.json().get("supply", [])
        for coin in amounts:
            if coin.get("denom") == "uheli":
                return int(coin.get("amount", 0))
        return None
    except Exception as e:
        logging.error(f"Lỗi khi lấy supply: {e}")
        return None


def get_tx_last_7d(address):
    url = "https://lcd.helichain.com/cosmos/tx/v1beta1/txs"
    end_time = datetime.now(timezone.utc)             # UTC aware
    start_time = end_time - timedelta(days=7)
    page_key = None
    total_sent = 0

    try:
        while True:
            params = {
                "events": f"transfer.sender='{address}'",
                "pagination.limit": 100
            }
            if page_key:
                params["pagination.key"] = page_key

            r = requests.get(url, params=params, timeout=20).json()
            txs = r.get("tx_responses", [])

            for tx in txs:
                try:
                    ts = parser.isoparse(tx.get("timestamp", ""))

                    # ✅ ép về UTC aware
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts = ts.astimezone(timezone.utc)

                except Exception as e:
                    logging.warning(f"Lỗi parse timestamp: {e}")
                    continue

                # ✅ ép start_time và end_time cũng thành UTC aware
                s = start_time.astimezone(timezone.utc)
                e = end_time.astimezone(timezone.utc)

                # So sánh UTC aware <-> UTC aware
                if ts < s:
                    return total_sent  # dừng khi ra khỏi 7 ngày

                if s <= ts <= e:
                    for log in tx.get("logs", []):
                        for event in log.get("events", []):
                            if event.get("type") == "transfer":
                                for attr in event.get("attributes", []):
                                    if attr.get("key") == "amount" and attr.get("value", "").endswith("uheli"):
                                        try:
                                            val = int(attr["value"].replace("uheli", ""))
                                            total_sent += val / 1_000_000
                                        except Exception as e:
                                            logging.warning(f"Lỗi parse amount: {e}")

            page_key = r.get("pagination", {}).get("next_key")
            if not page_key:
                break

    except Exception as e:
        logging.error(f"Lỗi khi lấy tx của {address}: {e}")

    return total_sent

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

def get_total_unbonding():
    """Tính tổng HELI đang unbonding từ tất cả delegator trên toàn mạng."""
    try:
        base = "https://lcd.helichain.com/cosmos/staking/v1beta1"
        # 1. Lấy danh sách validator
        vals = requests.get(f"{base}/validators?pagination.limit=200", timeout=20).json()
        validators = vals.get("validators", [])
        total = 0

        for val in validators:
            valoper = val.get("operator_address")
            if not valoper:
                continue
            page_key = None
            while True:
                url = f"{base}/validators/{valoper}/unbonding_delegations"
                params = {"pagination.limit": 200}
                if page_key:
                    params["pagination.key"] = page_key
                r = requests.get(url, params=params, timeout=20).json()

                for ub in r.get("unbonding_responses", []):
                    for entry in ub.get("entries", []):
                        try:
                            total += int(entry.get("balance", "0"))
                        except:
                            pass

                page_key = r.get("pagination", {}).get("next_key")
                if not page_key:
                    break

        return total / 1e6
    except Exception as e:
        logging.error(f"Lỗi khi lấy unbonding: {e}")
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

def get_balance(address):
    """Lấy balance HELI của ví"""
    try:
        r = requests.get(
            f"https://lcd.helichain.com/cosmos/bank/v1beta1/balances/{address}",
            timeout=10
        )
        r.raise_for_status()
        balances = r.json().get("balances", [])
        for b in balances:
            if b.get("denom") == "uheli":
                return int(b.get("amount", "0")) / 1_000_000
    except requests.exceptions.Timeout:
        logging.error(f"⏱ Timeout khi gọi get_balance({address})")
    except Exception as e:
        logging.error(f"Lỗi get_balance({address}): {e}")
    return 0


def get_staked(address):
    """Lấy tổng HELI đang stake"""
    try:
        r = requests.get(
            f"https://lcd.helichain.com/cosmos/staking/v1beta1/delegations/{address}",
            timeout=10
        )
        r.raise_for_status()
        total = 0
        for d in r.json().get("delegation_responses", []):
            total += int(d.get("balance", {}).get("amount", "0"))
        return total / 1_000_000
    except requests.exceptions.Timeout:
        logging.error(f"⏱ Timeout khi gọi get_staked({address})")
    except Exception as e:
        logging.error(f"Lỗi get_staked({address}): {e}")
    return 0


def get_unstaking(address):
    """Lấy tổng HELI đang unstake"""
    try:
        r = requests.get(
            f"https://lcd.helichain.com/cosmos/staking/v1beta1/delegators/{address}/unbonding_delegations",
            timeout=10
        )
        r.raise_for_status()
        total = 0
        for u in r.json().get("unbonding_responses", []):
            for entry in u.get("entries", []):
                total += int(entry.get("balance", "0"))
        return total / 1_000_000
    except requests.exceptions.Timeout:
        logging.error(f"⏱ Timeout khi gọi get_unstaking({address})")
    except Exception as e:
        logging.error(f"Lỗi get_unstaking({address}): {e}")
    return 0


# -------------------------------
# Commands
# -------------------------------
# --- Lệnh /start ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ Bot khởi động. Sẽ gửi cảnh báo tự động. Bạn đã bắt đầu nhận cảnh báo lệnh mồi.")
    job_queue: JobQueue = context.job_queue
    job_queue.run_repeating(job_detect_doilai, interval=300, first=10, chat_id=chat_id)
    job_queue.run_repeating(job_trend, interval=900, first=30, chat_id=chat_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 Danh sách lệnh khả dụng:

/help - Xem hướng dẫn
/whoami - Hiển thị ID và quyền của bạn
/grant <id> - Cấp quyền tạm thời cho user (admin)
/revoke <id> - Thu hồi tạm thời quyền user (admin)
/clear - Xóa 50 tin nhắn gần đây
/showusers - Liệt kê ID được cấp quyền
/heliinfo - Tổng quan HELI

/staked - Xem tổng HELI đã staking
/unstake - Xem tổng HELI đang unstake
/unbonding_wallets - Xem số ví đang unbonding
/validator - Danh sách validator & trạng thái jail
/status - Trạng thái hệ thống

/price - Giá HELI hiện tại
/supply - Tổng cung HELI
/apy - Tính APY staking (đã trừ commission)
/coreteam - Tình trạng các ví Core Team

/heatmap - Chi tiết lượng unstake trong 14 ngày
/orderbook - Tổng quan cung cầu MUA - BÁN
/flow - Biến động M-B trong 1h
/detect_doilai - Phát hiện ĐỘI LÁI
/alert - Cảnh báo Spam lệnh mồi
/trend - Đánh giá xu hướng HELI
"""
    await update.message.reply_text(help_text)

async def heliinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    
    try:
        # --- Nhóm 1: Mạng & Validator ---
        # Status
        try:
            url = f"{LCD}/cosmos/base/tendermint/v1beta1/blocks/latest"
            r = requests.get(url, timeout=10).json()
            height = r.get("block", {}).get("header", {}).get("height", "N/A")
            proposer = r.get("block", {}).get("header", {}).get("proposer_address", "N/A")
            status_txt = f"⛓ Block height: {height}\n👤 Proposer: {proposer}"
        except:
            status_txt = "⚠️ Không lấy được trạng thái mạng"
        
        # Validator
        vals, validator_txt = [], ""
        try:
            url = f"{LCD}/cosmos/staking/v1beta1/validators?pagination.limit=2000"
            r = requests.get(url, timeout=15).json()
            vals = r.get("validators", [])
            total = len(vals)
            jailed = sum(1 for v in vals if v.get("jailed", False))
            bonded = sum(1 for v in vals if v.get("status") == "BOND_STATUS_BONDED" and not v.get("jailed", False))
            validator_txt = f"Tổng: {total} | Bonded: {bonded} | Jail: {jailed}"
        except:
            validator_txt = "⚠️ Không lấy được validator"

        # Bonded Ratio
        bonded_ratio_txt, bonded, supply_uheli = "", 0, 0
        try:
            pool = get_pool()
            bonded = int(pool.get("bonded_tokens", 0))
            supply_uheli = get_total_supply_uheli()
            ratio = bonded / supply_uheli * 100 if bonded and supply_uheli else 0
            bonded_ratio_txt = f"{ratio:.2f}%"
        except:
            bonded_ratio_txt = "⚠️ Không tính được"

        # APY
        apy_txt = ""
        try:
            inflation = get_inflation()
            top_val = get_top_validator()
            commission = float(top_val.get("commission", {}).get("commission_rates", {}).get("rate", 0)) if top_val else 0
            apy_value = inflation / (bonded / supply_uheli) * (1 - commission) * 100 if bonded and supply_uheli else 0
            apy_txt = f"{apy_value:.2f}%"
        except:
            apy_txt = "⚠️ Không tính được"

        # Top 3 Validator
        top3_validators_txt = ""
        try:
            sorted_vals = sorted(vals, key=lambda v: int(v.get("tokens", 0)), reverse=True)[:3]
            lines = []
            for i, v in enumerate(sorted_vals, 1):
                moniker = v.get("description", {}).get("moniker", "N/A")
                tokens = int(v.get("tokens", 0))
                percent = tokens / bonded * 100 if bonded else 0
                lines.append(f"{i}. {moniker} — {tokens/1e6:,.0f} HELI ({percent:.2f}%)")
            top3_validators_txt = "\n".join(lines)
        except:
            top3_validators_txt = "⚠️ Không lấy được Top Validator"

        # --- Nhóm 2: Tokenomics ---
        supply_txt, staked_txt, unstake_txt, unbonding_wallets_txt = "", "", "", ""

        # Supply
        try:
            supply = get_total_supply_uheli() / 1e6
            supply_txt = f"{supply:,.0f} HELI"
        except:
            supply_txt = "⚠️ Không lấy được supply"

        # Staked
        try:
            pool = get_pool()
            staked_txt = f"{int(pool.get('bonded_tokens',0))/1e6:,.2f} HELI"
        except:
            staked_txt = "⚠️ Không lấy được staking"

        # Unstake
        total_unbonding = 0
        try:
            total_unbonding = get_total_unbonding()
            unstake_txt = f"{total_unbonding:,.2f} HELI"
        except:
            unstake_txt = "⚠️ Không lấy được unstake"

        # Unbonding wallets
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
            unbonding_wallets_txt = f"{len(wallets)} ví"
        except:
            unbonding_wallets_txt = "⚠️ Không lấy được"

        # Top 5 Unstake
        top5_unstake_txt = ""
        try:
            vals_url = f"{LCD}/cosmos/staking/v1beta1/validators?pagination.limit=2000"
            vals = requests.get(vals_url, timeout=15).json().get("validators", [])
            wallet_unbond = {}
            for v in vals:
                valoper = v.get("operator_address")
                url = f"{LCD}/cosmos/staking/v1beta1/validators/{valoper}/unbonding_delegations?pagination.limit=2000"
                r = requests.get(url, timeout=15).json()
                for resp in r.get("unbonding_responses", []):
                    delegator = resp.get("delegator_address")
                    entries = resp.get("entries", [])
                    total_amt = sum(int(e.get("balance", 0)) for e in entries)
                    wallet_unbond[delegator] = wallet_unbond.get(delegator, 0) + total_amt

            sorted_wallets = sorted(wallet_unbond.items(), key=lambda x: x[1], reverse=True)[:5]
            lines = []
            for i, (addr, amt) in enumerate(sorted_wallets, 1):
                percent = amt / total_unbonding * 100 if total_unbonding else 0
                lines.append(f"{i}. {addr[:8]}... — {amt/1e6:,.0f} HELI ({percent:.2f}%)")
            top5_unstake_txt = "\n".join(lines) if lines else "Không có ví unbonding"
        except:
            top5_unstake_txt = "⚠️ Không lấy được Top Unstake"

        # --- Nhóm 3: Thị trường ---
        price_txt = ""
        try:
            url = "https://api.mexc.com/api/v3/ticker/price?symbol=HELIUSDT"
            r = requests.get(url, timeout=10).json()
            price_txt = f"${float(r.get('price', 0)):.6f}"
        except:
            price_txt = "⚠️ Không lấy được giá"

        # --- Kết quả tổng hợp ---
        msg = (
            "📊 *HELI Overview*\n\n"
            "🌐 *Mạng & Validator*\n"
            f"{status_txt}\n"
            f"🖥 Validator: {validator_txt}\n"
            f"📈 Bonded Ratio: {bonded_ratio_txt}\n"
            f"💰 APY: {apy_txt}\n"
            f"🏆 Top 3 Validator:\n{top3_validators_txt}\n\n"
            "🔗 *Tokenomics*\n"
            f"💎 Staked: {staked_txt}\n"
            f"🔓 Unstake: {unstake_txt}\n"
            f"👛 Unbonding Wallets: {unbonding_wallets_txt}\n"
            f"💰 Supply: {supply_txt}\n"
            f"📤 Top 5 Unstake:\n{top5_unstake_txt}\n\n"
            "💹 *Thị trường*\n"
            f"💲 Price: {price_txt}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy heliinfo: {e}")


# ===========================
# 2. Dữ liệu giả lập / placeholder
# ===========================
async def get_orderbook2():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/depth?symbol=HELIUSDT&limit=50") as resp:
            data = await resp.json()
            return {
                "bids": [(float(p), float(q)) for p, q in data["bids"]],
                "asks": [(float(p), float(q)) for p, q in data["asks"]]
            }

async def get_price_data():
    async with aiohttp.ClientSession() as session:
        # Lấy giá hiện tại
        async with session.get(f"{BASE_URL}/ticker/price?symbol=HELIUSDT") as resp:
            price_data = await resp.json()
            current_price = float(price_data["price"])

        # Lấy dữ liệu nến để tính EMA
        async with session.get(f"{BASE_URL}/klines?symbol=HELIUSDT&interval=5m&limit=50") as resp:
            klines = await resp.json()
            closes = [float(k[4]) for k in klines]  # giá đóng cửa

        ema5 = sum(closes[-5:]) / 5
        ema20 = sum(closes[-20:]) / 20
        avg24h = sum(closes) / len(closes)

        return {"current": current_price, "ema5": ema5, "ema20": ema20, "avg24h": avg24h}

# ===========================
# 3. Phát hiện đội lái (detect_doilai)
# ===========================
recent_orders = deque()
THRESHOLD_SMALL_ORDER = 40000  # Khối lượng tối đa để coi là lệnh mồi
THRESHOLD_SPAM_COUNT = 10
MAX_DISPLAY = 20  # Số mục hiển thị tối đa

async def detect_doilai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return
    # Lấy orderbook hiện tại
    orderbook = await get_orderbook2()
    all_orders = orderbook["bids"] + orderbook["asks"]  # Nối cả mua và bán

    # Lọc lệnh mồi (KL < 10k)
    small_orders = [o for o in all_orders if o[1] < THRESHOLD_SMALL_ORDER]

    if small_orders:
        # Gộp khối lượng theo giá
        summary = defaultdict(float)
        for price, qty in small_orders:
            summary[price] += qty

        # Tạo message
        msg = f"🚨 Phát hiện {len(small_orders)} lệnh mồi bất thường:\n"
        for price, total_qty in sorted(summary.items())[:MAX_DISPLAY]:
            msg += f"💰 Giá {price} - KL {total_qty}\n"

        # Nếu còn nhiều giá khác, thông báo
        if len(summary) > MAX_DISPLAY:
            msg += f"...và {len(summary) - MAX_DISPLAY} giá khác không hiển thị"

    else:
        msg = "✅ Không phát hiện lệnh mồi."

    await update.message.reply_text(msg)


# Background task kiểm tra spam lệnh mồi
async def alert_loop(bot):
    while True:
        orderbook = await get_orderbook2()
        all_orders = orderbook["bids"] + orderbook["asks"]

        # Lọc lệnh mồi KL <10k
        small_orders = [o for o in all_orders if o[1] < THRESHOLD_SMALL_ORDER]

        if len(small_orders) >= THRESHOLD_COUNT:
            # Gộp KL theo giá
            summary = defaultdict(float)
            for price, qty in small_orders:
                summary[price] += qty

            # Tạo message cảnh báo
            msg = f"⚠️ Phát hiện {len(small_orders)} lệnh mồi bất thường:\n"
            for price, total_qty in sorted(summary.items())[:MAX_DISPLAY]:
                msg += f"💰 Giá {price} - KL {total_qty}\n"
            if len(summary) > MAX_DISPLAY:
                msg += f"...và {len(summary) - MAX_DISPLAY} giá khác không hiển thị"

            # Gửi cảnh báo cho tất cả user
            for chat_id in user_chats:
                await bot.send_message(chat_id=chat_id, text=msg)

        await asyncio.sleep(CHECK_INTERVAL)

# ===========================
# 4. Cảnh báo spam lệnh mồi (alert)
# ===========================

async def alert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return

    orderbook = await get_orderbook2()
    all_orders = orderbook["bids"] + orderbook["asks"]
    small_orders = [o for o in all_orders if o[1] < THRESHOLD_SMALL_ORDER]

    if len(small_orders) >= THRESHOLD_COUNT:
        summary = defaultdict(float)
        for price, qty in small_orders:
            summary[price] += qty

        msg = f"⚠️ Phát hiện {len(small_orders)} lệnh mồi bất thường:\n"
        for price, total_qty in sorted(summary.items())[:MAX_DISPLAY]:
            msg += f"💰 Giá {price} - KL {total_qty}\n"
        if len(summary) > MAX_DISPLAY:
            msg += f"...và {len(summary) - MAX_DISPLAY} giá khác không hiển thị"

        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("✅ Chưa phát hiện lệnh mồi.")

# ===========================
# 5. Đánh giá xu hướng Heli (trend)
# ===========================

async def trend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return
    symbol = "HELIUSDT"
    base_url = "https://api.mexc.com/api/v3/klines"
    tf_map = {"1h": "Ngắn hạn (1h)", "4h": "Trung hạn (4h)", "1d": "Dài hạn (1D)"}

    results = {}
    summaries = {}

    for tf in tf_map:
        url = f"{base_url}?symbol={symbol}&interval={tf}&limit=300"
        data = requests.get(url).json()

        # MEXC klines trả về 8 cột
        df = pd.DataFrame(data, columns=["t","o","h","l","c","v","ct","q"])
        df["c"] = df["c"].astype(float)
        df["h"] = df["h"].astype(float)
        df["l"] = df["l"].astype(float)
        df["v"] = df["v"].astype(float)

        signals, summary = analyze_tf(df)
        results[tf] = signals
        summaries[tf] = summary

    # Xuất báo cáo
    msg = "💹 *Xu hướng HELI*\n━━━━━━━━━━━━━━━\n"
    for tf, label in tf_map.items():
        msg += f"\n⏱ {label}:\n" + "\n".join(results[tf]) + f"\n👉 {summaries[tf]}\n"

    msg += "\n━━━━━━━━━━━━━━━\n📊 *Nhận định tổng thể:*\n"
    msg += f"• Xu hướng 1h: {summaries['1h']}\n"
    msg += f"• Xu hướng 4h: {summaries['4h']}\n"
    msg += f"• Xu hướng 1D: {summaries['1d']}\n"

    # Trung + Dài hạn
    if summaries["4h"] == summaries["1d"]:
        msg += f"• Trung & Dài hạn: {summaries['4h']}\n"
    else:
        msg += f"• Trung & Dài hạn: {summaries['4h']} / {summaries['1d']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

# Hàm lấy orderbook async
async def get_orderbook():
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL) as resp:
            data = await resp.json()
            asks = data.get("asks", [])
            bids = data.get("bids", [])

            total_asks = sum(float(qty) for price, qty in asks)
            total_bids = sum(float(qty) for price, qty in bids)

            return total_asks, total_bids, asks[:5], bids[:5]

async def get_orderbookfull():
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL) as resp:
            data = await resp.json()
            asks = data.get("asks", [])
            bids = data.get("bids", [])

            total_asks = sum(float(qty) for price, qty in asks)
            total_bids = sum(float(qty) for price, qty in bids)
            return total_asks, total_bids

# ====== Job Tasks ======
async def job_detect_doilai(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    orderbook = await get_orderbook2()
    big_orders = [o for o in orderbook["bids"] + orderbook["asks"] if o[1] > 1000]

    if big_orders:
        msg = "🚨 [Auto] Phát hiện lệnh mồi bất thường:\n"
        for price, qty in big_orders:
            msg += f"💰 Giá {price} - KL {qty}\n"
        await context.bot.send_message(chat_id, msg)

async def job_trend(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    data = await get_price_data()

    if data["ema5"] > data["ema20"] and data["current"] > data["ema5"]:
        trend = "📈 Xu hướng: Tăng"
    elif data["ema5"] < data["ema20"] and data["current"] < data["ema20"]:
        trend = "📉 Xu hướng: Giảm"
    else:
        trend = "➖ Xu hướng: Sideway"

    msg = (
        f"{trend}\n\n"
        f"Giá hiện tại: {data['current']}\n"
        f"EMA5: {data['ema5']:.4f}\n"
        f"EMA20: {data['ema20']:.4f}\n"
        f"Trung bình 24h: {data['avg24h']:.4f}"
    )
    await context.bot.send_message(chat_id, msg)

# Command handler cho Telegram
# Lệnh /flow
async def flow(update, context):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    global last_snapshot
    total_asks, total_bids = await get_orderbookfull()
    now = int(time.time())

    msg = "📊 Dòng tiền Orderbook HELI/USDT (MEXC)\n"

    if last_snapshot["time"] == 0:
        # Lần đầu chạy
        last_snapshot = {"asks": total_asks, "bids": total_bids, "time": now}
        msg += "✅ Snapshot đầu tiên đã lưu. Hãy gọi lại lệnh sau để xem biến động."
    else:
        delta_time = (now - last_snapshot["time"]) / 60
        asks_diff = total_asks - last_snapshot["asks"]
        bids_diff = total_bids - last_snapshot["bids"]

        msg += (
            f"⏱ Thời gian so sánh: {delta_time:.1f} phút\n"
            f"🔴 Asks: {last_snapshot['asks']:,.2f} → {total_asks:,.2f} "
            f"({asks_diff:+,.2f})\n"
            f"🟢 Bids: {last_snapshot['bids']:,.2f} → {total_bids:,.2f} "
            f"({bids_diff:+,.2f})\n\n"
        )

        if asks_diff > bids_diff and asks_diff > 0:
            msg += "⚠️ Lực **bán** bổ sung nhiều hơn → áp lực giá xuống.\n"
        elif bids_diff > asks_diff and bids_diff > 0:
            msg += "✅ Lực **mua** bổ sung nhiều hơn → có hỗ trợ tăng giá.\n"
        else:
            msg += "➖ Dòng tiền chưa rõ rệt, thị trường cân bằng.\n"

        # Cập nhật snapshot
        last_snapshot = {"asks": total_asks, "bids": total_bids, "time": now}

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def orderbook(update, context):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    total_asks, total_bids, top_asks, top_bids = await get_orderbook()

    ratio = (total_asks / total_bids) if total_bids > 0 else float("inf")

    msg = (
        f"📊 Orderbook HELI/USDT (MEXC)\n"
        f"🔴 Tổng lượng chờ bán (asks): {total_asks:,.2f} HELI\n"
        f"🟢 Tổng lượng chờ mua (bids): {total_bids:,.2f} HELI\n"
        f"📈 Tỷ lệ Bán/Mua: {ratio:.4f}x\n\n"
    )

    if ratio > 1.2:
        msg += "⚠️ Áp lực bán đang chiếm ưu thế.\n"
    elif ratio < 0.8:
        msg += "✅ Lực mua mạnh hơn, có hỗ trợ giá.\n"
    else:
        msg += "➖ Thị trường cân bằng, chưa rõ xu hướng.\n"

    msg += "Top 5 lệnh bán (asks):\n"
    for price, qty in top_asks:
        msg += f"🔴 Giá {price} | SL {qty}\n"

    msg += "\nTop 5 lệnh mua (bids):\n"
    for price, qty in top_bids:
        msg += f"🟢 Giá {price} | SL {qty}\n"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

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

async def unbonding_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# === HÀM /heatmap ===
async def heatmap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return

    sent = await update.message.reply_text("⏳ Đang phân tích heatmap unbonding...")
    loop = asyncio.get_running_loop()
    heatmap = await loop.run_in_executor(None, get_unbonding_heatmap)

    if not heatmap:
        await sent.edit_text("⚠️ Không lấy được dữ liệu heatmap từ LCD.")
        return

    msg = "🌡️ Heatmap giải phóng HELI (14 ngày tới):"
    for d in range(15):
        if heatmap.get(d, 0) > 0:
            msg += f"\n🗓️ Ngày +{d}: {heatmap[d]:,.2f} HELI"

    await sent.edit_text(msg)

# === HÀM /unstake ===
async def unstake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return

    sent = await update.message.reply_text("⏳ Đang tính tổng HELI unbonding toàn mạng...")
    loop = asyncio.get_running_loop()
    total, top10 = await loop.run_in_executor(None, get_total_unbonding_with_top10)

    if total is None:
        await sent.edit_text("⚠️ Không lấy được dữ liệu unbonding từ LCD.")
        return

    msg = f"🔓 Tổng HELI đang unbonding toàn mạng: {total:,.2f} HELI\n\n🏆 Top 10 ví unbonding:"
    for addr, bal in top10:
        msg += f"\n- {addr[:12]}...: {bal:,.2f} HELI"

    await sent.edit_text(msg)

async def bonded_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền.")
        return

    sent = await update.message.reply_text("⏳ Đang tính Bonded Ratio...")

    loop = asyncio.get_running_loop()

    def work_sync():
        try:
            r = requests.get("https://lcd.helichain.com/cosmos/staking/v1beta1/pool", timeout=10)
            r.raise_for_status()
            pool = r.json().get("pool", {})
            bonded_uheli = int(pool.get("bonded_tokens", 0))
        except Exception as e:
            logging.error(f"Lỗi lấy bonded: {e}")
            return None, None, "Không lấy được dữ liệu bonded."

        supply_uheli = get_total_supply_uheli()
        if not supply_uheli:
            return None, None, "Không lấy được total supply."

        return bonded_uheli, supply_uheli, None

    bonded_uheli, supply_uheli, err = await loop.run_in_executor(None, work_sync)

    if err:
        await sent.edit_text(f"⚠️ {err}")
        return

    bonded = bonded_uheli / 1e6
    supply = supply_uheli / 1e6
    ratio = bonded / supply * 100

    await sent.edit_text(f"📊 Bonded Ratio: {ratio:.4f}%")


async def apy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    pool = get_pool()
    bonded = int(pool.get("bonded_tokens", 0))
    not_bonded = int(pool.get("not_bonded_tokens", 0))
    supply_uheli = get_total_supply_uheli()
    if bonded == 0 or supply_uheli == 0:
        await update.message.reply_text("⚠️ Không thể tính APY.")
        return
    bonded_ratio = bonded / supply_uheli
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
            await update.message.reply_text(f"💲 Giá HELI hiện tại (MEXC): ${price_usd:,.6f}")
            return

        # Fallback CoinGecko
        url_cg = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "heli", "vs_currencies": "usd"}
        r = requests.get(url_cg, params=params, timeout=10).json()
        price_usd = r.get("heli", {}).get("usd")

        if price_usd:
            await update.message.reply_text(f"💲 Giá HELI hiện tại (CoinGecko): ${price_usd:,.6f}")
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

async def coreteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ Đang kiểm tra ví core team...")

    CORE_WALLETS = {
    "heli1ve27kkz6t8st902a6x4tz9fe56j6c87w92vare": "Ví Incentive Ecosystem",
    "heli1vzu8p83d2l0rswtllpqdelj4dewlty6r4kjfwa": "Ví Core Team",
    "heli13w3en6ny39srs23gayt7wz9faayezqwqekzwmt": "Ví DAOs treasury",
    "heli196slpj6yrqxj74ftpqspuzd609rqu9wl6j6fde": "Ví nhận từ DAOs"
    }

    results = []
    cutoff = datetime.utcnow() - timedelta(days=7)

    for address, note in CORE_WALLETS.items():
        try:
            balance = get_balance(address)
            staked = get_staked(address)
            unstake = get_unstaking(address)

            results.append(
                f"🔹 `{address}` ({note})\n"
                f"   💰 Balance: {balance:,.0f} HELI\n"
                f"   🔒 Staked: {staked:,.0f} HELI\n"
                f"   ⏳ Unstake: {unstake:,.0f} HELI\n"
            )
        except Exception as e:
            results.append(f"⚠️ Lỗi khi xử lý ví {address} ({note})")

    msg = "📊 **Tình trạng ví Core Team**\n\n" + "\n\n".join(results)
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def allaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(EXPLORER_URL, timeout=20) as resp:
                html = await resp.text()

        match = re.search(r"A total of\s+([\d,]+)\s+token holders found", html)
        if match:
            total_accounts = int(match.group(1).replace(",", ""))
            msg = f"👥 Total Accounts: {total_accounts}"
        else:
            msg = "👥 Total Accounts: Không lấy được từ Explorer"

        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi khi lấy total accounts: {e}")

async def get_market_price():
    try:
        # Ưu tiên lấy giá từ MEXC
        url = "https://api.mexc.com/api/v3/ticker/price?symbol=HELIUSDT"
        r = requests.get(url, timeout=10).json()
        price_usd = float(r.get("price", 0))

        if price_usd > 0:
            return price_usd

        # Fallback CoinGecko
        url_cg = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "heli", "vs_currencies": "usd"}
        r = requests.get(url_cg, params=params, timeout=10).json()
        price_usd = r.get("heli", {}).get("usd")

        return price_usd if price_usd else None
    except Exception as e:
        print(f"⚠️ Lỗi khi lấy giá: {e}")
        return None

# Lệnh /support_resist
async def support_resist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn chưa được cấp quyền. Dùng /whoami gửi admin.")
        return

    # Biên độ mặc định ±20% hoặc do user nhập
    try:
        RANGE = float(context.args[0]) if context.args else 0.20
        if RANGE <= 0 or RANGE > 1:  # giới hạn hợp lý (0 < RANGE <= 1)
            RANGE = 0.20
    except (ValueError, IndexError):
        RANGE = 0.20

    # Lấy giá thị trường
    market_price = await get_market_price()
    if not market_price:
        await update.message.reply_text("⚠️ Không lấy được giá thị trường.")
        return
    min_price = market_price * (1 - RANGE)
    max_price = market_price * (1 + RANGE)

    # Lấy orderbook
    orderbook = await get_orderbook2()
    bids = orderbook["bids"]
    asks = orderbook["asks"]
    if not bids or not asks:
        await update.message.reply_text("❌ Không lấy được dữ liệu orderbook.")
        return

    # Gom support/resistance
    support = defaultdict(float)
    resistance = defaultdict(float)

    for price, qty in bids:
        if qty >= THRESHOLD_WALL and min_price <= price <= max_price:
            support[price] += qty

    for price, qty in asks:
        if qty >= THRESHOLD_WALL and min_price <= price <= max_price:
            resistance[price] += qty

    # -------------------------
    # 1️⃣ Tổng quan
    total_support = sum(support.values())
    total_resistance = sum(resistance.values())

    if total_support > total_resistance * 1.4:
        direction_icon = "⬆️"
    elif total_resistance > total_support * 1.4:
        direction_icon = "⬇️"
    else:
        direction_icon = "↔️"

    msg = (
        f"📊 *Hỗ trợ - Kháng cự* quanh giá thị trường *{market_price:.8f}* (±{RANGE*100:.1f}%)\n"
        f"📉 *Biên độ giá hiển thị*: {min_price:.8f} – {max_price:.8f}\n\n"
        f"🟢 *Tổng Hỗ trợ*: {format_qty(total_support)} HELI\n"
        f"🔴 *Tổng Kháng cự*: {format_qty(total_resistance)} HELI\n"
    )

    if total_support > 0 and total_resistance > 0:
        ratio_support = total_support / total_resistance
        ratio_resist = total_resistance / total_support
        msg += f"⚖️ *Tỷ lệ Hỗ trợ/Kháng cự*: {ratio_support:.2f} - {ratio_resist:.2f} {direction_icon}\n\n"
    else:
        msg += "⚖️ *Tỷ lệ Hỗ trợ/Kháng cự*: Không đủ dữ liệu\n\n"

    # -------------------------
    # 2️⃣ Chi tiết
    if support:
        sorted_support = sorted(
            [(p, q) for p, q in support.items() if min_price <= p <= max_price],
            reverse=True,
            key=lambda x: x[0]
        )[:5]
        msg += "🟢 *Hỗ trợ mạnh (Giá | KL)*\n"
        msg += "--------------------------\n"
        msg += make_ascii_chart(sorted_support, "🟢", total_support) + "\n\n"
    else:
        msg += "🟢 Không có hỗ trợ mạnh\n\n"

    if resistance:
        sorted_resistance = sorted(
            [(p, q) for p, q in resistance.items() if min_price <= p <= max_price],
            key=lambda x: x[0]
        )[:5]
        msg += "🔴 *Kháng cự mạnh (Giá | KL)*\n"
        msg += "--------------------------\n"
        msg += make_ascii_chart(sorted_resistance, "🔴", total_resistance) + "\n\n"
    else:
        msg += "🔴 Không có kháng cự mạnh\n\n"

    # -------------------------
    # 3️⃣ Nhận định
    msg += "📈 *Nhận định*: "
    if total_support > total_resistance * 1.4:
        msg += "⬆️ Xu hướng TĂNG (Hỗ trợ > Kháng cự)"
    elif total_resistance > total_support * 1.4:
        msg += "⬇️ Xu hướng GIẢM (Kháng cự > Hỗ trợ)"
    else:
        msg += "↔️ Xu hướng CÂN BẰNG (sideway)"

    await update.message.reply_text(msg, parse_mode="Markdown")



# -------------------------------
# Main
# -------------------------------
def main():
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(connect_timeout=20, read_timeout=20, write_timeout=20, pool_timeout=20)
    application = Application.builder().token(BOT_TOKEN).request(request).build()


    # Lệnh quản lý user
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("grant", grant))
    application.add_handler(CommandHandler("revoke", revoke))


    
    # Đăng ký command
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("unstake", unstake))
    application.add_handler(CommandHandler("unbonding_wallets", unbonding_wallets))
    application.add_handler(CommandHandler("bonded_ratio", bonded_ratio))
    application.add_handler(CommandHandler("apy", apy))
    application.add_handler(CommandHandler("supply", supply))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("staked", staked))
    application.add_handler(CommandHandler("validator", validator))
    application.add_handler(CommandHandler("coreteam", coreteam))
    application.add_handler(CommandHandler("heatmap", heatmap))
    application.add_handler(CommandHandler("allaccounts", allaccounts))
    application.add_handler(CommandHandler("orderbook", orderbook))
    application.add_handler(CommandHandler("flow", flow))
    application.add_handler(CommandHandler("detect_doilai", detect_doilai))
    application.add_handler(CommandHandler("alert", alert_handler))
    application.add_handler(CommandHandler("trend", trend_handler))
    application.add_handler(CommandHandler("support_resist", support_resist_handler))
    application.add_handler(CommandHandler("heliinfo", heliinfo))
    application.add_handler(CommandHandler("showusers", showusers_handler))

    logging.info("🚀 Bot HeliChain đã khởi động...")

    # ✅ Chạy webhook cho Render
    if os.getenv("RENDER") == "true":
        port = int(os.environ.get("PORT", "10000"))
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )

if __name__ == "__main__":
    main()
