import os
import re
import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import plotly.express as px
import pandas as pd
import telebot
from supabase import create_client
from config import CATEGORIES
from dotenv import load_dotenv

# Загружаем переменные из .env файла (локально), на Render они уже в окружении
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(BOT_TOKEN)

# ---------- HTTP Health Check (для Render) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f'Health server running on port {port}')
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()
# --------------------------------------------------------

# ---------- Работа с категориями ----------
def extract_amount_and_category(text):
    amounts = re.findall(r'\d+[.,]?\d*', text)
    if not amounts:
        return None, None
    amount = float(amounts[-1].replace(',', '.'))
    lower_text = text.lower()
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in lower_text:
                return amount, category
    return amount, "other"

# ---------- Работа с базой данных ----------
def save_expense(user_id, username, amount, category, date_str=None):
    if date_str is None:
        date_str = datetime.date.today().isoformat()
    data = {
        "user_id": str(user_id),
        "username": username,
        "amount": amount,
        "category": category,
        "date": date_str
    }
    supabase.table("expenses").insert(data).execute()

def get_expenses_for_period(start_date, end_date):
    response = supabase.table("expenses") \
        .select("*") \
        .gte("date", start_date) \
        .lte("date", end_date) \
        .execute()
    return response.data

def delete_last_expense(user_id):
    """Удаляет самую последнюю запись, сделанную пользователем user_id."""
    # Получаем последнюю запись этого пользователя (по дате и id)
    resp = supabase.table("expenses") \
        .select("id") \
        .eq("user_id", str(user_id)) \
        .order("date", desc=True) \
        .order("id", desc=True) \
        .limit(1) \
        .execute()
    data = resp.data
    if not data:
        return False
    record_id = data[0]["id"]
    supabase.table("expenses").delete().eq("id", record_id).execute()
    return True

# ---------- Графики ----------
def generate_pie_chart(expenses, title):
    if not expenses:
        return None
    df = pd.DataFrame(expenses)
    df_grouped = df.groupby("category")["amount"].sum().reset_index()
    fig = px.pie(df_grouped, values='amount', names='category',
                 title=title,
                 color_discrete_sequence=px.colors.qualitative.Pastel)
    fig.update_traces(textposition='inside', textinfo='percent+label')
    path = "/tmp/expenses_pie.png"
    fig.write_image(path)
    return path

# ---------- Команды бота ----------
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Привет! Я бот семейного бюджета.\n"
                          "Просто напишите трату, например: 'Такси 300' или 'Купила продукты 600'.\n"
                          "/stats неделя – расходы за неделю\n"
                          "/stats месяц – за месяц\n"
                          "/stats квартал – за квартал\n"
                          "/stats ДД.ММ.ГГГГ ДД.ММ.ГГГГ – произвольный период\n"
                          "/undo – удалить последнюю ошибочную трату")

@bot.message_handler(commands=['undo'])
def undo_last(message):
    user_id = message.from_user.id
    success = delete_last_expense(user_id)
    if success:
        bot.reply_to(message, "🔙 Последняя ваша запись удалена.")
    else:
        bot.reply_to(message, "🤷 Нет записей для удаления.")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    args = message.text.split()[1:]
    today = datetime.date.today()

    if not args:
        bot.reply_to(message, "Укажите период: неделя, месяц, квартал или две даты (ДД.ММ.ГГГГ ДД.ММ.ГГГГ)")
        return

    if len(args) == 1:
        period = args[0].lower()
        if period == "неделя":
            start = today - datetime.timedelta(days=7)
            end = today
            title = "Расходы за неделю"
        elif period == "месяц":
            start = today.replace(day=1)
            end = today
            title = "Расходы за месяц"
        elif period == "квартал":
            quarter_month = (today.month - 1) // 3 * 3 + 1
            start = today.replace(month=quarter_month, day=1)
            end = today
            title = "Расходы за квартал"
        else:
            bot.reply_to(message, "Непонятный период. Используйте: неделя, месяц, квартал")
            return
    elif len(args) == 2:
        try:
            start = datetime.datetime.strptime(args[0], "%d.%m.%Y").date()
            end = datetime.datetime.strptime(args[1], "%d.%m.%Y").date()
            title = f"Расходы с {args[0]} по {args[1]}"
        except:
            bot.reply_to(message, "Неверный формат даты. Используйте ДД.ММ.ГГГГ")
            return
    else:
        bot.reply_to(message, "Слишком много аргументов.")
        return

    expenses = get_expenses_for_period(start.isoformat(), end.isoformat())
    if not expenses:
        bot.reply_to(message, "За этот период нет расходов.")
        return

    chart_path = generate_pie_chart(expenses, title)
    if chart_path:
        with open(chart_path, 'rb') as photo:
            bot.send_photo(message.chat.id, photo)
    else:
        bot.reply_to(message, "Не удалось построить график.")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        return

    amount, category = extract_amount_and_category(message.text)
    if amount is None:
        bot.reply_to(message, "Не поняла сумму. Напиши, например: 'Такси 300' или 'Купила продукты 600'.")
        return

    username = message.from_user.username or message.from_user.first_name
    save_expense(message.from_user.id, username, amount, category)
    reply = f"✅ Записано: {amount} руб. в категорию «{category}» ({username})"
    bot.reply_to(message, reply)

# ---------- Запуск ----------
bot.polling(none_stop=True)
