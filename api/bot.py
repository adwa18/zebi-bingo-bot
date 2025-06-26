from flask import Flask, request, jsonify, send_from_directory
import logging
import psycopg2
from psycopg2 import pool
import random
import string
import os
import time
from datetime import datetime, timedelta
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram import (
    Update,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'public')
app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='')
TOKEN = os.environ.get("TOKEN")
WEB_APP_URL = os.environ.get("WEB_APP_URL")
try:
    ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(',') if x and x.isdigit()]
except ValueError:
    logging.error("Invalid ADMIN_IDS format")
    ADMIN_IDS = []
DATABASE_URL = os.environ.get("DATABASE_URL")
BACK_BUTTON_TEXT = "🔙 Back"
API_URL = f"{WEB_APP_URL}/api"

# Validate environment variables
if not all([TOKEN, WEB_APP_URL, DATABASE_URL]):
    raise ValueError("Missing required environment variables: TOKEN, WEB_APP_URL, or DATABASE_URL")

# Constants
INSUFFICIENT_WALLET = "Insufficient wallet"
BET_OPTIONS = [10, 50, 100, 200]
HOUSE_CUT = 0.02  # 2% house cut
GAME_COUNTDOWN_SECONDS = 120  # 2 minutes
REFERRAL_BONUS = 10  # ETB for 20 successful referrals
REFERRAL_THRESHOLD = 20
MINIMUM_WITHDRAWAL = 100
MINIMUM_DEPOSIT = 50
INITIAL_WALLET = 10
SELECT_WALLET_QUERY = "SELECT wallet FROM users WHERE user_id = %s"
UPDATE_WALLET_CREDIT_QUERY = "UPDATE users SET wallet = wallet + %s WHERE user_id = %s"
UPDATE_WALLET_DEBIT_QUERY = "UPDATE users SET wallet = wallet - %s WHERE user_id = %s"
SELECT_CARD_NUMBERS_QUERY = "SELECT card_numbers FROM player_cards WHERE game_id = %s AND user_id = %s"
SELECT_ROLE_QUERY = "SELECT role FROM users WHERE user_id = %s"
UPDATE_ROLE_QUERY = "UPDATE users SET role = 'admin' WHERE user_id = %s AND role != 'admin'"

# Initialize logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- Database Functions ---
db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)

def get_db_connection():
    try:
        conn = db_pool.getconn()
        conn.autocommit = False
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

def release_db_connection(conn):
    try:
        db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"Error releasing connection: {str(e)}")

def init_db():
    logger.info("Initializing database")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    phone TEXT,
                    username TEXT UNIQUE,
                    name TEXT,
                    wallet INTEGER DEFAULT %s,
                    score INTEGER DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by TEXT,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    role TEXT DEFAULT 'user',
                    invalid_bingo_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
                CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code);
            ''', (INITIAL_WALLET,))
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    amount INTEGER,
                    method TEXT,
                    status TEXT DEFAULT 'pending',
                    verification_code TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
            ''')
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    players TEXT DEFAULT '',
                    numbers_called TEXT DEFAULT '',
                    status TEXT DEFAULT 'waiting',
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    winner_id BIGINT,
                    prize_amount INTEGER DEFAULT 0,
                    bet_amount INTEGER DEFAULT 0,
                    countdown_start TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_games_game_id ON games(game_id);
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS player_cards (
                    card_id SERIAL PRIMARY KEY,
                    game_id TEXT,
                    user_id BIGINT,
                    card_numbers TEXT,
                    card_accepted BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                );
                CREATE INDEX IF NOT EXISTS idx_player_cards_game_user ON player_cards(game_id, user_id);
            ''')
            cursor.execute('''
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
            logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)

def generate_referral_code(user_id):
    return f"BINGO{user_id}{random.randint(1000, 9999)}"

def generate_tx_id(user_id):
    return f"TX{user_id}{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

def generate_withdraw_id(user_id):
    return f"WD{user_id}{random.randint(1000, 9999)}"

def generate_game_id():
    return f"G{random.randint(10000, 99999)}"

def generate_card_numbers():
    numbers = random.sample(range(1, 101), 25)
    return ','.join(map(str, numbers))

def check_referral_bonus(user_id):
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
                cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (bonus_amount, user_id))
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

# --- Static File Serving ---
@app.route('/')
def serve_index():
    file_path = os.path.join(STATIC_FOLDER, 'index.html')
    if not os.path.exists(file_path):
        logger.error(f"index.html not found at {file_path}")
        return jsonify({'error': 'File not found'}), 404
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    file_path = os.path.join(STATIC_FOLDER, path)
    if not os.path.exists(file_path):
        logger.error(f"File not found: {path}")
        return jsonify({'error': 'File not found'}), 404
    return send_from_directory(STATIC_FOLDER, path)



# --- API Endpoints ---
@app.route('/api/user_data', methods=['GET'])
def user_data():
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
                    cursor.execute(SELECT_WALLET_QUERY, (int(user_id),))
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

