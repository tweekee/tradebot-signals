import os
import asyncio
import requests
import pandas as pd
import ta
import datetime
import pytz
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

# === Загрузка .env ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FOREX_API_KEY = os.getenv("FOREX_API_KEY", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002108450567"))  # ID канала для проверки подписки

if not BOT_TOKEN or ADMIN_ID == 0:
    raise ValueError("❌ Ошибка: проверь .env (BOT_TOKEN или ADMIN_ID не найдены)")

# === Настройки ===
TIMEFRAME = "1min"
SIGNALS_ENABLED = True
SIGNAL_LIMIT_PER_DAY = 15
signal_count_today = 0

# Пары только форекс
PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
    "NZD/USD", "USD/CAD", "EUR/JPY", "EUR/GBP", "GBP/JPY"
]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === Получение данных с Twelve Data (форекс) ===
def get_forex_ohlcv(symbol, interval="1min", outputsize=100):
    try:
        base, quote = symbol.split("/")
        url = (
            f"https://api.twelvedata.com/time_series?"
            f"symbol={base}/{quote}&interval={interval}&outputsize={outputsize}&apikey={FOREX_API_KEY}"
        )
        r = requests.get(url, timeout=10)
        data = r.json()

        if "values" not in data:
            print(f"⚠️ Ошибка загрузки {symbol}: {data}")
            return None

        df = pd.DataFrame(data["values"])
        df = df.rename(columns={"datetime": "time"})
        df = df.astype({
            "open": float, "high": float, "low": float, "close": float
        })
        df = df.sort_values("time").reset_index(drop=True)
        return df

    except Exception as e:
        print(f"⚠️ Ошибка загрузки {symbol}: {e}")
        return None

# === Проверка силы сигнала ===
def check_strong_signal(df):
    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["signal"] = macd.macd_signal()

    bullish = (
        df["ema_fast"].iloc[-1] > df["ema_slow"].iloc[-1] and
        df["rsi"].iloc[-1] < 45 and
        df["macd"].iloc[-1] > df["signal"].iloc[-1]
    )

    bearish = (
        df["ema_fast"].iloc[-1] < df["ema_slow"].iloc[-1] and
        df["rsi"].iloc[-1] > 55 and
        df["macd"].iloc[-1] < df["signal"].iloc[-1]
    )

    if bullish:
        return "Вверх"
    elif bearish:
        return "Вниз"
    else:
        return None

# === Проверка подписки ===
async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# === Цикл сигналов ===
async def signal_loop():
    global SIGNALS_ENABLED, signal_count_today
    print("✅ Цикл сигналов запущен!")

    tz = pytz.timezone("Europe/Kiev")

    while True:
        now = datetime.datetime.now(tz)
        weekday = now.weekday()  # 0 = понедельник, 6 = воскресенье

        # Только будни, с 10:00 до 18:00
        if weekday >= 5 or now.hour < 10 or now.hour >= 18:
            if now.hour == 0 and signal_count_today != 0:
                signal_count_today = 0  # сброс раз в сутки
                print("🔄 Новый день — счётчик сигналов обнулён")
            await asyncio.sleep(300)  # проверяем каждые 5 мин вне рабочего времени
            continue

        if not SIGNALS_ENABLED or signal_count_today >= SIGNAL_LIMIT_PER_DAY:
            await asyncio.sleep(300)
            continue

        for pair in PAIRS:
            try:
                df = await asyncio.to_thread(get_forex_ohlcv, pair)
                if df is None:
                    continue

                signal = check_strong_signal(df)
                print(f"[{pair}] анализ завершён → {signal}")

                if signal:
                    signal_count_today += 1

                    # предварительное сообщение
                    await bot.send_message(
                        ADMIN_ID, 
                        f"⏳ Готовится сигнал по `{pair}`...", 
                        parse_mode="Markdown"
                    )
                    await asyncio.sleep(3)

                    msg = (
                        f"📊 <b>Сигнал найден!</b>\n"
                        f"Пара: <code>{pair}</code>\n"
                        f"Направление: {signal}\n"
                        f"Таймфрейм: {TIMEFRAME}\n"
                        f"Стратегия: EMA / RSI / MACD\n"
                        f"🕒 {now.strftime('%H:%M:%S')}"
                    )
                    await bot.send_message(ADMIN_ID, msg, parse_mode="HTML")

                    if signal_count_today >= SIGNAL_LIMIT_PER_DAY:
                        await bot.send_message(ADMIN_ID, "✅ Дневной лимит сигналов достигнут")
                        break

            except Exception as e:
                print(f"⚠️ Ошибка при анализе {pair}: {e}")

        # Проверяем каждые 30 минут (можно поменять)
        await asyncio.sleep(1800)

# === Команды Telegram ===
@dp.message(Command("start"))
async def start(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer("❌ Подпишись на канал, чтобы получать сигналы!")
        return

    await message.answer(
        "👋 Привет! Я бот форекс-сигналов.\n\n"
        "Команды:\n"
        "/on — включить сигналы\n"
        "/off — выключить сигналы\n"
        "/status — проверить статус\n"
        "/reset — сбросить счётчик сигналов"
    )

@dp.message(Command("on"))
async def on_cmd(message: Message):
    global SIGNALS_ENABLED
    SIGNALS_ENABLED = True
    await message.answer("✅ Сигналы включены")

@dp.message(Command("off"))
async def off_cmd(message: Message):
    global SIGNALS_ENABLED
    SIGNALS_ENABLED = False
    await message.answer("⛔ Сигналы выключены")

@dp.message(Command("status"))
async def status_cmd(message: Message):
    status = "🟢 ВКЛЮЧЕНЫ" if SIGNALS_ENABLED else "🔴 ВЫКЛЮЧЕНЫ"
    await message.answer(
        f"Статус: {status}\n"
        f"Отправлено сигналов сегодня: {signal_count_today}/{SIGNAL_LIMIT_PER_DAY}"
    )

@dp.message(Command("reset"))
async def reset_cmd(message: Message):
    global signal_count_today
    signal_count_today = 0
    await message.answer("🔄 Счётчик сигналов сброшен")

# === Запуск ===
async def main():
    print("🚀 Бот запущен!")
    print(f"Админ ID: {ADMIN_ID}")
    asyncio.create_task(signal_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
