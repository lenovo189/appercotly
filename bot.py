import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from supabase import create_client, Client
import google.generativeai as genai

# Load env vars
possible_paths = [
    os.path.join(os.getcwd(), ".env.local"),
    os.path.join(os.getcwd(), "..", ".env.local"),
    os.path.join(os.path.dirname(__file__), "..", ".env.local")
]

for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        break

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-2.5-flash-lite')

# Localization
BOT_STRINGS = {
    'en': {
        'welcome': "Welcome to BloomGuard! 🌿\n\nPlease link your account to get started. Generate a 6-digit code in your Dashboard on the website and send it here.",
        'welcome_back': "Welcome back, {name}! 🌱\nUse /projects to see your plant hydration projects.",
        'invalid_code': "Invalid or expired code. Please check your dashboard and try again.",
        'linked': "Success! Your account is now linked, {name}. 🎉\nUse /projects to get started.",
        'no_projects': "You don't have any approved projects yet. Create one on the website!",
        'choose_project': "Choose a project to chat with BloomGuard AI:",
        'entering_chat': "Entering chat for '{name}'. 🤖\nType your message to chat with Gemini. Use /exit to switch projects.",
        'unknown_error': "I'm sorry, I'm having trouble thinking right now. 🌿",
        'lang_choice': "Please choose your language / Iltimos, tilni tanlang:",
        'help': "Commands:\n/start - Link account\n/projects - List projects\n/lang - Change language\n/exit - Exit chat",
        'not_linked': "Your account is not linked. Please use /start."
    },
    'uz': {
        'welcome': "BloomGuard-ga xush kelibsiz! 🌿\n\nBoshlash uchun hisobingizni bog'lang. Saytdagi Dashboard-dan 6 xonali kod oling va bu yerga yuboring.",
        'welcome_back': "Xush kelibsiz, {name}! 🌱\nLoyihalarni ko'rish uchun /projects buyrug'idan foydalaning.",
        'invalid_code': "Kod noto'g'ri yoki muddati o'tgan. Iltimos, saytdan yangi kod oling.",
        'linked': "Tabriklaymiz! Hisobingiz bog'landi, {name}. 🎉\nBoshlash uchun /projects buyrug'ini bosing.",
        'no_projects': "Sizda hali tasdiqlangan loyihalar yo'q. Saytda loyiha yarating!",
        'choose_project': "BloomGuard AI bilan bog'lanish uchun loyihani tanlang:",
        'entering_chat': "'{name}' loyihasi uchun chatga kirildi. 🤖\nSavolingizni yuboring. Loyihani almashtirish uchun /exit buyrug'ini bosing.",
        'unknown_error': "Kechirasiz, hozir javob bera olmayman. 🌿",
        'lang_choice': "Iltimos, tilni tanlang:",
        'help': "Buyruqlar:\n/start - Hisobni bog'lash\n/projects - Loyihalar ro'yxati\n/lang - Tilni o'zgartirish\n/exit - Chatdan chiqish",
        'not_linked': "Hisobingiz bog'lanmagan. Iltimos, /start qiling."
    }
}

# States
LINKING, SELECTING_PROJECT, CHATTING, SETTING_LANG = range(4)

