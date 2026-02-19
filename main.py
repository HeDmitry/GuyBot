import asyncio
import logging
import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatAction
from duckduckgo_search import DDGS
from openai import AsyncOpenAI

# --- ТОКЕНЫ (ВНИМАНИЕ: Смени их, они теперь публичны!) ---
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
def search_web(query):
    try:
        logging.info(f"Searching: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="ru-ru", max_results=2))
            if results:
                return "\n".join([f"- {r['body']}" for r in results])
            return None
    except Exception as e:
        logging.error(f"Search error: {e}")
        return None

def extract_search_query(text):
    text_lower = text.lower()
    for trigger in SEARCH_TRIGGERS:
        if text_lower.startswith(trigger):
            return text[len(trigger):].strip()
    return None

def clean_bot_reply(text):
    text = re.sub(r'^(Гай|Guy|Бот|AI|Assistant):\s*', '', text, flags=re.IGNORECASE)
    return text.strip()

def update_history(chat_id, role, name, content):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    api_role = "user" if role == "user" else "assistant"
    # Для пользователя добавляем имя в контент, чтобы бот понимал, кто пишет
    formatted_msg = f"{name}: {content}" if role == "user" else content
    
    chat_histories[chat_id].append({"role": api_role, "content": formatted_msg})
    
    if len(chat_histories[chat_id]) > MAX_CONTEXT_DEPTH:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_CONTEXT_DEPTH:]

def check_for_rudeness(text, chat_id):
    if any(word in text.lower() for word in RUDE_KEYWORDS):
        grudge_state[chat_id] = GRUDGE_DURATION
        return True
    return False

@dp.message()
async def handle_message(message: types.Message):
    global BOT_ID
    if not message.text or message.via_bot:
        return

    if BOT_ID is None:
        me = await bot.get_me()
        BOT_ID = me.id

    chat_id = message.chat.id
    text = message.text
    user_name = message.from_user.first_name

    # 1. Проверка триггеров (чтобы не отвечать на всё подряд)
    is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
    is_triggered = any(name in text.lower() for name in BOT_TRIGGER_NAMES)
    
    # Сначала обновляем историю для контекста
    update_history(chat_id, "user", user_name, text)

    if not (is_triggered or is_reply):
        return

    await bot.send_chat_action(chat_id, action=ChatAction.TYPING)

    # Проверка на хамство
    check_for_rudeness(text, chat_id)
    current_grudge = grudge_state.get(chat_id, 0)
    
    if any(w in text.lower() for w in ["извини", "прости", "sorry"]):
        grudge_state[chat_id] = 0
        current_grudge = 0

    # Поиск в сети
    web_context = ""
    search_query = extract_search_query(text)
    if search_query and current_grudge == 0:
        res = await asyncio.to_thread(search_web, search_query)
        if res:
            web_context = f"\nАКТУАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА:\n{res}\nИспользуй это для ответа."

    # Сборка системного промпта
    server_tz = ZoneInfo("Asia/Vladivostok") # Поменял на Москву для логики поиска
    now = datetime.now(server_tz).strftime("%H:%M")

    full_system = SYSTEM_PROMPT_BASE + f"\nТекущее время: {now}."
    if current_grudge > 0:
        full_system += SYSTEM_PROMPT_ANGRY
        grudge_state[chat_id] -= 1
    if web_context:
        full_system += web_context

    # Формируем пакет сообщений
    messages_payload = [{"role": "system", "content": full_system}]
    messages_payload.extend(chat_histories[chat_id]) # Берем всю историю, включая последнее сообщение

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_payload,
            temperature=0.8,
            max_tokens=500
        )

        answer = clean_bot_reply(response.choices[0].message.content)

        if answer:
            update_history(chat_id, "assistant", "Гай", answer)
            try:
                await message.reply(answer, parse_mode="Markdown")
            except Exception as markdown_error:
                # Если упало из-за разметки, шлем просто текст
                await message.reply(answer)

    except Exception as e:
        logging.error(f"API Error: {e}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())