@app.route('/api/invite_data', methods=['GET'])
def invite_data():
    try:
        user_id = request.args.get('user_id')
        if not user_id or not user_id.isdigit():
            return jsonify({'error': 'Valid user_id is required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT referral_code FROM users WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
                if not result:
                    return jsonify({'error': 'User not found'}), 404
                referral_code = result[0]
                cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = %s", (user_id,))
                referral_count = cursor.fetchone()[0]
                bot_username = 'ZebiBingoBot'  # Replace with actual bot username
                referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                return jsonify({
                    'referral_link': referral_link,
                    'referral_count': referral_count,
                    'bonus_threshold': REFERRAL_THRESHOLD,
                    'bonus_amount': REFERRAL_BONUS
                })
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in invite_data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/add_admin', methods=['POST'])
def add_admin():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        target_user_id = data.get('target_user_id')
        if not user_id or not target_user_id:
            return jsonify({'error': 'user_id and target_user_id required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_ROLE_QUERY, (user_id,))
                role = cursor.fetchone()
                if not role or role[0] != 'admin':
                    return jsonify({'status': 'unauthorized'}), 403
                cursor.execute(UPDATE_ROLE_QUERY, (target_user_id,))
                if cursor.rowcount > 0:
                    conn.commit()
                    return jsonify({'status': 'success', 'message': f'User {target_user_id} promoted to admin'})
                return jsonify({'status': 'failed', 'reason': 'User not found or already admin'}), 400
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in add_admin: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/get_contacts', methods=['GET'])
def get_contacts():
    try:
        user_id = request.args.get('user_id')
        if not user_id or not user_id.isdigit():
            return jsonify({'error': 'Valid user_id required'}), 400
        # Placeholder: Contacts not implemented in Telegram Web App
        return jsonify({'contacts': []})
    except Exception as e:
        logger.error(f"Error in get_contacts: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/send_invites', methods=['POST'])
def send_invites():
    try:
        data = request.get_json()
        friend_ids = data.get('friend_ids', [])
        if not friend_ids:
            return jsonify({'status': 'failed', 'reason': 'No friend IDs provided'}), 400
        # Placeholder: Sending invites not implemented
        sent_count = len(friend_ids)
        return jsonify({
            'status': 'success',
            'sent_count': sent_count,
            'message': f'Invites sent to {sent_count} friends'
        })
    except Exception as e:
        logger.error(f"Error in send_invites: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/admin_actions', methods=['POST'])
def admin_actions():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        action = data.get('action')
        if not user_id or not action:
            return jsonify({'error': 'user_id and action required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_ROLE_QUERY, (user_id,))
                role = cursor.fetchone()
                if not role or role[0] != 'admin':
                    return jsonify({'status': 'unauthorized'}), 403
                actions = {
                    'start_game': lambda: start_game_action(cursor, data.get('game_id'), data.get('bet_amount')),
                    'end_game': lambda: end_game_action(cursor, data.get('game_id')),
                    'verify_payment': lambda: verify_payment_action(cursor, data.get('tx_id')),
                    'kick_user': lambda: kick_user_action(cursor, data.get('target_user_id')),
                    'manage_withdrawal': lambda: manage_withdrawal_action(cursor, data.get('withdraw_id'), data.get('action_type'), data.get('admin_note', ''), user_id)
                }
                result = actions.get(action, lambda: jsonify({'status': 'failed', 'reason': 'Invalid action'}))()
                if result.status_code != 200:
                    return result
                conn.commit()
                return result
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in admin_actions: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

def start_game_action(cursor, game_id, bet_amount):
    cursor.execute("SELECT players, bet_amount FROM games WHERE game_id = %s AND status = 'waiting'", (game_id,))
    game = cursor.fetchone()
    if not game:
        return jsonify({'status': 'failed', 'reason': 'Game not found'}), 404
    if len(game[0].split(',')) < 2:
        return jsonify({'status': 'failed', 'reason': 'At least 2 players required'}), 400
    if game[1] != bet_amount:
        return jsonify({'status': 'failed', 'reason': 'Bet amount mismatch'}), 400
    cursor.execute(
        "UPDATE games SET status = 'started', start_time = %s, prize_amount = %s WHERE game_id = %s",
        (datetime.now(), bet_amount * len(game[0].split(',')), game_id)
    )
    return jsonify({'status': 'started', 'prize_amount': bet_amount * len(game[0].split(','))})

def end_game_action(cursor, game_id):
    cursor.execute(
        "UPDATE games SET status = 'finished', end_time = %s WHERE game_id = %s AND status = 'started'",
        (datetime.now(), game_id)
    )
    if cursor.rowcount > 0:
        return jsonify({'status': 'ended'})
    return jsonify({'status': 'failed', 'reason': 'Game not found or not started'}), 404

def verify_payment_action(cursor, tx_id):
    cursor.execute("SELECT user_id, amount FROM transactions WHERE tx_id = %s AND status = 'pending'", (tx_id,))
    tx = cursor.fetchone()
    if tx:
        user_id, amount = tx
        cursor.execute("UPDATE transactions SET status = 'verified' WHERE tx_id = %s", (tx_id,))
        cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (amount, user_id))
        cursor.execute("SELECT referrer_id FROM referrals WHERE referee_id = %s AND NOT bonus_credited", (user_id,))
        referrer = cursor.fetchone()
        if referrer:
            cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (REFERRAL_BONUS, referrer[0]))
            cursor.execute("UPDATE referrals SET bonus_credited = TRUE WHERE referee_id = %s", (user_id,))
        return jsonify({'status': 'verified', 'user_id': user_id, 'amount': amount})
    return jsonify({'status': 'failed', 'reason': 'Transaction not found or already processed'}), 404

def kick_user_action(cursor, target_user_id):
    cursor.execute("DELETE FROM users WHERE user_id = %s", (target_user_id,))
    if cursor.rowcount > 0:
        return jsonify({'status': 'kicked'})
    return jsonify({'status': 'failed', 'reason': 'User not found'}), 404

def manage_withdrawal_action(cursor, withdraw_id, action_type, admin_note, user_id):
    cursor.execute("SELECT user_id, amount FROM withdrawals WHERE withdraw_id = %s AND status = 'pending'", (withdraw_id,))
    withdrawal = cursor.fetchone()
    if withdrawal:
        withdrawal_user_id, amount = withdrawal
        cursor.execute(SELECT_WALLET_QUERY, (withdrawal_user_id,))
        wallet = cursor.fetchone()[0]
        if action_type == 'approve' and wallet >= amount:
            cursor.execute(UPDATE_WALLET_DEBIT_QUERY, (amount, withdrawal_user_id))
            cursor.execute("UPDATE withdrawals SET status = 'approved', admin_note = %s WHERE withdraw_id = %s", (admin_note, withdraw_id))
            return jsonify({'status': 'approved', 'user_id': withdrawal_user_id, 'amount': amount})
        elif action_type == 'reject':
            cursor.execute("UPDATE withdrawals SET status = 'rejected', admin_note = %s WHERE withdraw_id = %s", (admin_note, withdraw_id))
            return jsonify({'status': 'rejected', 'user_id': withdrawal_user_id, 'amount': amount})
    return jsonify({'status': 'failed', 'reason': 'Withdrawal not found or already processed'}), 404

@app.route('/api/create_game', methods=['POST'])
def create_game():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        bet_amount = data.get('bet_amount')
        if not user_id or bet_amount not in BET_OPTIONS:
            return jsonify({'status': 'failed', 'reason': 'Invalid user_id or bet amount'}), 400
        if int(user_id) not in ADMIN_IDS:
            return jsonify({'status': 'unauthorized', 'reason': 'Only admins can create games'}), 403
        game_id = generate_game_id()
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO games (game_id, status, bet_amount, countdown_start) VALUES (%s, 'waiting', %s, NULL)",
                    (game_id, bet_amount)
                )
                conn.commit()
                return jsonify({'game_id': game_id, 'status': 'waiting', 'bet_amount': bet_amount})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in create_game: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/available_games', methods=['GET'])
def get_available_games():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT game_id, bet_amount, COUNT(DISTINCT user_id) as players
                FROM games g
                LEFT JOIN player_cards pc ON g.game_id = pc.game_id
                WHERE g.status = 'waiting'
                GROUP BY g.game_id, g.bet_amount
            """)
            games = [
                {'game_id': row[0], 'bet_amount': row[1], 'players': row[2]}
                for row in cursor.fetchall()
            ]
            active_bets = {g['bet_amount'] for g in games}
            for bet in BET_OPTIONS:
                if bet not in active_bets:
                    games.append({'game_id': None, 'bet_amount': bet, 'players': 0})
            return jsonify({'games': games})
    except Exception as e:
        logger.error(f"Error getting available games: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        release_db_connection(conn)

@app.route('/api/join_game', methods=['POST'])
def join_game():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        bet_amount = data.get('bet_amount')
        if not user_id or not game_id or not bet_amount:
            return jsonify({'status': 'failed', 'reason': 'Missing parameters'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT players, bet_amount FROM games WHERE game_id = %s AND status = 'waiting'", (game_id,))
                game = cursor.fetchone()
                if not game:
                    return jsonify({'status': 'failed', 'reason': 'Game not found or not joinable'}), 404
                players, game_bet = game
                players = players.split(',') if players else []
                if str(user_id) in players:
                    return jsonify({'status': 'failed', 'reason': 'Already joined'}), 400
                if bet_amount != game_bet:
                    return jsonify({'status': 'failed', 'reason': 'Bet amount must match game'}), 400
                cursor.execute(SELECT_WALLET_QUERY, (user_id,))
                wallet = cursor.fetchone()
                if not wallet or wallet[0] < bet_amount:
                    return jsonify({'status': 'failed', 'reason': f'Insufficient funds. You have {wallet[0] if wallet else 0} ETB, need {bet_amount} ETB.'}), 400
                cursor.execute(UPDATE_WALLET_DEBIT_QUERY, (bet_amount, user_id))
                players.append(str(user_id))
                cursor.execute("UPDATE games SET players = %s WHERE game_id = %s", (','.join(players), game_id))
                cursor.execute(
                    "INSERT INTO player_cards (game_id, user_id, card_accepted) VALUES (%s, %s, FALSE)",
                    (game_id, user_id)
                )
                if len(players) == 1:
                    cursor.execute(
                        "UPDATE games SET countdown_start = %s WHERE game_id = %s",
                        (datetime.now(), game_id)
                    )
                conn.commit()
                return jsonify({'status': 'joined', 'game_id': game_id, 'players': len(players), 'bet_amount': bet_amount})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in join_game: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/select_number', methods=['POST'])
def select_number():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        selected_number = data.get('selected_number')
        if not user_id or not game_id or selected_number is None:
            return jsonify({'status': 'failed', 'reason': 'Missing parameters'}), 400
        if not (1 <= selected_number <= 100):
            return jsonify({'status': 'failed', 'reason': 'Number must be 1-100'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT players, status FROM games WHERE game_id = %s AND status = 'waiting'", (game_id,))
                game = cursor.fetchone()
                if not game or str(user_id) not in game[0].split(','):
                    return jsonify({'status': 'failed', 'reason': 'Invalid game or user'}), 400
                cursor.execute(
                    "SELECT selected_number FROM player_cards WHERE game_id = %s AND user_id = %s",
                    (game_id, user_id)
                )
                if cursor.fetchone():
                    return jsonify({'status': 'failed', 'reason': 'Number already selected'}), 400
                random.seed(selected_number)
                card_numbers = sorted(random.sample(range(1, 101), 25))
                cursor.execute(
                    "UPDATE player_cards SET card_numbers = %s WHERE game_id = %s AND user_id = %s",
                    (','.join(map(str, card_numbers)), game_id, user_id)
                )
                conn.commit()
                return jsonify({
                    'status': 'card_generated',
                    'card_numbers': card_numbers,
                    'selected_number': selected_number
                })
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in select_number: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/accept_card', methods=['POST'])
def accept_card():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        if not user_id or not game_id:
            return jsonify({'status': 'failed', 'reason': 'Missing parameters'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_CARD_NUMBERS_QUERY, (game_id, user_id))
                card = cursor.fetchone()
                if not card:
                    return jsonify({'status': 'failed', 'reason': 'Card not found'}), 404
                cursor.execute(
                    "UPDATE player_cards SET card_accepted = TRUE WHERE game_id = %s AND user_id = %s",
                    (game_id, user_id)
                )
                conn.commit()
                return jsonify({'status': 'accepted', 'card_numbers': card[0].split(',')})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in accept_card: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/game_status', methods=['GET'])
def game_status():
    try:
        game_id = request.args.get('game_id')
        user_id = request.args.get('user_id')
        if not game_id or not user_id:
            return jsonify({'error': 'game_id and user_id required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, start_time, end_time, numbers_called, prize_amount, winner_id, players, bet_amount, countdown_start
                    FROM games WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cursor.fetchone()
                if not game:
                    return jsonify({'status': 'not_found'}), 404
                status, start_time, end_time, numbers_called, prize_amount, winner_id, players_str, bet_amount, countdown_start = game
                players = players_str.split(',') if players_str else []
                cursor.execute(SELECT_CARD_NUMBERS_QUERY, (game_id, user_id))
                card = cursor.fetchone()
                if str(user_id) not in players and status != 'waiting':
                    return jsonify({'status': 'failed', 'reason': 'Not in game'}), 403
                auto_start = countdown_start and len(players) >= 2 and (datetime.now() - countdown_start).total_seconds() >= GAME_COUNTDOWN_SECONDS
                if auto_start and status == 'waiting':
                    cursor.execute(
                        "UPDATE games SET status = 'started', start_time = %s, prize_amount = %s WHERE game_id = %s",
                        (datetime.now(), bet_amount * len(players), game_id)
                    )
                    conn.commit()
                    status = 'started'
                    prize_amount = bet_amount * len(players)
                return jsonify({
                    'status': status,
                    'start_time': start_time.isoformat() if start_time else None,
                    'end_time': end_time.isoformat() if end_time else None,
                    'numbers_called': numbers_called.split(',') if numbers_called else [],
                    'prize_amount': prize_amount,
                    'winner_id': winner_id,
                    'players': players,
                    'bet_amount': bet_amount,
                    'card_numbers': card[0].split(',') if card else []
                })
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in game_status: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/call_number', methods=['POST'])
def call_number():
    try:
        data = request.get_json()
        game_id = data.get('game_id')
        if not game_id:
            return jsonify({'status': 'failed', 'reason': 'game_id required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT status, numbers_called, end_time FROM games WHERE game_id = %s", (game_id,))
                game = cursor.fetchone()
                if not game or game[0] != 'started' or (game[2] and datetime.now() > game[2]):
                    return jsonify({'status': 'invalid', 'reason': 'Game not started or ended'}), 400
                numbers = game[1].split(',') if game[1] else []
                if len(numbers) >= 100:
                    return jsonify({'status': 'complete', 'reason': 'All numbers called'}), 400
                new_number = random.randint(1, 100)
                while str(new_number) in numbers:
                    new_number = random.randint(1, 100)
                numbers.append(str(new_number))
                cursor.execute(
                    "UPDATE games SET numbers_called = %s WHERE game_id = %s",
                    (','.join(numbers), game_id)
                )
                conn.commit()
                return jsonify({'number': new_number, 'called_numbers': numbers, 'remaining': 100 - len(numbers)})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in call_number: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/check_bingo', methods=['POST'])
def check_bingo():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        if not user_id or not game_id:
            return jsonify({'status': 'failed', 'reason': 'Missing parameters'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT numbers_called, winner_id, players, bet_amount FROM games WHERE game_id = %s",
                    (game_id,)
                )
                game = cursor.fetchone()
                if not game or game[1] is not None:
                    return jsonify({'message': 'Game already has a winner or not started', 'won': False}), 400
                numbers_called, _, players, bet_amount = game
                players_list = players.split(',')
                if str(user_id) not in players_list:
                    return jsonify({'message': 'Not in this game', 'won': False}), 403
                cursor.execute(SELECT_CARD_NUMBERS_QUERY, (game_id, user_id))
                card = cursor.fetchone()
                if not card:
                    return jsonify({'message': 'Card not found', 'won': False}), 404
                card_numbers = set(card[0].split(','))
                numbers_called_set = set(numbers_called.split(',')) if numbers_called else set()
                marked = [num for num in card_numbers if num in numbers_called_set]
                card_grid = [marked[i:i+5] for i in range(0, 25, 5)]
                won = (
                    any(len(row) == 5 for row in card_grid) or
                    any(all(str(i*5 + col) in marked for i in range(5)) for col in range(5)) or
                    all(str(i*5 + i) in marked for i in range(5)) or
                    all(str(i*5 + (4-i)) in marked for i in range(5))
                )
                if not won:
                    players_list.remove(str(user_id))
                    cursor.execute(
                        "UPDATE games SET players = %s WHERE game_id = %s",
                        (','.join(players_list), game_id)
                    )
                    cursor.execute(
                        "DELETE FROM player_cards WHERE game_id = %s AND user_id = %s",
                        (game_id, user_id)
                    )
                    cursor.execute(
                        "UPDATE users SET invalid_bingo_count = invalid_bingo_count + 1 WHERE user_id = %s",
                        (user_id,)
                    )
                    conn.commit()
                    return jsonify({
                        'message': '🚫 Kicked from game for invalid Bingo!',
                        'won': False,
                        'kicked': True
                    }), 403
                total_bet = bet_amount * len(players_list)
                prize_amount = int(total_bet * (1 - HOUSE_CUT))
                cursor.execute(
                    "UPDATE games SET winner_id = %s, prize_amount = %s, status = 'finished', end_time = %s WHERE game_id = %s",
                    (user_id, prize_amount, datetime.now(), game_id)
                )
                cursor.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
                winner_username = cursor.fetchone()[0]
                cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (prize_amount, user_id))
                cursor.execute("UPDATE users SET score = score + 1 WHERE user_id = %s", (user_id,))
                conn.commit()
                return jsonify({
                    'message': f'🎉 Bingo! {winner_username} won {prize_amount} ETB!',
                    'won': True,
                    'prize': prize_amount
                })
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in check_bingo: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/pending_withdrawals', methods=['GET'])
def pending_withdrawals():
    try:
        user_id = request.args.get('user_id')
        if not user_id or not user_id.isdigit():
            return jsonify({'error': 'Valid user_id required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_ROLE_QUERY, (user_id,))
                role = cursor.fetchone()
                if not role or role[0] != 'admin':
                    return jsonify({'status': 'unauthorized'}), 403
                cursor.execute("SELECT withdraw_id, user_id, amount, method, request_time FROM withdrawals WHERE status = 'pending'")
                withdrawals = [
                    {'withdraw_id': row[0], 'user_id': row[1], 'amount': row[2], 'method': row[3], 'request_time': row[4].isoformat()}
                    for row in cursor.fetchall()
                ]
                return jsonify({'withdrawals': withdrawals})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in pending_withdrawals: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/request_withdrawal', methods=['POST'])
def request_withdrawal():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        amount = data.get('amount')
        method = data.get('method', 'telebirr')
        if not user_id or not amount or amount < MINIMUM_WITHDRAWAL:
            return jsonify({'status': 'failed', 'reason': f'Invalid user_id or amount (minimum {MINIMUM_WITHDRAWAL} ETB)'}), 400
        withdraw_id = generate_withdraw_id(user_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_WALLET_QUERY, (user_id,))
                wallet = cursor.fetchone()
                if not wallet or wallet[0] < amount:
                    return jsonify({'status': 'failed', 'reason': f'Insufficient funds. You have {wallet[0] if wallet else 0} ETB, need {amount} ETB.'}), 400
                cursor.execute(UPDATE_WALLET_DEBIT_QUERY, (amount, user_id))
                cursor.execute(
                    "INSERT INTO withdrawals (withdraw_id, user_id, amount, method) VALUES (%s, %s, %s, %s)",
                    (withdraw_id, user_id, amount, method)
                )
                conn.commit()
                return jsonify({'status': 'requested', 'withdraw_id': withdraw_id, 'amount': amount})
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in request_withdrawal: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/bot/user_data', methods=['GET'])
def bot_user_data():
    try:
        user_id = request.args.get('user_id')
        if not user_id or not user_id.isdigit():
            return jsonify({'error': 'Valid user_id required'}), 400
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT wallet, username, role FROM users WHERE user_id = %s", (user_id,))
                data = cursor.fetchone()
                if data:
                    bonus = check_referral_bonus(int(user_id))
                    if bonus > 0:
                        cursor.execute(SELECT_WALLET_QUERY, (user_id,))
                        data = cursor.fetchone() + data[1:]
                    return jsonify({
                        'wallet': data[0],
                        'username': data[1],
                        'is_admin': data[2] == 'admin',
                        'referral_bonus': bonus
                    })
                return jsonify({'error': 'User not found'}), 404
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in bot_user_data: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Webhook
@app.route('/api/webhook', methods=['GET', 'POST'])
async def webhook():
    if request.method == 'GET':
        return jsonify({'status': 'Webhook is active', 'url': f'{WEB_APP_URL}/api/webhook'})
    try:
        data = request.get_json()
        logger.info(f"Received webhook data: {data}")
        update = Update.de_json(data, application.bot)
        if not update:
            logger.error("Invalid update data")
            return jsonify({'error': 'Invalid update data'}), 400
        await application.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0].startswith('ref_'):
        try:
            referrer_id = int(context.args[0][4:])
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO referrals (referrer_id, referee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (referrer_id, user.id)
                    )
                    conn.commit()
            finally:
                release_db_connection(conn)
        except ValueError:
            logger.warning(f"Invalid referral code: {context.args[0]}")
    try:
        image_path = os.path.join(STATIC_FOLDER, 'bingo_welcome.png')
        if os.path.exists(image_path):
            await update.message.reply_photo(
                photo=InputFile(image_path),
                caption="🎉 Welcome to ዜቢ ቢንጎ! 🎉\n💰 Win prizes\n🎱 Play with friends via Web App!",
                reply_markup=main_menu_keyboard(user.id)
            )
        else:
            await update.message.reply_text(
                "🎉 Welcome to ዜቢ ቢንጎ! 🎉\n💰 Win prizes\n🎱 Play with friends via Web App!",
                reply_markup=main_menu_keyboard(user.id)
            )
    except Exception as e:
        logger.error(f"Error in start handler: {str(e)}")
        await update.message.reply_text(
            "🎉 Welcome to ዜቢ ቢንጎ! 🎉\n💰 Win prizes\n🎱 Play with friends via Web App!",
            reply_markup=main_menu_keyboard(user.id)
        )

def main_menu_keyboard(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
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
    finally:
        release_db_connection(conn)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "ለመቀጠል ስልክ ቁጥሮን ያጋሩ!",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("📲 Share Contact", request_contact=True)]
            ], resize_keyboard=True, one_time_keyboard=True)
        )
    except Exception as e:
        logger.error(f"Error in register handler: {str(e)}")
        await update.callback_query.edit_message_text("❌ Error occurred. Please try again.")

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        contact = update.message.contact
        user = update.effective_user
        context.user_data['phone'] = contact.phone_number
        context.user_data['name'] = contact.first_name or user.username
        context.user_data['awaiting_username'] = True
        await update.message.reply_text(
            "Please enter your desired username:",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Error in contact_handler: {str(e)}")
        await update.message.reply_text("❌ Error during registration.")

async def username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
                    INSERT INTO users (user_id, phone, name, username, referral_code, wallet)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (update.effective_user.id, context.user_data['phone'], context.user_data['name'],
                     username, referral_code, INITIAL_WALLET)
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "UPDATE users SET username = %s WHERE user_id = %s AND username IS NULL",
                        (username, update.effective_user.id)
                    )
                conn.commit()
                bonus = check_referral_bonus(update.effective_user.id)
                message = f"🎉 Registration successful, {username}! {INITIAL_WALLET} ETB credited."
                if bonus > 0:
                    message += f"\nYou earned {bonus} ETB for referrals!"
                await update.message.reply_text(
                    message,
                    reply_markup=main_menu_keyboard(update.effective_user.id)
                )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in username_handler: {str(e)}")
        await update.message.reply_text("❌ Error setting username.")
    finally:
        context.user_data.pop('awaiting_username', None)

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Error in instructions: {str(e)}")
        await update.callback_query.edit_message_text("❌ Error loading instructions.")

async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT referral_code FROM users WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
                if not result:
                    await query.edit_message_text("❌ Please register first.")
                    return
                referral_code = result[0]
                cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = %s", (user_id,))
                referral_count = cursor.fetchone()[0]
                bot_username = context.bot.username or "ZebiBingoBot"
                referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                invite_text = f"""
👥 Invite Friends & Earn!
Invite friends to earn {REFERRAL_BONUS} ETB for every {REFERRAL_THRESHOLD} registrations!
Your referral link:
👉 {referral_link} 👈
Current referrals: {referral_count}

📢 Share this copiable link with friends!
                """
                await query.edit_message_text(
                    text=invite_text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
                )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in invite_friends: {str(e)}")
        await query.edit_message_text("❌ Error generating invite link.")

async def contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🛟 Contact Support\n\nFor help, contact @ZebiSupportBot\nAvailable 24/7!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
        )
    except Exception as e:
        logger.error(f"Error in contact_support: {str(e)}")
        await update.callback_query.edit_message_text("❌ Error contacting support.")

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SELECT_WALLET_QUERY, (user_id,))
                balance = cursor.fetchone()
                bonus = check_referral_bonus(user_id)
                message = f"💰 Your balance: {balance[0]} ETB"
                if bonus > 0:
                    message += f"\n🎉 You earned {bonus} ETB for referrals!"
                await query.edit_message_text(
                    message,
                    reply_markup=main_menu_keyboard(user_id)
                )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in check_balance: {str(e)}")
        await query.edit_message_text("❌ Error checking balance.")

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
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
                await query.edit_message_text(
                    text=leaderboard_text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
                )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in show_leaderboard: {str(e)}")
        await query.edit_message_text("❌ Error loading leaderboard.")

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.edit_message_text(
            "💰 Enter deposit amount in Birr:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
        )
        context.user_data['awaiting_deposit'] = True
    except Exception as e:
        logger.error(f"Error in deposit: {str(e)}")
        await update.callback_query.edit_message_text("❌ Error initiating deposit.")

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if 'awaiting_deposit' not in context.user_data:
            return
        amount = float(update.message.text)
        if amount < MINIMUM_DEPOSIT:
            raise ValueError(f"Amount must be at least {MINIMUM_DEPOSIT} ETB")
        context.user_data['deposit_amount'] = amount
        await show_payment_options(update, context)
    except ValueError as e:
        await update.message.reply_text(f"❌ Invalid amount: {str(e)}")
    except Exception as e:
        logger.error(f"Error in process_deposit_amount: {str(e)}")
        await update.message.reply_text("❌ Error processing deposit amount.")
    finally:
        context.user_data.pop('awaiting_deposit', None)

async def show_payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = context.user_data['deposit_amount']
        await update.message.reply_text(
            f"Select payment method for {amount} Birr:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Telebirr", callback_data="payment_telebirr")],
                [InlineKeyboardButton("CBE", callback_data="payment_cbe")],
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]
            ])
        )
    except Exception as e:
        logger.error(f"Error in show_payment_options: {str(e)}")
        await update.message.reply_text("❌ Error showing payment options.")

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        method = query.data.split('_')[1]
        amount = context.user_data['deposit_amount']
        tx_id = generate_tx_id(query.from_user.id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO transactions (tx_id, user_id, amount, method, verification_code) VALUES (%s, %s, %s, %s, %s)",
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

ማሳሰቢያ 📢:
1. አጭር የጹሁፍ መለክት(sms) ካልደረሳቹ ያለትራንዛክሽን ቁጥር ሲስተሙ ዋሌት ስለማይሞላላቹ የከፈላችሁበትን ደረሰኝ ከባንክ በመቀበል በማንኛውም ሰአት ትራንዛክሽን ቁጥሩን ቦቱ ላይ ማስገባት ትችላላቹ
2. ዲፖዚት ባረጋቹ ቁጥር ቦቱ የሚያገናኛቹ ኤጀንቶች ስለሚለያዩ ከላይ ወደሚሰጣቹ የኢትዮጵያ ንግድ ባንክ አካውንት ብቻ ብር መላካችሁን እርግጠኛ ይሁኑ።
                """
            await query.edit_message_text(
                text=payment_details,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='back_to_menu')]])
            )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in handle_payment_method: {str(e)}")
        await query.edit_message_text("❌ Error processing payment method.")

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
    try:
        query = update.callback_query
        if not query or update.effective_user.id not in ADMIN_IDS:
            return
        await query.answer()
        action = query.data.split('_')[1]
        if action == "create_game":
            context.user_data['awaiting_bet_amount'] = True
            await query.edit_message_text(
                f"Enter bet amount ({', '.join(map(str, BET_OPTIONS))} ETB):"
            )
        elif action == "stats":
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM users")
                    users = cursor.fetchone()[0]
                    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status = 'verified'")
                    total_deposits = cursor.fetchone()[0] or 0
                    cursor.execute("SELECT COUNT(*) FROM transactions WHERE status = 'pending'")
                    pending = cursor.fetchone()[0]
                await query.edit_message_text(
                    f"📊 Stats: Users: {users}, Deposits: {total_deposits} ETB, Pending: {pending}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
                )
            finally:
                release_db_connection(conn)
        elif action == "verify":
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT tx_id, user_id, amount, verification_code FROM transactions WHERE status = 'pending'")
                    pending_txs = cursor.fetchall()
                if not pending_txs:
                    await query.edit_message_text(
                        "✅ No pending transactions.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
                    )
                    return
                keyboard = [
                    [InlineKeyboardButton(f"User {tx[1]} - {tx[2]} ETB ({tx[3]})", callback_data=f"verify_{tx[0]}")]
                    for tx in pending_txs
                ]
                keyboard.append([InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')])
                await query.edit_message_text(
                    "✅ Verify Payments:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            finally:
                release_db_connection(conn)
        

        elif action == "withdrawals":
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT withdraw_id, user_id, amount, method FROM withdrawals WHERE status = 'pending'")
                    withdrawals = cursor.fetchall()
                if not withdrawals:
                    await query.edit_message_text(
                        "✅ No pending withdrawals.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
                    )
                    return
                keyboard = [
                    [InlineKeyboardButton(f"User {w[1]} - {w[2]} ETB ({w[3]})", callback_data=f"withdraw_{w[0]}")]
                    for w in withdrawals
                ]
                keyboard.append([InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')])
                await query.edit_message_text(
                    "💸 Pending Withdrawals:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            finally:
                release_db_connection(conn)
        elif action == "broadcast":
            context.user_data['awaiting_broadcast'] = True
            await query.edit_message_text(
                "📢 Enter broadcast message:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
            )
        elif action.startswith("verify_"):
            tx_id = action.split('_')[1]
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id, amount FROM transactions WHERE tx_id = %s AND status = 'pending'", (tx_id,))
                    tx = cursor.fetchone()
                    if tx:
                        user_id, amount = tx
                        cursor.execute("UPDATE transactions SET status = 'verified' WHERE tx_id = %s", (tx_id,))
                        cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (amount, user_id))
                        cursor.execute("SELECT referrer_id FROM referrals WHERE referee_id = %s AND NOT bonus_credited", (user_id,))
                        referrer = cursor.fetchone()
                        if referrer:
                            cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (REFERRAL_BONUS, referrer[0]))
                            cursor.execute("UPDATE referrals SET bonus_credited = TRUE WHERE referee_id = %s", (user_id,))
                        conn.commit()
                        await query.edit_message_text(
                            f"✅ Transaction {tx_id} verified for {amount} ETB.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_verify')]])
                        )
                    else:
                        await query.edit_message_text(
                            "❌ Transaction not found or already processed.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_verify')]])
                        )
            finally:
                release_db_connection(conn)
        elif action.startswith("withdraw_"):
            withdraw_id = action.split('_')[1]
            context.user_data['withdraw_id'] = withdraw_id
            keyboard = [
                [InlineKeyboardButton("Approve", callback_data=f"withdraw_action_approve_{withdraw_id}")],
                [InlineKeyboardButton("Reject", callback_data=f"withdraw_action_reject_{withdraw_id}")],
                [InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_withdrawals')]
            ]
            await query.edit_message_text(
                f"💸 Manage withdrawal {withdraw_id}:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif action.startswith("withdraw_action_"):
            action_type, withdraw_id = action.split('_')[2:4]
            context.user_data['withdraw_action_type'] = action_type
            context.user_data['withdraw_id'] = withdraw_id
            await query.edit_message_text(
                f"Enter note for {action_type} withdrawal {withdraw_id}:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_withdrawals')]])
            )
    except Exception as e:
        logger.error(f"Error in admin_handler: {str(e)}")
        await query.edit_message_text("❌ Error in admin action.")
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
    
async def process_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        text = update.message.text
        if 'awaiting_bet_amount' in context.user_data:
            try:
                bet_amount = int(text)
                if bet_amount not in BET_OPTIONS:
                    await update.message.reply_text(
                        f"Invalid bet amount. Choose from {', '.join(map(str, BET_OPTIONS))} ETB."
                    )
                    return
                game_id = generate_game_id()
                conn = get_db_connection()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO games (game_id, status, bet_amount, countdown_start) VALUES (%s, 'waiting', %s, NULL)",
                            (game_id, bet_amount)
                        )
                        conn.commit()
                    await update.message.reply_text(
                        f"🎮 Game {game_id} created with {bet_amount} ETB bet.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
                    )
                finally:
                    release_db_connection(conn)
            except ValueError:
                await update.message.reply_text("❌ Invalid bet amount. Enter a number.")
            finally:
                context.user_data.pop('awaiting_bet_amount', None)
        elif 'awaiting_broadcast' in context.user_data:
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id FROM users")
                    user_ids = [row[0] for row in cursor.fetchall()]
                for uid in user_ids:
                    try:
                        await context.bot.send_message(chat_id=uid, text=f"📢 {text}")
                    except (BadRequest, Forbidden, TimedOut):
                        logger.warning(f"Failed to send broadcast to user {uid}")
                await update.message.reply_text(
                    "📢 Broadcast sent.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin')]])
                )
            finally:
                release_db_connection(conn)
                context.user_data.pop('awaiting_broadcast', None)
        elif 'withdraw_id' in context.user_data and 'withdraw_action_type' in context.user_data:
            withdraw_id = context.user_data['withdraw_id']
            action_type = context.user_data['withdraw_action_type']
            admin_note = text
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id, amount FROM withdrawals WHERE withdraw_id = %s AND status = 'pending'", (withdraw_id,))
                    withdrawal = cursor.fetchone()
                    if withdrawal:
                        withdrawal_user_id, amount = withdrawal
                        cursor.execute(SELECT_WALLET_QUERY, (withdrawal_user_id,))
                        wallet = cursor.fetchone()[0]
                        if action_type == 'approve' and wallet >= amount:
                            cursor.execute(UPDATE_WALLET_DEBIT_QUERY, (amount, withdrawal_user_id))
                            cursor.execute(
                                "UPDATE withdrawals SET status = 'approved', admin_note = %s WHERE withdraw_id = %s",
                                (admin_note, withdraw_id)
                            )
                            status = 'approved'
                        elif action_type == 'reject':
                            cursor.execute(
                                "UPDATE withdrawals SET status = 'rejected', admin_note = %s WHERE withdraw_id = %s",
                                (admin_note, withdraw_id)
                            )
                            status = 'rejected'
                        else:
                            await update.message.reply_text(
                                f"❌ Insufficient funds for user {withdrawal_user_id}.",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_withdrawals')]])
                            )
                            return
                        conn.commit()
                        await update.message.reply_text(
                            f"💸 Withdrawal {withdraw_id} {status}.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_withdrawals')]])
                        )
                    else:
                        await update.message.reply_text(
                            "❌ Withdrawal not found or already processed.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data='admin_withdrawals')]])
                        )
            finally:
                release_db_connection(conn)
                context.user_data.pop('withdraw_id', None)
                context.user_data.pop('withdraw_action_type', None)
    except Exception as e:
        logger.error(f"Error in process_admin_input: {str(e)}")
        await update.message.reply_text("❌ Error processing admin input.")

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🎉 Welcome back to ዜቢ ቢንጎ!",
            reply_markup=main_menu_keyboard(update.effective_user.id)
        )
    except Exception as e:
        logger.error(f"Error in back_to_menu: {str(e)}")
        await update.callback_query.edit_message_text("❌ Error returning to menu.")

async def process_transaction_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id, amount FROM transactions WHERE verification_code = %s AND status = 'pending'",
                    (text,)
                )
                tx = cursor.fetchone()
                if tx:
                    user_id, amount = tx
                    cursor.execute(
                        "UPDATE transactions SET status = 'verified' WHERE verification_code = %s",
                        (text,)
                    )
                    cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (amount, user_id))
                    cursor.execute(
                        "SELECT referrer_id FROM referrals WHERE referee_id = %s AND NOT bonus_credited",
                        (user_id,)
                    )
                    referrer = cursor.fetchone()
                    if referrer:
                        cursor.execute(UPDATE_WALLET_CREDIT_QUERY, (REFERRAL_BONUS, referrer[0]))
                        cursor.execute(
                            "UPDATE referrals SET bonus_credited = TRUE WHERE referee_id = %s",
                            (user_id,)
                        )
                    conn.commit()
                    await update.message.reply_text(
                        f"✅ Deposit of {amount} ETB verified!",
                        reply_markup=main_menu_keyboard(user_id)
                    )
                else:
                    await update.message.reply_text(
                        "❌ Invalid or already processed transaction code.",
                        reply_markup=main_menu_keyboard(update.effective_user.id)
                    )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error in process_transaction_code: {str(e)}")
        await update.message.reply_text("❌ Error processing transaction code.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ An error occurred. Please try again.")
    except Exception as e:
        logger.error(f"Error in error_handler: {str(e)}")

async def main():
    global application
    init_db()
    
        
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CallbackQueryHandler(instructions, pattern='instructions'))
    application.add_handler(CallbackQueryHandler(invite_friends, pattern='invite'))
    application.add_handler(CallbackQueryHandler(contact_support, pattern='support'))
    application.add_handler(CallbackQueryHandler(check_balance, pattern='check_balance'))
    application.add_handler(CallbackQueryHandler(show_leaderboard, pattern='leaderboard'))
    application.add_handler(CallbackQueryHandler(deposit, pattern='deposit'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='back_to_menu'))
    application.add_handler(CallbackQueryHandler(admin_handler, pattern='admin.*|verify_.*|withdraw_.*|withdraw_action_.*'))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^[a-zA-Z0-9]{6}$'), process_transaction_code))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, username_handler), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit_amount), group=2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input), group=3)
    application.add_error_handler(error_handler)
    await application.bot.set_webhook(url=f"{WEB_APP_URL}/api/webhook")
    return application

        

if __name__ == '__main__':
    
    import asyncio
    application = asyncio.run(main())
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))