def get_user_lang(user_id):
    res = supabase.table("profiles").select("language").eq("telegram_id", str(user_id)).execute()
    if res.data:
        return res.data[0].get('language', 'en')
    return 'en'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    res = supabase.table("profiles").select("*").eq("telegram_id", user_id).execute()
    
    if res.data:
        profile = res.data[0]
        lang = profile.get('language', 'en')
        await update.message.reply_text(BOT_STRINGS[lang]['welcome_back'].format(name=profile['full_name']))
        return SELECTING_PROJECT
    else:
        # Ask for language first?
        keyboard = [
            [InlineKeyboardButton("English 🇺🇸", callback_data='set_lang_en')],
            [InlineKeyboardButton("O'zbek 🇺🇿", callback_data='set_lang_uz')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(BOT_STRINGS['en']['lang_choice'], reply_markup=reply_markup)
        return SETTING_LANG

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = query.data.split('_')[-1]
    user_id = str(update.effective_user.id)
    
    # If they are already linked, update DB
    res = supabase.table("profiles").select("id").eq("telegram_id", user_id).execute()
    if res.data:
        supabase.table("profiles").update({"language": lang}).eq("telegram_id", user_id).execute()

    context.user_data['temp_lang'] = lang
    await query.edit_message_text(BOT_STRINGS[lang]['welcome'])
    return LINKING

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("English 🇺🇸", callback_data='set_lang_en')],
        [InlineKeyboardButton("O'zbek 🇺🇿", callback_data='set_lang_uz')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose language:", reply_markup=reply_markup)
    return SETTING_LANG

async def handle_link_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    user_id = str(update.effective_user.id)
    lang = context.user_data.get('temp_lang', 'en')

    if not code.isdigit() or len(code) != 6:
        await update.message.reply_text("Please enter 6 digits.")
        return LINKING

    res = supabase.table("profiles").select("*").eq("telegram_link_code", code).execute()
    if not res.data:
        await update.message.reply_text(BOT_STRINGS[lang]['invalid_code'])
        return LINKING

    profile = res.data[0]
    # Set the language they chose earlier during linking
    supabase.table("profiles").update({
        "telegram_id": user_id, 
        "telegram_link_code": None,
        "language": lang
    }).eq("id", profile['id']).execute()
    
    await update.message.reply_text(BOT_STRINGS[lang]['linked'].format(name=profile['full_name']))
    return SELECTING_PROJECT

async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang = get_user_lang(user_id)
    
    res_p = supabase.table("profiles").select("id").eq("telegram_id", user_id).execute()
    if not res_p.data:
        await update.message.reply_text(BOT_STRINGS[lang]['not_linked'])
        return LINKING
    
    owner_id = res_p.data[0]['id']
    res = supabase.table("projects").select("*").eq("owner_id", owner_id).eq("status", "approved").execute()
    
    if not res.data:
        await update.message.reply_text(BOT_STRINGS[lang]['no_projects'])
        return SELECTING_PROJECT

    projects = res.data
    keyboard = [[p['name']] for p in projects]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(BOT_STRINGS[lang]['choose_project'], reply_markup=reply_markup)
    return SELECTING_PROJECT 

async def handle_project_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    user_id = str(update.effective_user.id)
    lang = get_user_lang(user_id)
    
    res_p = supabase.table("profiles").select("id").eq("telegram_id", user_id).execute()
    if not res_p.data: return LINKING
    owner_id = res_p.data[0]['id']
    
    res = supabase.table("projects").select("*").eq("owner_id", owner_id).eq("name", choice).execute()
    if not res.data:
        return SELECTING_PROJECT

    project = res.data[0]
    context.user_data['active_project_id'] = project['id']
    context.user_data['project_name'] = project['name']
    context.user_data['project_desc'] = project['description']
    
    await update.message.reply_text(
        BOT_STRINGS[lang]['entering_chat'].format(name=project['name']),
        reply_markup=ReplyKeyboardRemove()
    )
    return CHATTING

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project_id = context.user_data.get('active_project_id')
    user_msg = update.message.text
    user_id = str(update.effective_user.id)
    lang = get_user_lang(user_id)
    
    res_p = supabase.table("profiles").select("id").eq("telegram_id", user_id).execute()
    if not res_p.data: return LINKING
    db_user_id = res_p.data[0]['id']

    supabase.table("messages").insert({
        "project_id": project_id, "user_id": db_user_id, "role": "user", "content": user_msg
    }).execute()

    lang_instruction = "IMPORTANT: Respond in English." if lang == 'en' else "IMPORTANT: O'zbek tilida javob bering."
    prompt = f"{lang_instruction}\nYou are BloomGuard AI. Project: {context.user_data['project_name']}. Description: {context.user_data['project_desc']}. User says: {user_msg}"
    
    try:
        response = model.generate_content(prompt)
        ai_resp = response.text
    except Exception as e:
        print(e)
        ai_resp = BOT_STRINGS[lang]['unknown_error']

    supabase.table("messages").insert({"project_id": project_id, "role": "assistant", "content": ai_resp}).execute()
    await update.message.reply_text(ai_resp)
    return CHATTING

app = FastAPI()

application = Application.builder().token(TELEGRAM_TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start), CommandHandler("projects", list_projects), CommandHandler("lang", lang_command)],
    states={
        SETTING_LANG: [CallbackQueryHandler(set_lang_callback, pattern='^set_lang_')],
        LINKING: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link_code)],
        SELECTING_PROJECT: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_project_choice)],
        CHATTING: [MessageHandler(filters.TEXT & (~filters.COMMAND), chat)],
    },
    fallbacks=[CommandHandler("exit", list_projects), CommandHandler("lang", lang_command)],
    allow_reentry=True
)

application.add_handler(conv_handler)

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()

@app.post("/")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
