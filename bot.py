import os
import re
import datetime
import plotly.express as px
import pandas as pd
import telebot
from supabase import create_client
from config import CATEGORIES

# Подключаемся к Supabase и боту
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(BOT_TOKEN)

def extract_amount_and_category(text):
    """Извлекает сумму и определяет категорию по ключевым словам."""
    # Ищем все числа в тексте (в том числе с точкой и запятой)
    amounts = re.findall(r'\d+[.,]?\d*', text)
    if not amounts:
        return None, None
    # Берём последнее число как сумму (обычно её пишут в конце)
    amount = float(amounts[-1].replace(',', '.'))
    
    lower_text = text.lower()
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in lower_text:
                return amount, category
    return amount, "other"

def save_expense(user_id, username, amount, category, date_str=None):
    """Сохраняет расход в Supabase."""
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
    """Получает расходы из базы за заданный период."""
    response = supabase.table("expenses") \
        .select("*") \
        .gte("date", start_date) \
        .lte("date", end_date) \
        .execute()
    return response.data

def generate_pie_chart(expenses, title):
    """Строит круговую диаграмму и возвращает путь к PNG."""
    if not expenses:
        return None
    df = pd.DataFrame(expenses)
    # Группируем по категориям и суммируем
    df_grouped = df.groupby("category")["amount"].sum().reset_index()
    fig = px.pie(df_grouped, values='amount', names='category',
                 title=title,
                 color_discrete_sequence=px.colors.qualitative.Pastel)
    fig.update_traces(textposition='inside', textinfo='percent+label')
    path = "/tmp/expenses_pie.png"
    fig.write_image(path)
    return path

# Обработчик всех текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    # Игнорируем команды, обработаем отдельно
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

# Команда /stats
@bot.message_handler(commands=['stats'])
def stats_command(message):
    # Парсим аргументы: /stats неделя, месяц, квартал или /stats 01.05.2026 12.05.2026
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
            # Квартал: текущий квартал
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

# Запуск
bot.polling(none_stop=True)