import logging
import psycopg2
import random
import string
import os
from datetime import datetime
from flask import Flask, request
from telegram.error import BadRequest, Unauthorized, TimedOut
from telegram import (
    Update,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# --- Flask App ---
app = Flask(__name__)  # Added Flask initialization

# --- Configuration ---
TOKEN = os.environ.get('TOKEN', '8119390210:AAFjN2YTSaPEyae9N9otMZ6kaNoo4-gns18')
WEB_APP_URL = os.environ.get('WEB_APP_URL', 'https://bingo-webapp.vercel.app')
ADMIN_IDS = [int(x) for x in os.environ.get('ADMIN_IDS', '5380773431').split(',')]
DATABASE_URL = os.environ.get('DATABASE_URL')
BACK_BUTTON_TEXT = "🔙 Back"

# Initialize logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Functions ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_db_connection() as conn:  # Fixed: get_db.connection() → get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,  -- Changed: INTEGER → BIGINT
                phone TEXT,
                username TEXT UNIQUE,
                name TEXT,
                wallet INTEGER DEFAULT 10,  -- Changed: balance → wallet
                score INTEGER DEFAULT 0,
                referral_code TEXT UNIQUE,
                referred_by TEXT,
                registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                role TEXT DEFAULT 'user',  -- Added
                invalid_bingo_count INTEGER DEFAULT 0  -- Added
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id TEXT PRIMARY KEY,
                user_id BIGINT,  -- Changed: INTEGER → BIGINT
                amount INTEGER,
                method TEXT,
                status TEXT DEFAULT 'pending',
                verification_code TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                referral_id SERIAL PRIMARY KEY,  -- Changed: AUTOINCREMENT → SERIAL
                referrer_id BIGINT,  -- Changed: INTEGER → BIGINT
                referee_id BIGINT,  -- Changed: INTEGER → BIGINT
                bonus_credited BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                creator_id BIGINT,  -- Changed: INTEGER → BIGINT
                players TEXT DEFAULT '',
                selected_numbers TEXT DEFAULT '',
                status TEXT DEFAULT 'waiting',
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                numbers_called TEXT DEFAULT '',
                winner_id BIGINT,  -- Changed: INTEGER → BIGINT
                prize_amount INTEGER DEFAULT 0,  -- Added
                bet_amount INTEGER DEFAULT 0,  -- Added
                countdown_start TIMESTAMP  -- Added
            )
        ''')
        conn.commit()

def get_db():
    return psycopg2.connect(DATABASE_URL)

def generate_referral_code(user_id):
    return f"BINGO{user_id}{random.randint(1000, 9999)}"

def generate_tx_id(user_id):
    return f"TX{user_id}{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

# --- Menu Functions ---
def main_menu_keyboard(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))  # Changed: ? → %s
        registered = cursor.fetchone() is not None
    keyboard = [
        [InlineKeyboardButton("🎮 Launch Game", web_app=WebAppInfo(url=f"{WEB_APP_URL}?user_id={user_id}"))] if registered else [],
        [InlineKeyboardButton("💰 Check Balance", callback_data='check_balance')] if registered else [],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard')] if registered else [],
        [InlineKeyboardButton("💳 Deposit", callback_data='deposit')] if registered else [],
        [InlineKeyboardButton("📖 Instructions", callback_data='instructions')],
        [InlineKeyboardButton("👥 Invite Friends", callback_data='invite')] if registered else [],
        [InlineKeyboardButton("🛟 Contact Support", callback_data='support')]
    ]
    if not registered:
        keyboard.insert(0, [InlineKeyboardButton("📝 Register", callback_data='register')])
    return InlineKeyboardMarkup([row for row in keyboard if row])

# --- Core Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0].startswith('ref_'):
        referrer_id = int(context.args[0][4:])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO referrals (referrer_id, referee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",  # Changed: ? → %s, added ON CONFLICT
                (referrer_id, user.id)
            )
            conn.commit()
    try:
        await update.message.reply_photo(
            photo=InputFile('bingo_welcome.png'),
            caption="🎉 Welcome to ዜቢ ቢንጎ! 🎉\n💰 Win prizes\n🎱 Play with friends via Web App!",
            reply_markup=main_menu_keyboard(user.id)
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "🎉 Welcome to ዜቢ ቢንጎ! 🎉",
            reply_markup=main_menu_keyboard(user.id)
        )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "ለመቀጠል ስልክ ቁጥሮን ያጋሩ!",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("📲 Share Contact", request_contact=True)]
        ], resize_keyboard=True, one_time_keyboard=True)
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_id, phone, name, referral_code) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",  # Changed: ? → %s, added ON CONFLICT
            (user.id, contact.phone_number, contact.first_name or user.username, generate_referral_code(user.id))
        )
        conn.commit()
    await update.message.reply_text(
        "🎉 Registration successful! 100 BNG credited.",
        reply_markup=main_menu_keyboard(user.id)
    )

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text="""
📋 **የዜቢ ቢንጎ መመሪያዎች**

🔹 **የመጀመሪያ ደረጃ:**
1. ለመጫወት ወደቦቱ ሲገቡ register የሚለውን በመንካት ስልክ ቁጥሮትን ያጋሩ
2. menu ውስጥ በመግባት deposit fund የሚለውን በመንካት በሚፈልጉት የባንክ አካውንት ገንዘብ ገቢ ያድርጉ
3. menu ውስጥ በመግባት ወደ Web App ይግቡ እና መወራረድ የሚፈልጉበትን የብር መጠን ይምረጡ

🎮 **የጨዋታ ሂደት (10x10 ካርድ):**
1. ወደጨዋታው እድገቡ ከሚመጣሎት 100 የመጫወቻ ቁጥሮች መርጠው accept የሚለውን በመንካት ይይቀጥሉ
2. ጨዋታው ለመጀመር የተሰጠውን ጊዜ ሲያልቅ ቁጥሮች መውጣት ይጀምራል
3. የሚወጡት ቁጥሮች የመረጡት ካርቴላ ላይ መኖሩን እያረጋገጡ ያቅልሙ
4. አንድ መስመር፣ አራት ጠርዞች፣ ወይም ሙሉ ቤት ሲመጣ ቢንጎ በማለት ማሸነፍ ይችላሉ
   - አንድ መስመር ማለት:
     * አንድ ወደጎን ወይንም 
     * ወደታች ወይንም 
     * ዲያጎናል ሲዘጉ
   - አራት ጠርዝ ሲመጣሎት
5. እነዚህ ማሸነፊያ ቁጥሮች ሳይመጣሎት bingo እሚለውን ከነኩ ከጨዋታው ይባረራሉ

⚠️ **ማሳሰቢያዎች:**
1. ጨዋታ ማስጀመሪያ ሰከንድ (countdown) ሲያልቅ ተጫዋች ብዛት ከ2 በታች ከሆነ አይጀምርም
2. ጨዋታ ከጀመረ በኋላ ካርቴላ መምረጫ ቦርዱ ይፀዳል
3. እርሶ በዘጉበት ቁጥር ሌላ ተጫዋች ዘግቶ ቀድሞ bingo ካለ አሸናፊነትዋን ያጣሉ

💰 **የሽልማት ስርዓት:**
- ከአጠቃላይ የሽልማት ገንዘብ (ከየአንዳንዱ ጨዋታ): 2 ፐርሰንት ለቤቱ ገቢ ተደርጎ ቀሪው ለአሸናፊው ይላካል

📝 ወደ ምርጡ ጨዋታ ይግቡ!
""",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Launch Game", web_app=WebAppInfo(url=f"{WEB_APP_URL}?user_id={update.callback_query.from_user.id}"))],
            [InlineKeyboardButton("💰 Deposit", callback_data='deposit')],
            [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
        ]),
        parse_mode='Markdown'
    )

async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bot_username = context.bot.username or "ZebiBingoBot"
    invite_text = f"""
👥 Invite Friends & Earn!
Invite friends to earn 10 ETB per registration!
Your referral link:
👉 https://t.me/{bot_username}?start=ref_{query.from_user.id} 👈

📢 Share this copiable link with friends!
"""
    await query.edit_message_text(
        text=invite_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )

async def contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🛟 Contact Support\n\nFor help, contact @ZebiSupportBot\nAvailable 24/7!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet FROM users WHERE user_id = %s", (user_id,))  # Changed: balance → wallet, ? → %s
        balance = cursor.fetchone()
    await query.edit_message_text(
        f"💰 Your balance: {balance[0]} ETB" if balance else "❌ Not registered.",  # Changed: BNG → ETB
        reply_markup=main_menu_keyboard(user_id)
    )

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, name, score FROM users ORDER BY score DESC LIMIT 10")
        leaderboard = cursor.fetchall()
    leaderboard_text = "🏆 Top 10 Players:\n"
    for i, (user_id, name, score) in enumerate(leaderboard, 1):
        leaderboard_text += f"{i}. {name} - {score} points\n"
    await query.edit_message_text(
        text=leaderboard_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )

# --- Payment Handlers ---
async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(
        "💰 Enter deposit amount in Birr:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )
    context.user_data['awaiting_deposit'] = True

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_deposit' not in context.user_data:
        return
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
        context.user_data['deposit_amount'] = amount
        await show_payment_options(update, context)
    except ValueError:
        await update.message.reply_text("Invalid amount.")
    finally:
        context.user_data.pop('awaiting_deposit', None)

async def show_payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = context.user_data['deposit_amount']
    await update.message.reply_text(
        f"Select payment method for {amount} Birr:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Telebirr", callback_data="payment_telebirr")],
            [InlineKeyboardButton("CBE", callback_data="payment_cbe")],
            [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
        ])
    )

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.split('_')[1]
    amount = context.user_data['deposit_amount']
    tx_id = generate_tx_id(query.from_user.id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (tx_id, user_id, amount, method, verification_code) VALUES (%s, %s, %s, %s, %s)",  # Changed: ? → %s
            (tx_id, query.from_user.id, amount, method, tx_id[-6:])
        )
        conn.commit()

    if method == 'telebirr':
        payment_details = f"""
📋 **Telebirr Payment Details (Copy This):**

Name: {query.from_user.first_name}
Amount: {amount} Birr
Reference: {tx_id[-6:]}

📌 **Account to Send To:**
- Telebirr Account: +251944156222
- Name: ናትናኤል ዳንኤል

📝 **Instructions:**
1. Open the Telebirr App
2. Select 'Send Money'
3. Enter the account number: +251944156222
4. Enter the exact amount: {amount} Birr
5. Use the reference code: {tx_id[-6:]} in the note
6. Complete the transaction
7. Send the transaction confirmation code here
"""
    else:  # CBE
        payment_details = f"""
📋 **CBE Payment Details (Copy This):**

Name: {query.from_user.first_name}
Amount: {amount} Birr
Reference: {tx_id[-6:]}

📌 **Account to Send To:**
- CBE Account: 1000340957688
- Name: ናትናኤል ዳንኤል

📝 **Detailed Instructions:**
1. ከላይ ባለው የኢትዮጵያ ንግድ ባንክ አካውንት {amount}ብር ያስገቡ 
2. የምትልኩት የገንዘብ መጠን እና እዚ ላይ እንዲሞላልዎ የምታስገቡት የብር መጠን ተመሳሳይ መሆኑን እርግጠኛ ይሁን 
3. ብሩን ስትልኩ የከፈላችሁበትን መረጃ የያዝ አጭር የጹሁፍ መልክት(sms) ከኢትዮጵያ ንግድ ባንክ ይደርሳችኋል
4. የደረሳችሁን አጭር የጹሁፍ መለክት(sms) ሙሉዉን ኮፒ(copy) በማረግ ከታሽ ባለው የቴሌግራም የጹሁፍ ማስገቢአው ላይ ፔስት(paste) በማረግ ይላኩት
5. ብር ስትልኩ የምትጠቀሙት USSD(889) ከሆነ አንዳንዴ አጭር የጹሁፍ መለክት(sms) ላይገባላቹ ስለሚችል ከUSSD(889) ሂደት መጨረሻ ላይ Complete የሚለው ላይ ስደርሱ 3 ቁጥርን በመጫን የትራንዛክሽን ቁጥሩን ሲያሳያቹህ ትራንዛክሽን ቁጥሩን ጽፎ ማስቀመጥ ይኖርባችኋል 

ማሳሰቢያ 📢፡ 
1. አጭር የጹሁፍ መለክት(sms) ካልደረሳቹ ያለትራንዛክሽን ቁጥር ሲስተሙ ዋሌት ስለማይሞላላቹ የከፈላችሁበትን ደረሰኝ ከባንክ በመቀበል በማንኛውም ሰአት ትራንዛክሽን ቁጥሩን ቦቱ ላይ ማስገባት ትችላላቹ 
2. ዲፖዚት ባረጋቹ ቁጥር ቦቱ የሚያገናኛቹ ኤጀንቶች ስለሚለያዩ ከላይ ወደሚሰጣቹ የኢትዮጵያ ንግድ ባንክ አካውንት ብቻ ብር መላካችሁን እርግጠኛ ይሁኑ።
"""
    await query.edit_message_text(
        text=payment_details,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )

# --- Admin Commands ---
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        "🛠 Admin Panel",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Set Game Times", callback_data="admin_set_times")],
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("✅ Verify Payments", callback_data="admin_verify")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
        ])
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or update.effective_user.id not in ADMIN_IDS:
        return
    await query.answer()
    action = query.data.split('_')[1]
    if action == "set_times":
        await query.edit_message_text("📅 Enter start and end times (YYYY-MM-DD HH:MM) separated by space:")
        context.user_data['awaiting_times'] = True
    elif action == "stats":
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            users = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(amount) FROM transactions WHERE status = 'verified'")
            total_deposits = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM transactions WHERE status = 'pending'")
            pending = cursor.fetchone()[0]
        await query.edit_message_text(f"📊 Stats: Users: {users}, Deposits: {total_deposits} ETB, Pending: {pending}")
    elif action == "verify":
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tx_id, user_id, amount, verification_code FROM transactions WHERE status = 'pending'")
            pending_txs = cursor.fetchall()
        if not pending_txs:
            await query.edit_message_text("✅ No pending transactions.")
            return
        keyboard = [[InlineKeyboardButton(f"User {tx[1]} - {tx[2]} ETB ({tx[3]})", callback_data=f"verify_{tx[0]}")] for tx in pending_txs]
        keyboard.append([InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')])
        await query.edit_message_text("✅ Verify Payments:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "broadcast":
        await query.edit_message_text("📢 Enter broadcast message:")
        context.user_data['awaiting_broadcast'] = True

async def admin_set_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_times' not in context.user_data:
        return
    try:
        start_time, end_time = update.message.text.split()
        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M')
        end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M')
        if end_time <= start_time:
            raise ValueError
        game_id = f"MP{int(datetime.now().timestamp())}"
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO games (game_id, creator_id, status, start_time, end_time) VALUES (%s, %s, %s, %s, %s)",  # Changed: ? → %s
                (game_id, update.effective_user.id, 'scheduled', start_time, end_time)
            )
            conn.commit()
        await update.message.reply_text(f"✅ Game {game_id} scheduled from {start_time} to {end_time}.")
    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        context.user_data.pop('awaiting_times', None)

async def admin_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_times' in context.user_data or not update.callback_query:
        return
    query = update.callback_query
    await query.answer()
    tx_id = query.data.split('_')[1]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount FROM transactions WHERE tx_id = %s AND status = 'pending'", (tx_id,))  # Changed: ? → %s
        tx = cursor.fetchone()
        if tx:
            user_id, amount = tx
            cursor.execute("UPDATE transactions SET status = 'verified' WHERE tx_id = %s", (tx_id,))  # Changed: ? → %s
            cursor.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (amount, user_id))  # Changed: balance → wallet, ? → %s
            cursor.execute("SELECT referrer_id FROM referrals WHERE referee_id = %s AND NOT bonus_credited", (user_id,))  # Changed: ? → %s
            referrer = cursor.fetchone()
            if referrer:
                cursor.execute("UPDATE users SET wallet = wallet + 10 WHERE user_id = %s", (referrer[0],))  # Changed: 20 → 10, balance → wallet, ? → %s
                cursor.execute("UPDATE referrals SET bonus_credited = TRUE WHERE referee_id = %s", (user_id,))  # Changed: ? → %s
            conn.commit()
            await query.edit_message_text(f"✅ Verified {amount} ETB for User {user_id}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_verify')]]))
        else:
            await query.edit_message_text("❌ Invalid transaction.")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_broadcast' not in context.user_data:
        return
    message = update.message.text
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        for user_id, in cursor.fetchall():
            try:
                await context.bot.send_message(chat_id=user_id, text=f"📢 {message}")
            except (BadRequest, Unauthorized, TimedOut) as e:
                logger.warning(f"Failed to send broadcast to user {user_id}: {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error sending broadcast to user {user_id}: {str(e)}")
                raise
    await update.message.reply_text("📢 Broadcast sent!")
    context.user_data.pop('awaiting_broadcast', None)

# --- Webhook Handler ---
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return "OK", 200

# --- Set Webhook ---
@app.route("/setwebhook", methods=["GET"])
def set_webhook():
    webhook_url = f"https://{os.getenv('VERCEL_URL', 'zebi-bingo-bot.vercel.app')}/{TOKEN}"  # Updated: Default domain
    success = application.bot.set_webhook(webhook_url)
    return "Webhook set" if success else "Failed to set webhook"

# --- Initialize Handlers ---
def setup_handlers():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CallbackQueryHandler(register, pattern="^register$"))
    application.add_handler(CallbackQueryHandler(instructions, pattern="^instructions$"))
    application.add_handler(CallbackQueryHandler(check_balance, pattern="^check_balance$"))
    application.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard$"))
    application.add_handler(CallbackQueryHandler(invite_friends, pattern="^invite$"))
    application.add_handler(CallbackQueryHandler(contact_support, pattern="^support$"))
    application.add_handler(CallbackQueryHandler(deposit, pattern="^deposit$"))
    application.add_handler(CallbackQueryHandler(handle_payment_method, pattern="^payment_"))
    application.add_handler(CallbackQueryHandler(admin_handler, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(admin_verify, pattern="^verify_"))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+(?:\.\d+)?$'), process_deposit_amount))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_times))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast))

# --- Main Application ---
application = ApplicationBuilder().token(TOKEN).build()  # Added: Define application globally

if __name__ == "__main__":
    init_db()
    setup_handlers()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))