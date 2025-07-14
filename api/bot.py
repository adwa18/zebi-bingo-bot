import os
import logging
import random
import string
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
import psycopg2
from psycopg2 import pool

from telegram import (
    Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, Application
)
from telegram.error import BadRequest, Forbidden, TimedOut

# --- Configuration ---
TOKEN = os.environ.get("TOKEN")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "")  # Only for referral links, etc.
try:
    ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(',') if x and x.isdigit()]
except ValueError:
    ADMIN_IDS = []
DATABASE_URL = os.environ.get("DATABASE_URL")
BACK_BUTTON_TEXT = "🔙 Back"

if not all([TOKEN, DATABASE_URL]):
    raise ValueError("Missing required environment variables: TOKEN or DATABASE_URL")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger('api.bot')

# --- DB Pool ---
db_pool = None
def get_db_connection():
    global db_pool
    if db_pool is None:
        db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    return db_pool.getconn()

def release_db_connection(conn):
    global db_pool
    if db_pool:
        db_pool.putconn(conn)

def init_db():
    global db_pool
    if db_pool is not None:
        return
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    phone TEXT,
                    username TEXT UNIQUE,
                    name TEXT,
                    wallet INTEGER DEFAULT 10,
                    score INTEGER DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by TEXT,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    role TEXT DEFAULT 'user',
                    invalid_bingo_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
                CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code);

                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    amount INTEGER NOT NULL,
                    method TEXT,
                    verification_code TEXT,
                    transaction_type TEXT DEFAULT 'deposit',
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);

                CREATE TABLE IF NOT EXISTS referrals (
                    referral_id SERIAL PRIMARY KEY,
                    referrer_id BIGINT,
                    referee_id BIGINT,
                    bonus_credited BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referrer_id) REFERENCES users(user_id),
                    FOREIGN KEY (referee_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals(referrer_id);

                CREATE TABLE IF NOT EXISTS withdrawals (
                    withdraw_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    method TEXT,
                    admin_note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_withdrawals_user_id ON withdrawals(user_id);
            ''')
            conn.commit()
    finally:
        release_db_connection(conn)

# --- Utilities ---
def generate_referral_code(user_id):
    import hashlib
    return hashlib.md5(str(user_id).encode()).hexdigest()[:8]

def generate_tx_id(user_id):
    return f"TX{user_id}{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

def generate_withdraw_id(user_id):
    return f"WD{user_id}{random.randint(1000, 9999)}"

def check_referral_bonus(user_id):
    REFERRAL_BONUS = 10
    REFERRAL_THRESHOLD = 20
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = %s AND bonus_credited = FALSE",
                (user_id,)
            )
            referral_count = cursor.fetchone()[0]
            if referral_count >= REFERRAL_THRESHOLD:
                bonuses_to_award = referral_count // REFERRAL_THRESHOLD
                bonus_amount = bonuses_to_award * REFERRAL_BONUS
                cursor.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (bonus_amount, user_id))
                cursor.execute(
                    "UPDATE referrals SET bonus_credited = TRUE WHERE referrer_id = %s LIMIT %s",
                    (user_id, bonuses_to_award * REFERRAL_THRESHOLD)
                )
                conn.commit()
                return bonus_amount
            return 0
    except Exception as e:
        logger.error(f"Error checking referral bonus: {str(e)}")
        return 0
    finally:
        release_db_connection(conn)

# --- Telegram Bot Logic ---
application = None  # Will be initialized in main()

def main_menu_keyboard(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
            registered = cursor.fetchone() is not None
            keyboard = []
            if registered:
                keyboard.extend([
                    [InlineKeyboardButton("💰 Check Balance", callback_data='check_balance')],
                    [InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard')],
                    [InlineKeyboardButton("💳 Deposit", callback_data='deposit')],
                    [InlineKeyboardButton("👥 Invite Friends", callback_data='invite')],
                    [InlineKeyboardButton("📖 Instructions", callback_data='instructions')],
                    [InlineKeyboardButton("🛟 Contact Support", callback_data='support')]
                ])
            else:
                keyboard.extend([
                    [InlineKeyboardButton("📝 Register", callback_data='register')],
                    [InlineKeyboardButton("📖 Instructions", callback_data='instructions')],
                    [InlineKeyboardButton("🛟 Contact Support", callback_data='support')]
                ])
            return InlineKeyboardMarkup(keyboard)
    finally:
        release_db_connection(conn)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = "🎉 Welcome to ዜቢ ቢንጎ! 🎉\n💰 Win prizes\n🎱 Play with friends!"
    reply_markup = main_menu_keyboard(user.id)
    await update.message.reply_text(
        text=message,
        reply_markup=reply_markup
    )

# --- Registration Handlers, Balance, Leaderboard, Deposit, Referrals, etc. ---
# (same as in your previous version, but remove any reference to WebAppInfo or webapp URLs)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text="ለመቀጠል ስልክ ቁጥሮን ያጋሩ!",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("📲 Share Contact", request_contact=True)]
        ], resize_keyboard=True, one_time_keyboard=True)
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    context.user_data['phone'] = contact.phone_number
    context.user_data['name'] = contact.first_name or user.username
    context.user_data['awaiting_username'] = True
    await update.message.reply_text(
        "Please enter your desired username:",
        reply_markup=ReplyKeyboardRemove()
    )

async def username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_username' not in context.user_data:
        return
    username = update.message.text.strip()
    if not (3 <= len(username) <= 20):
        await update.message.reply_text("❌ Username must be 3-20 characters. Try again:")
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            referral_code = generate_referral_code(update.effective_user.id)
            cursor.execute(
                """
                INSERT INTO users (user_id, phone, name, username, referral_code, wallet, score, role)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (update.effective_user.id, context.user_data['phone'], context.user_data['name'],
                 username, referral_code, 10, 0, 'user')
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "UPDATE users SET username = %s WHERE user_id = %s AND username IS NULL",
                    (username, update.effective_user.id)
                )
            conn.commit()
            bonus = check_referral_bonus(update.effective_user.id)
            message = f"🎉 Registration successful, {username}! 10 ETB credited."
            if bonus > 0:
                message += f"\nYou earned {bonus} ETB for referrals!"
            await update.message.reply_text(
                message,
                reply_markup=main_menu_keyboard(update.effective_user.id)
            )
    finally:
        release_db_connection(conn)
        context.user_data.pop('awaiting_username', None)

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text="📋 የዜቢ ቢንጎ መመሪያዎች\n\n... 
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
 ...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
        ]),
        parse_mode='Markdown'
    )

async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT referral_code FROM users WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            referral_code = result[0] if result else generate_referral_code(user_id)
            if not result:
                cursor.execute(
                    "UPDATE users SET referral_code = %s WHERE user_id = %s",
                    (referral_code, user_id)
                )
                conn.commit()
            # Webapp gone, so share bot link only
            invite_link = f"https://t.me/{context.bot.username}?start=ref_{referral_code}"
            message = f"👥 Invite friends and earn 10 ETB per referral!\nYour link: {invite_link}"
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
            )
    finally:
        release_db_connection(conn)

async def contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text="🛟 Contact Support\n\nFor help, contact @ZebiSupportBot\nAvailable 24/7!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
    )

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT wallet FROM users WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            balance = result[0] if result else 0
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=f"💰 Your balance: {balance} ETB",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
            )
    finally:
        release_db_connection(conn)

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT username, score, wallet
                FROM users
                WHERE role = 'user'
                ORDER BY score DESC, wallet DESC
                LIMIT 10
                """
            )
            leaderboard = cursor.fetchall()
            leaderboard_text = "🏆 Top 10 Players:\n"
            for i, (username, score, wallet) in enumerate(leaderboard, 1):
                leaderboard_text += f"{i}. {username or 'Anonymous'} - {score} points, {wallet} ETB\n"
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=leaderboard_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
            )
    finally:
        release_db_connection(conn)

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
        await update.callback_query.answer(),
        await update.callback_query.edit_message_text(
                text="💳 Please enter the deposit amount (ETB):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
            )
            
async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if 'awaiting_deposit' not in context.user_data:
            logger.warning(f"User {user_id} attempted deposit without proper state")
            return

        amount_text = update.message.text.strip()
        if not amount_text.isdigit() or int(amount_text) <= 0:
            await update.message.reply_text("⚠️ Please enter a valid positive number for the deposit amount.")
            return  # Do not pop here, let them try again

        amount = int(amount_text)
        MINIMUM_DEPOSIT = 10  # Adjust as needed
        if amount < MINIMUM_DEPOSIT:
            await update.message.reply_text(f"⚠️ Minimum deposit is {MINIMUM_DEPOSIT} ETB")
            return

        context.user_data['deposit_amount'] = amount
        logger.info(f"User {user_id} entered deposit amount: {amount} ETB")
        context.user_data.pop('awaiting_deposit', None)  # Success: clear state
        await show_payment_options(update, context)

    except Exception as e:
        logger.error(f"Error processing deposit for user {user_id}: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")
        context.user_data.pop('awaiting_deposit', None)  # On error, clear state

async def show_payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if 'deposit_amount' not in context.user_data:
            logger.warning(f"User {user_id} accessed payment options without amount")
            await update.message.reply_text("⚠️ Please start the deposit process again.")
            return

        amount = context.user_data['deposit_amount']
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Telebirr", callback_data="payment_telebirr")],
            [InlineKeyboardButton("CBE", callback_data="payment_cbe")],
            [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
        ])
        logger.info(f"Showing payment options to user {user_id} for {amount} ETB")
        await update.message.reply_text(
            f"💳 Select payment method for {amount} ETB:",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Error showing payment options to user {user_id}: {e}")
        await update.message.reply_text("❌ Failed to load payment options.


async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try:
        await query.answer()

        if 'deposit_amount' not in context.user_data:
            logger.warning(f"User {user_id} selected payment without amount")
            await query.edit_message_text("⚠️ Deposit session expired. Please start over.")
            return

        method = query.data.split('_')[1].lower()
        amount = context.user_data['deposit_amount']
        tx_id = generate_tx_id(user_id)

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO transactions (tx_id, user_id, amount, method, verification_code) VALUES (%s, %s, %s, %s, %s)",
                    (tx_id, user_id, amount, method, tx_id[-6:])
                )
                conn.commit()
        finally:
            release_db_connection(conn)

        if method == 'telebirr':
            payment_details = f"""📋 Telebirr Payment Instructions:
Amount: {amount} ETB
Reference: {tx_id[-6:]}
Account: +251944156222
 

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

Amount: {amount} ETB
Reference: {tx_id[-6:]}
Account: 1000340957688
Name: ናትናኤል ዳንኤል

📝 **Detailed Instructions:**
1. ከላይ ባለው የኢትዮጵያ ንግድ ባንክ አካውንት {amount} ብር ያስገቡ
2. የምትልኩት የገንዘብ መጠን እና እዚ ላይ እንዲሞላልዎ የምታስገቡት የብር መጠን ተመሳሳይ መሆኑን እርግጠኛ ይሁን
3. ብሩን ስትልኩ የከፈላችሁበትን መረጃ የያዝ አጭር የጹሁፍ መልክት(sms) ከኢትዮጵያ ንግድ ባንክ ይደርሳችኋል
4. የደረሳችሁን አጭር የጹሁፍ መለክት(sms) ሙሉዉን ኮፒ(copy) በማረግ ከታሽ ባለው የቴሌግራም የጹሁፍ ማስገቢአው ላይ ፔስት(paste) በማረግ ይላኩት
5. ብር ስትልኩ የምትጠቀሙት USSD(889) ከሆነ አንዳንዴ አጭር የጹሁፍ መለክት(sms) ላይገባላቹ ስለሚችል ከUSSD(889) ሂደት መጨረሻ ላይ Complete የሚለው ላይ ስደርሱ 3 ቁጥርን በመጫን የትራንዛክሽን ቁጥሩን ሲያሳያቹህ ትራንዛክሽን ቁጥሩን ጽፎ ማስቀመጥ ይኖርባችኋል

ማሳሰቢያ 📢:
1. አጭር የጹሁፍ መለክት(sms) ካልደረሳቹ ያለትራንዛክሽን ቁጥር ሲስተሙ ዋሌት ስለማይሞላላቹ የከፈላችሁበትን ደረሰኝ ከባንክ በመቀበል በማንኛውም ሰአት ትራንዛክሽን ቁጥሩን ቦቱ ላይ ማስገባት ትችላላቹ
2. ዲፖዚት ባረጋቹ ቁጥር ቦቱ የሚያገናኛቹ ኤጀንቶች ስለሚለያዩ ከላይ ወደሚሰጣቹ የኢትዮጵያ ንግድ ባንክ አካውንት ብቻ ብር መላካችሁን እርግጠኛ ይሁኑ።
                """
            logger.info(f"User {user_id} selected {method} payment for {amount} ETB")
        await query.edit_message_text(
            f"✅ Payment method selected\n\n{payment_details}\n"
            "Please complete the payment and send the confirmation.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
            ])
        )
        context.user_data.pop('deposit_amount', None)  # Optionally clear

    except Exception as e:
        logger.error(f"Error handling payment method for user {user_id}: {e}")
        await query.edit_message_text(
            "❌ Failed to process payment selection.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
            ])
        )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id not in ADMIN_IDS:
            return
        await update.message.reply_text(
            "🛠 Admin Panel",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Create Game", callback_data="admin_create_game")],
                [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
                [InlineKeyboardButton("✅ Verify Payments", callback_data="admin_verify")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
                [InlineKeyboardButton("💸 Manage Withdrawals", callback_data="admin_withdrawals")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in admin: {str(e)}")
        await update.message.reply_text("❌ Error accessing admin panel.")

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    try:
        await query.answer()

        if user_id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin access attempt by {user_id}")
            await query.edit_message_text("⛔ Unauthorized access.")
            return

        action = query.data.split('_')[1]
        if action == "verify":
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT tx_id, user_id, amount FROM transactions WHERE status = 'pending'"
                    )
                    pending_txs = cursor.fetchall()

                if not pending_txs:
                    await query.edit_message_text(
                        "✅ No pending transactions.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]
                        ])
                    )
                    return

                keyboard = [
                    [InlineKeyboardButton(f"TX {tx[0]} - User {tx[1]} - {tx[2]} ETB",
                     callback_data=f"verify_{tx[0]}")]
                    for tx in pending_txs
                ]
                keyboard.append([InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')])

                await query.edit_message_text(
                    "📋 Pending Transactions:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            finally:
                release_db_connection(conn)
        elif action == "withdrawals":
            # Similar pattern for withdrawals handling
            pass

    except Exception as e:
        logger.error(f"Error in admin_handler for user {user_id}: {e}")
        await query.edit_message_text(
            "❌ Admin action failed.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]
            ])
        )

async def process_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if user_id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin input attempt by {user_id}")
            return

        text = update.message.text

        if 'awaiting_broadcast' in context.user_data:
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id FROM users")
                    user_ids = [row[0] for row in cursor.fetchall()]

                success = 0
                for uid in user_ids:
                    try:
                        await context.bot.send_message(
                            chat_id=uid,
                            text=f"📢 Announcement:\n\n{text}"
                        )
                        success += 1
                    except Exception as e:
                        logger.warning(f"Failed to send to user {uid}: {e}")

                await update.message.reply_text(
                    f"📢 Broadcast sent to {success}/{len(user_ids)} users.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]
                    ])
                )

            finally:
                release_db_connection(conn)
                context.user_data.pop('awaiting_broadcast', None)

    except Exception as e:
        logger.error(f"Error processing admin input for user {user_id}: {e}")
        await update.message.reply_text(
            "❌ Failed to process admin command.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]
            ])
        )


# Add similar minimal handlers for deposits, withdrawals, admin, etc. as in your previous version,
# but ensure you remove any reference to webapp URLs and static file serving.

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text="🎉 Welcome back to ዜቢ ቢንጎ!",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    try:
        if update and update.effective_message:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error occurred. Please try again or contact support."
            )
    except Exception as e:
        logger.error(f"Error in error_handler: {str(e)}", exc_info=True)

def setup_bot():
    global application
    application = ApplicationBuilder().token(TOKEN).build()
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(register, pattern='^register$'))
    application.add_handler(CallbackQueryHandler(instructions, pattern='instructions$'))
    application.add_handler(CallbackQueryHandler(invite_friends, pattern='invite$'))
    application.add_handler(CallbackQueryHandler(contact_support, pattern='support$'))
    application.add_handler(CallbackQueryHandler(check_balance, pattern='check_balance$'))
    application.add_handler(CallbackQueryHandler(show_leaderboard, pattern='leaderboard$'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='back_to_menu$'))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, username_handler), group=1)
    application.add_error_handler(error_handler)

# --- Flask App for Vercel ---
app = Flask(__name__)

@app.route('/api/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint for Vercel"""
    global application
    if application is None:
        setup_bot()
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return jsonify({'status': 'ok'})

# --- Minimal API Endpoints for WebApp Integration (Database Sharing) ---
@app.route('/api/user_data', methods=['GET'])
def user_data():
    """Used by the webapp to query user data from the shared DB"""
    try:
        user_id = request.args.get('user_id')
        if not user_id or not user_id.isdigit():
            return jsonify({'error': 'Valid user_id is required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wallet, username, role, invalid_bingo_count
                    FROM users
                    WHERE user_id = %s
                    """,
                    (int(user_id),)
                )
                data = cursor.fetchone()
                if not data:
                    return jsonify({'error': 'User not found', 'registered': False}), 404
                bonus = check_referral_bonus(int(user_id))
                if bonus > 0:
                    cursor.execute("SELECT wallet FROM users WHERE user_id = %s", (int(user_id),))
                    data = cursor.fetchone() + data[1:]  # Update wallet after bonus
                return jsonify({
                    'wallet': data[0],
                    'username': data[1],
                    'role': data[2],
                    'invalid_bingo_count': data[3],
                    'registered': True,
                    'referral_bonus': bonus
                })
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in user_data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT username, score, wallet
                FROM users
                WHERE role = 'user'
                ORDER BY score DESC, wallet DESC
                LIMIT 10
                """
            )
            leaderboard = [
                {'username': row[0] or 'Anonymous', 'score': row[1], 'wallet': row[2]}
                for row in cursor.fetchall()
            ]
            return jsonify({'leaders': leaderboard})
    except Exception as e:
        logger.error(f"Error in leaderboard: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500
    finally:
        release_db_connection(conn)

# --- Vercel Entrypoint ---
# (No need for if __name__ == '__main__': block on Vercel)

# Initialize DB Pool and Bot on cold start
init_db()
setup_bot()