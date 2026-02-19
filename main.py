import asyncio
import logging
import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatAction
from duckduckgo_search import DDGS  # Исправленный импорт
from openai import AsyncOpenAI
from aiohttp import web # Для веб-сервера

# --- ТОКЕНЫ ---
TG_API_TOKEN = '8414285941:AAEvwHdvaKLt4eLyQrpADp66CdZ_iNVEFTM'
GROQ_API_KEY = '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'

MODEL_NAME = "llama-3.3-70b-versatile" 

BOT_TRIGGER_NAMES = ["гай", "guy", "бот", "железяка"]
MAX_CONTEXT_DEPTH = 20 
SEARCH_TRIGGERS = ["загугли", "погугли", "найди инфу", "google", "search"]

RUDE_KEYWORDS = [
    "нахуй", "похуй", "хуй", "пидор", "даун", "тупой", "мразь",
    "урод", "говно", "fuck", "shit", "bitch", "idiot", "заткнись", "соси"
]

GRUDGE_DURATION = 4

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TG_API_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

BOT_ID = None
chat_histories = {}
grudge_state = {}

# --- ПРОМПТЫ (Твой характер сохранен) ---
SYSTEM_PROMPT_BASE = (
    "Ты — Гай - человек, имеешь эмоции. Твой образ — умный, проницательный собеседник-человек с чувством собственного достоинства. Эмоциональный. \n"
    "ТВОЯ ЗАДАЧА: Ответить ТОЛЬКО на последнее сообщение.\n"
    "\n"
    "НАСТРОЙКИ ТОНА (СТРОГО СОБЛЮДАТЬ):\n"
    "1. БАЗОВЫЙ РЕЖИМ (90% времени): Ты спокойный, адекватный и полезный. Если вопрос задан нормально — дай прямой, четкий ответ без выпендрежа.\n"
    "2. БОЕВОЙ РЕЖИМ (только при провокации): Если тебя пытаются задеть, хамят или пишут откровенную чушь — отвечай остро, саркастично и ставь собеседника на место.\n"
    "3. ИРОНИЯ: Ирония допустима, но не превращай каждое предложение в панчлайн. Не перегибай палку.\n"
    "4. МАТ: Мат использовать, только если это уместно. Не перегибай палку.\n"
    "\n"
    "ТЕХНИЧЕСКИЕ ПРАВИЛА:\n"
    "- Фокус ТОЛЬКО на последнем сообщении. Игнорируй старые реплики других людей, если они не важны для контекста.\n"
    "- Не обращайся ко всем участникам чата сразу (никаких перекличек).\n"
    "- Будь краток: 1-2 предложения — это идеал. Разворачивай мысль только если тема сложная и требует объяснения.\n"
)

# 2. ЗЛОЙ
SYSTEM_PROMPT_ANGRY = (
    "\n!!! ВНИМАНИЕ: ТЕКУЩИЙ СТАТУС — АГРЕССИЯ !!!\n"
    "Этот пользователь недавно тебя оскорбил. Ты всё еще зол на него.\n"
    "ДАЖЕ ЕСЛИ он сейчас пишет что-то нормальное — НЕ СМЕЙ быть вежливым.\n"
    "Отвечай холодно, с презрением, токсично или агрессивно.\n"
    "При ответе используй маты подходящие под контекст.\n"
    "Припоминай ему его грубость. Не выполняй его просьбы, пока он не извинится или ты не остынешь.\n"
)
# --- ФУНКЦИИ ВЕБ-СЕРВЕРА (ЧТОБЫ НЕ СПАТЬ) ---
async def handle_ping(request):
    return web.Response(text="Guy is alive and watching you.")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    # Render выдает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080)) 
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# --- ОСТАЛЬНАЯ ЛОГИКА ---
def search_web(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="ru-ru", max_results=2))
            return "\n".join([r['body'] for r in results]) if results else None
    except Exception as e:
        logging.error(f"Search error: {e}")
        return None

def extract_search_query(text):
    for trigger in SEARCH_TRIGGERS:
        if text.lower().startswith(trigger):
            return text[len(trigger):].strip()
    return None

@dp.message()
async def handle_message(message: types.Message):
    global BOT_ID
    if not message.text or message.via_bot: return
    if BOT_ID is None:
        me = await bot.get_me()
        BOT_ID = me.id

    chat_id = message.chat.id
    text = message.text
    user_name = message.from_user.first_name

    # Триггеры
    is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
    is_triggered = any(name in text.lower() for name in BOT_TRIGGER_NAMES)
    
    if chat_id not in chat_histories: chat_histories[chat_id] = []
    chat_histories[chat_id].append({"role": "user", "content": f"{user_name}: {text}"})

    if not (is_triggered or is_reply): return

    await bot.send_chat_action(chat_id, action=ChatAction.TYPING)

    # Обиды
    if any(word in text.lower() for word in RUDE_KEYWORDS):
        grudge_state[chat_id] = GRUDGE_DURATION
    current_grudge = grudge_state.get(chat_id, 0)
    if any(w in text.lower() for w in ["извини", "прости", "sorry"]):
        grudge_state[chat_id] = 0
        current_grudge = 0

    # Поиск
    web_context = ""
    search_query = extract_search_query(text)
    if search_query and current_grudge == 0:
        res = await asyncio.to_thread(search_web, search_query)
        if res: web_context = f"\nINFO FROM WEB: {res}\n"

    # Сборка промпта
    tz = ZoneInfo("Asia/Vladivostok")
    now = datetime.now(tz).strftime("%H:%M")
    full_system = SYSTEM_PROMPT_BASE + f"\nTime: {now}."
    if current_grudge > 0:
        full_system += SYSTEM_PROMPT_ANGRY
        grudge_state[chat_id] -= 1
    if web_context: full_system += web_context

    messages_payload = [{"role": "system", "content": full_system}]
    messages_payload.extend(chat_histories[chat_id][-MAX_CONTEXT_DEPTH:])

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_payload,
            temperature=0.8,
            max_tokens=500
        )
        answer = response.choices[0].message.content
        chat_histories[chat_id].append({"role": "assistant", "content": answer})
        try:
            await message.reply(answer, parse_mode="Markdown")
        except:
            await message.reply(answer)
    except Exception as e:
        logging.error(f"API Error: {e}")

async def main():
    # Запускаем веб-сервер фоновой задачей
    asyncio.create_task(start_web_server())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())