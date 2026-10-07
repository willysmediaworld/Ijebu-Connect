import os
import re
import sqlite3
import random
import string
import json
import logging
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask, render_template_string, request, jsonify,
    g, session, redirect, url_for, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ======================================================================
# SYSTEM LOGGING SETUP
# ======================================================================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] in %(module)s: %(message)s'
)
logger = logging.getLogger("ijebu_connect")

# ======================================================================
# CONFIGURATION
# ======================================================================
DATABASE_URL = os.environ.get('DATABASE_URL')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '').strip()
ALLOW_TEST_PAYMENTS = os.environ.get('ALLOW_TEST_PAYMENTS', 'True').lower() == 'true'
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', 'willysmediaworld@gmail.com')
CONTACT_PHONE = "09018363715"
COMPANY_NAME = "Willys Media World"

BANK_INFO = {
    "bank_name": "OPay",
    "account_number": "09018363715",
    "account_name": "Rotimi Williams Oladele",
    "fee_naira": 2000
}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'ijebu_connect_secret_key_2026_secured'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max limit

# Static Upload Setup
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTS = {'mp4', 'webm', 'mov', 'm4v', 'avi'}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTS.union(ALLOWED_VIDEO_EXTS)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# HTTP CACHING & SPEED OPTIMIZATION FOR STATIC ASSETS
@app.after_request
def add_header(response):
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

# ======================================================================
# DATABASE ENGINE & NON-DESTRUCTIVE MIGRATION
# ======================================================================
def get_db():
    if 'db' not in g:
        if DATABASE_URL:
            import psycopg2
            import psycopg2.extras
            url = DATABASE_URL.replace("postgres://", "postgresql://")
            g.db = psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)
        else:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ijebu_connect.db')
            g.db = sqlite3.connect(db_path)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode = WAL;")
            g.db.execute("PRAGMA synchronous = NORMAL;")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def query_param():
    return "%s" if DATABASE_URL else "?"

def generate_ref_code():
    return 'CPN' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

def safe_add_column(cursor, table, column, col_type):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass

def add_notification(user_id, sender_id, notif_type, target_id, message):
    if user_id == sender_id or not user_id:
        return
    if notif_type == 'message':
        return
    try:
        db = get_db()
        cursor = db.cursor()
        p = query_param()
        cursor.execute(f'''
            INSERT INTO notifications (user_id, sender_id, type, target_id, message)
            VALUES ({p}, {p}, {p}, {p}, {p})
        ''', (user_id, sender_id, notif_type, target_id, message))
        db.commit()
    except Exception as e:
        logger.error(f"Error adding notification: {e}")

def count_user_listings(user_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT COUNT(*) FROM products WHERE user_id = {p} AND status = 'active'", (user_id,))
    prod_count = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM posts WHERE user_id = {p} AND content LIKE '%[PRODUCT_ADVERT]%'", (user_id,))
    ad_post_count = cursor.fetchone()[0]
    return prod_count + ad_post_count

# HARDCODED SEEDING TO PREVENT RENDER DATABASE RESET LOSSES
def seed_hardcoded_data(cursor, db):
    logger.info("Executing hardcoded seeding for Render environment protection...")
    p = query_param()
    admin_username = os.environ.get('ADMIN_SEED_USERNAME', 'ijebuconnect').lower()
    admin_password = os.environ.get('ADMIN_SEED_PASSWORD', 'Rotimi1972connect')
    admin_phone = os.environ.get('ADMIN_SEED_PHONE', '09018363715')
    admin_name = os.environ.get('ADMIN_SEED_NAME', "Sir Ola'Rotimi")
    admin_ref = os.environ.get('ADMIN_SEED_REF', 'CPN00001')
    admin_pass_hash = generate_password_hash(admin_password)

    cursor.execute(f"SELECT id FROM users WHERE username = {p} OR phone = {p} OR referral_code = {p}",
                   (admin_username, admin_phone, admin_ref))
    existing_admin = cursor.fetchone()

    if not existing_admin:
        try:
            cursor.execute(f'''
                INSERT INTO users (full_name, phone, username, password_hash, user_type, referral_code)
                VALUES ({p}, {p}, {p}, {p}, 'Admin', {p})
            ''', (admin_name, admin_phone, admin_username, admin_pass_hash, admin_ref))
            db.commit()
            admin_id = cursor.lastrowid or 1
            logger.info(f"Admin seed created with ID: {admin_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Error seeding admin user: {e}")
            admin_id = 1
    else:
        admin_id = existing_admin['id']
        try:
            cursor.execute(f"UPDATE users SET password_hash = {p}, user_type = 'Admin' WHERE id = {p}",
                           (admin_pass_hash, admin_id))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating admin seed: {e}")

    # Preload Official Pages
    cursor.execute("SELECT id FROM groups WHERE name LIKE '%Ijebu Imusin%'")
    if not cursor.fetchone():
        cursor.execute(f'''
            INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
            VALUES ({p}, 'Ijebu Imusin Youth Ambassadors Forum', 'Official platform for youth empowerment, leadership, community growth, and networking in Ijebu Imusin.', 'Community', '', '')
        ''', (admin_id,))
        page1_id = cursor.lastrowid or 1
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (page1_id, admin_id))
        cursor.execute(f'''
            INSERT INTO posts (user_id, group_id, content, post_type)
            VALUES ({p}, {p}, 'Welcome to Ijebu Imusin Youth Ambassadors Forum! Join us to empower the youth and build our community.', 'Social')
        ''', (admin_id, page1_id))

    cursor.execute("SELECT id FROM groups WHERE name LIKE '%Willys Media World%'")
    if not cursor.fetchone():
        cursor.execute(f'''
            INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
            VALUES ({p}, 'Willys Media World - Learn Coding', 'Welcome to Willys Media World Tech Hub! Learn Web Development, Software Engineering, Python, Flask, and Digital Skills. Phone: 09018363715 | Email: willysmediaworld@gmail.com', 'Education', '', '')
        ''', (admin_id,))
        page2_id = cursor.lastrowid or 2
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (page2_id, admin_id))
        cursor.execute(f'''
            INSERT INTO posts (user_id, group_id, content, post_type)
            VALUES ({p}, {p}, '🚀 Welcome to Willys Media World Coding Academy! Start learning Full-Stack Web Development, Python, JavaScript, and HTML/CSS today. Contact us at 09018363715 or willysmediaworld@gmail.com for mentorship.', 'Social')
        ''', (admin_id, page2_id))

    db.commit()
    logger.info("Seeding completed successfully.")

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        pk_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS users (
                id {pk_type},
                full_name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                user_type TEXT DEFAULT 'Resident',
                referral_code TEXT UNIQUE NOT NULL,
                referred_by TEXT DEFAULT NULL,
                wallet_balance REAL DEFAULT 0.0,
                is_verified_merchant INTEGER DEFAULT 0,
                age INTEGER DEFAULT 18,
                gender TEXT DEFAULT 'Unspecified',
                relationship_intent TEXT DEFAULT 'Networking',
                bio TEXT DEFAULT '',
                occupation TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                is_dating_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        safe_add_column(cursor, 'users', 'age', 'INTEGER DEFAULT 18')
        safe_add_column(cursor, 'users', 'gender', "TEXT DEFAULT 'Unspecified'")
        safe_add_column(cursor, 'users', 'relationship_intent', "TEXT DEFAULT 'Networking'")
        safe_add_column(cursor, 'users', 'bio', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'occupation', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'avatar_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'cover_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'is_dating_active', 'INTEGER DEFAULT 0')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS products (
                id {pk_type},
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT,
                image_url TEXT DEFAULT '',
                video_url TEXT DEFAULT '',
                location TEXT DEFAULT 'Ijebu Connect',
                whatsapp_number TEXT NOT NULL,
                listing_type TEXT DEFAULT 'Market',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS posts (
                id {pk_type},
                user_id INTEGER NOT NULL,
                group_id INTEGER DEFAULT 0,
                content TEXT NOT NULL,
                post_type TEXT DEFAULT 'Social',
                image_url TEXT DEFAULT '',
                video_url TEXT DEFAULT '',
                likes_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        safe_add_column(cursor, 'posts', 'group_id', 'INTEGER DEFAULT 0')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS post_likes (
                id {pk_type},
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, user_id)
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS comments (
                id {pk_type},
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                parent_id INTEGER DEFAULT 0,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        safe_add_column(cursor, 'comments', 'parent_id', 'INTEGER DEFAULT 0')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS comment_likes (
                id {pk_type},
                comment_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(comment_id, user_id)
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS followers (
                id {pk_type},
                follower_id INTEGER NOT NULL,
                followed_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(follower_id, followed_id)
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS groups (
                id {pk_type},
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'Community',
                avatar_url TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        safe_add_column(cursor, 'groups', 'cover_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'groups', 'avatar_url', "TEXT DEFAULT ''")

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS group_members (
                id {pk_type},
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, user_id)
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS events (
                id {pk_type},
                group_id INTEGER DEFAULT 0,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                event_date TEXT DEFAULT '',
                location TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS notifications (
                id {pk_type},
                user_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                target_id INTEGER DEFAULT 0,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS dating_winks (
                id {pk_type},
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sender_id, receiver_id)
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS partner_requests (
                id {pk_type},
                user_id INTEGER NOT NULL,
                amount REAL DEFAULT 2000.0,
                payment_method TEXT DEFAULT 'Bank Transfer',
                reference_note TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS transactions (
                id {pk_type},
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                tx_type TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS payout_requests (
                id {pk_type},
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                bank_name TEXT NOT NULL,
                account_number TEXT NOT NULL,
                account_name TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS messages (
                id {pk_type},
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS blocked_users (
                id {pk_type},
                blocker_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(blocker_id, blocked_id)
            )''')

        # INDEX OPTIMIZATIONS FOR SPEED
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users (LOWER(username))")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users (phone)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ref ON users (referral_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_type_group ON posts (post_type, group_id, id DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_user ON posts (user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_group ON events (group_id, id DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_likes ON post_likes (post_id, user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_post ON comments (post_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_followers_pair ON followers (follower_id, followed_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages (sender_id, receiver_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages (receiver_id, is_read)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications (user_id, is_read)")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

        seed_hardcoded_data(cursor, db)

with app.app_context():
    init_db()

# ======================================================================
# FILE & MEDIA UPLOADER
# ======================================================================
@app.route('/api/upload', methods=['POST'])
def upload_media():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file submitted.'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected.'}), 400
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        is_video = ext in ALLOWED_VIDEO_EXTS
        filename = f"{'vid' if is_video else 'img'}_{session['user_id']}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}.{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        file_url = f"/static/uploads/{filename}"
        logger.info(f"File uploaded successfully: {file_url}")
        return jsonify({'success': True, 'url': file_url, 'is_video': is_video})
    return jsonify({'success': False, 'message': 'Unsupported file format.'}), 400

# ======================================================================
# CPN COMMISSION ENGINE
# ======================================================================
def process_cpn_commission(user_id, upgrade_fee=2000.0):
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT full_name, referred_by FROM users WHERE id = {p}", (user_id,))
    buyer = cursor.fetchone()
    if not buyer or not buyer['referred_by']:
        return

    cursor.execute(f"SELECT id, full_name, referred_by FROM users WHERE referral_code = {p}", (buyer['referred_by'],))
    t1 = cursor.fetchone()
    if t1:
        bonus1 = upgrade_fee * 0.10
        cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}", (bonus1, t1['id']))
        cursor.execute(f"INSERT INTO transactions (user_id, amount, tx_type, description) VALUES ({p}, {p}, 'Tier-1 CPN Commission', {p})",
                       (t1['id'], bonus1, f"10% CPN Reward from {buyer['full_name']}"))
        add_notification(t1['id'], user_id, 'commission', 0, f"You earned ₦{bonus1:.2f} Tier-1 CPN Reward from {buyer['full_name']}!")

        if t1['referred_by']:
            cursor.execute(f"SELECT id FROM users WHERE referral_code = {p}", (t1['referred_by'],))
            t2 = cursor.fetchone()
            if t2:
                bonus2 = upgrade_fee * 0.05
                cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}", (bonus2, t2['id']))
                cursor.execute(f"INSERT INTO transactions (user_id, amount, tx_type, description) VALUES ({p}, {p}, 'Tier-2 CPN Commission', {p})",
                               (t2['id'], bonus2, f"5% CPN Reward from {buyer['full_name']}"))
                add_notification(t2['id'], user_id, 'commission', 0, f"You earned ₦{bonus2:.2f} Tier-2 CPN Reward!")
    db.commit()

def require_admin():
    if 'user_id' not in session:
        return None, (jsonify({'success': False, 'message': 'Login required.'}), 401)
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT id, full_name, username, user_type FROM users WHERE id = {p}", (session['user_id'],))
    row = cursor.fetchone()
    if not row or row['user_type'] != 'Admin':
        return None, (jsonify({'success': False, 'message': 'Admin access required.'}), 403)
    return dict(row), None

# ======================================================================
# AUTH API ENDPOINTS
# ======================================================================
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json or {}
    full_name = data.get('full_name', '').strip()
    phone = data.get('phone', '').strip()
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    ref_by = data.get('referred_by', '').strip().upper()

    if not full_name or not phone or not username or not password:
        return jsonify({'success': False, 'message': 'All fields are required.'}), 400

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT id FROM users WHERE username = {p} OR phone = {p}", (username, phone))
    if cursor.fetchone():
        return jsonify({'success': False, 'message': 'Username or Phone already registered.'}), 400

    valid_ref = None
    if ref_by:
        cursor.execute(f"SELECT referral_code FROM users WHERE referral_code = {p}", (ref_by,))
        r = cursor.fetchone()
        if r:
            valid_ref = r['referral_code']

    new_ref = generate_ref_code()
    ph = generate_password_hash(password)
    cursor.execute(
        f'''INSERT INTO users (full_name, phone, username, password_hash, referral_code, referred_by)
        VALUES ({p}, {p}, {p}, {p}, {p}, {p})''',
        (full_name, phone, username, ph, new_ref, valid_ref)
    )
    db.commit()
    logger.info(f"New user registered: username={username}, ref={new_ref}")
    return jsonify({'success': True, 'message': 'Account created successfully! You can now sign in.'})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT * FROM users WHERE username = {p} OR phone = {p}", (username, username))
    user = cursor.fetchone()

    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['full_name'] = user['full_name']
        session['user_type'] = user['user_type']
        session['referral_code'] = user['referral_code']

        cursor.execute(f"SELECT COUNT(*) FROM users WHERE referred_by = {p}", (user['referral_code'],))
        recruits = cursor.fetchone()[0]

        logger.info(f"User logged in: {username}")
        return jsonify({
            'success': True,
            'message': f'Welcome back, {user["full_name"]}!',
            'user': {
                'id': user['id'],
                'full_name': user['full_name'],
                'phone': user['phone'],
                'username': user['username'],
                'user_type': user['user_type'],
                'referral_code': user['referral_code'],
                'wallet_balance': float(user['wallet_balance'] or 0),
                'is_verified_merchant': user['is_verified_merchant'],
                'occupation': user['occupation'],
                'age': user['age'],
                'gender': user['gender'],
                'bio': user['bio'],
                'listings_count': count_user_listings(user['id']),
                'recruits_count': recruits
            }
        })
    logger.warning(f"Failed login attempt for user: {username}")
    return jsonify({'success': False, 'message': 'Invalid credentials.'}), 401

@app.route('/api/auth/me', methods=['GET'])
def get_current_user():
    if 'user_id' in session:
        db = get_db()
        cursor = db.cursor()
        p = query_param()
        cursor.execute(
            f'''SELECT id, full_name, phone, username, user_type, referral_code, wallet_balance,
            is_verified_merchant, age, gender, relationship_intent, bio, occupation, avatar_url, cover_url, is_dating_active
            FROM users WHERE id = {p}''',
            (session['user_id'],)
        )
        u = cursor.fetchone()
        if u:
            d = dict(u)
            d['wallet_balance'] = float(d.get('wallet_balance') or 0)
            d['listings_count'] = count_user_listings(d['id'])

            # OPTIMIZED SINGLE-PASS METRICS QUERY FOR FASTER SPEED
            cursor.execute(f'''
                SELECT 
                    (SELECT COUNT(*) FROM users WHERE referred_by = {p}) AS recruits_count,
                    (SELECT COUNT(*) FROM notifications WHERE user_id = {p} AND is_read = 0) AS unread_notifs,
                    (SELECT COUNT(*) FROM messages WHERE receiver_id = {p} AND is_read = 0) AS unread_chats
            ''', (d['referral_code'], d['id'], d['id']))
            counts = cursor.fetchone()
            d['recruits_count'] = counts[0]
            d['unread_notifs'] = counts[1]
            d['unread_chats'] = counts[2]

            return jsonify({'logged_in': True, 'user': d})
    return jsonify({'logged_in': False})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully.'})

# ======================================================================
# FULL PROFILE UPDATE ENDPOINT
# ======================================================================
@app.route('/api/users/profile/update', methods=['POST'])
def update_user_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401

    data = request.json or {}
    full_name = data.get('full_name', '').strip()
    phone = data.get('phone', '').strip()
    occupation = data.get('occupation', '').strip()
    gender = data.get('gender', 'Unspecified').strip()
    bio = data.get('bio', '').strip()
    avatar_url = data.get('avatar_url', '').strip()
    cover_url = data.get('cover_url', '').strip()

    try:
        age = int(data.get('age', 18))
    except (ValueError, TypeError):
        age = 18

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    if phone:
        cursor.execute(f"SELECT id FROM users WHERE phone = {p} AND id != {p}", (phone, uid))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Phone number already used by another account.'}), 400

    updates = []
    params = []

    if full_name:
        updates.append(f"full_name = {p}")
        params.append(full_name)
        session['full_name'] = full_name
    if phone:
        updates.append(f"phone = {p}")
        params.append(phone)
    if occupation is not None:
        updates.append(f"occupation = {p}")
        params.append(occupation)
    if age:
        updates.append(f"age = {p}")
        params.append(age)
    if gender:
        updates.append(f"gender = {p}")
        params.append(gender)
    if bio is not None:
        updates.append(f"bio = {p}")
        params.append(bio)
    if avatar_url:
        updates.append(f"avatar_url = {p}")
        params.append(avatar_url)
    if cover_url:
        updates.append(f"cover_url = {p}")
        params.append(cover_url)

    if updates:
        params.append(uid)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = {p}", tuple(params))
        db.commit()

    return jsonify({'success': True, 'message': 'All profile details updated successfully!'})

# ======================================================================
# PAGES API (FACEBOOK-STYLE PAGES)
# ======================================================================
@app.route('/api/pages', methods=['GET', 'POST'])
def handle_pages():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401
        data = request.json or {}
        name = data.get('name', '').strip()
        desc = data.get('description', '').strip()
        avatar = data.get('avatar_url', '').strip()
        cover = data.get('cover_url', '').strip()

        if not name:
            return jsonify({'success': False, 'message': 'Page name required.'}), 400

        cursor.execute(f'''INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
        VALUES ({p}, {p}, {p}, 'Community', {p}, {p})''', (session['user_id'], name, desc, avatar, cover))
        page_id = cursor.lastrowid or 0
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (page_id, session['user_id']))
        db.commit()
        return jsonify({'success': True, 'message': f'Page "{name}" created successfully!'})

    cursor.execute(f'''
        SELECT g.*, COUNT(gm.id) AS member_count
        FROM groups g LEFT JOIN group_members gm ON g.id = gm.group_id
        GROUP BY g.id ORDER BY g.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/pages/<int:page_id>', methods=['GET'])
def get_page_detail(page_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session.get('user_id') or 0

    cursor.execute(f'''
        SELECT g.*, u.full_name AS creator_name, u.username AS creator_username,
        (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id = g.id) AS member_count,
        CASE WHEN EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = g.id AND gm.user_id = {p}) THEN 1 ELSE 0 END AS is_member
        FROM groups g
        JOIN users u ON g.user_id = u.id
        WHERE g.id = {p}
    ''', (uid, page_id))
    page = cursor.fetchone()
    if not page:
        return jsonify({'success': False, 'message': 'Page not found.'}), 404
    res = dict(page)
    res['is_creator'] = (uid == page['user_id'])
    return jsonify({'success': True, 'page': res})

@app.route('/api/pages/<int:page_id>/update', methods=['POST'])
def update_page(page_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT user_id FROM groups WHERE id = {p}", (page_id,))
    g_row = cursor.fetchone()
    if not g_row or g_row['user_id'] != uid:
        return jsonify({'success': False, 'message': 'Only the page creator can update page details.'}), 403

    data = request.json or {}
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    avatar_url = data.get('avatar_url', '').strip()
    cover_url = data.get('cover_url', '').strip()

    updates = []
    params = []
    if name:
        updates.append(f"name = {p}")
        params.append(name)
    if description is not None:
        updates.append(f"description = {p}")
        params.append(description)
    if avatar_url:
        updates.append(f"avatar_url = {p}")
        params.append(avatar_url)
    if cover_url:
        updates.append(f"cover_url = {p}")
        params.append(cover_url)

    if updates:
        params.append(page_id)
        cursor.execute(f"UPDATE groups SET {', '.join(updates)} WHERE id = {p}", tuple(params))
        db.commit()

    return jsonify({'success': True, 'message': 'Page updated successfully!'})

@app.route('/api/pages/<int:page_id>/join', methods=['POST'])
def join_page(page_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id FROM group_members WHERE group_id = {p} AND user_id = {p}", (page_id, uid))
    if cursor.fetchone():
        cursor.execute(f"DELETE FROM group_members WHERE group_id = {p} AND user_id = {p}", (page_id, uid))
        db.commit()
        return jsonify({'success': True, 'joined': False, 'message': 'Unfollowed page.'})
    else:
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (page_id, uid))
        db.commit()
        return jsonify({'success': True, 'joined': True, 'message': 'Following page!'})

# ======================================================================
# EVENTS API
# ======================================================================
@app.route('/api/events', methods=['GET', 'POST'])
def handle_events():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401
        data = request.json or {}
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        event_date = data.get('event_date', '').strip()
        location = data.get('location', '').strip()
        image_url = data.get('image_url', '').strip()
        group_id = int(data.get('group_id') or 0)

        if not title:
            return jsonify({'success': False, 'message': 'Event title is required.'}), 400

        cursor.execute(f'''
            INSERT INTO events (group_id, user_id, title, description, event_date, location, image_url)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
        ''', (group_id, session['user_id'], title, description, event_date, location, image_url))
        db.commit()
        return jsonify({'success': True, 'message': 'Event created successfully!'})

    group_id = int(request.args.get('group_id') or 0)
    if group_id > 0:
        cursor.execute(f'''
            SELECT e.*, u.full_name AS creator_name, u.username AS creator_username
            FROM events e JOIN users u ON e.user_id = u.id
            WHERE e.group_id = {p} ORDER BY e.id DESC
        ''', (group_id,))
    else:
        cursor.execute(f'''
            SELECT e.*, u.full_name AS creator_name, u.username AS creator_username, g.name AS group_name
            FROM events e JOIN users u ON e.user_id = u.id
            LEFT JOIN groups g ON e.group_id = g.id ORDER BY e.id DESC LIMIT 50
        ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

# ======================================================================
# CPN & PAYMENTS
# ======================================================================
@app.route('/api/cpn/claim-bank-transfer', methods=['POST'])
def claim_bank_transfer():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    note = data.get('reference_note', '').strip()
    if not note:
        return jsonify({'success': False, 'message': 'Please enter transfer reference note.'}), 400

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"INSERT INTO partner_requests (user_id, amount, reference_note) VALUES ({p}, 2000.0, {p})",
                   (session['user_id'], note))
    db.commit()
    logger.info(f"Payment claim submitted by user_id {session['user_id']}")
    return jsonify({'success': True, 'message': 'Payment claim submitted! Admin will verify and activate your CPN Partner status.'})

@app.route('/api/cpn/withdraw', methods=['POST'])
def request_payout():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0.0

    bank_name = data.get('bank_name', '').strip()
    account_number = data.get('account_number', '').strip()
    account_name = data.get('account_name', '').strip()

    if amount < 1000 or not bank_name or not account_number or not account_name:
        return jsonify({'success': False, 'message': 'Minimum payout is ₦1,000. All bank details required.'}), 400

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance - {p} WHERE id = {p} AND wallet_balance >= {p}",
                   (amount, uid, amount))
    if cursor.rowcount == 0:
        return jsonify({'success': False, 'message': 'Insufficient wallet balance.'}), 400

    cursor.execute(f'''INSERT INTO payout_requests (user_id, amount, bank_name, account_number, account_name)
    VALUES ({p}, {p}, {p}, {p}, {p})''', (uid, amount, bank_name, account_number, account_name))
    cursor.execute(f'''INSERT INTO transactions (user_id, amount, tx_type, description)
    VALUES ({p}, {p}, 'Bank Cashout Request', {p})''', (uid, amount, f"Cashout to {bank_name} ({account_number})"))
    db.commit()
    logger.info(f"Payout requested by user {uid} for amount {amount}")
    return jsonify({'success': True, 'message': 'Cashout request submitted!'})

# ======================================================================
# MULTI-PILLAR PRODUCTS API (2 FREE LISTINGS ENFORCEMENT)
# ======================================================================
@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401

        uid = session['user_id']
        cursor.execute(f"SELECT user_type FROM users WHERE id = {p}", (uid,))
        me = cursor.fetchone()
        user_type = me['user_type'] if me else 'Resident'

        if user_type == 'Resident':
            used_listings = count_user_listings(uid)
            if used_listings >= 2:
                return jsonify({
                    'success': False,
                    'message': 'You have used your 2 Free Trial Listings! Upgrade to CPN Partner (₦2,000) for unlimited directory listings and referral earnings.',
                    'requires_upgrade': True
                }), 403

        data = request.json or {}
        title = data.get('title', '').strip()
        category = data.get('category', 'General')
        listing_type = data.get('listing_type', 'Market')
        try:
            price = float(data.get('price', 0))
        except (ValueError, TypeError):
            price = 0.0

        description = data.get('description', '').strip()
        whatsapp = data.get('whatsapp_number', '').strip()
        image_url = data.get('image_url', '').strip()
        video_url = data.get('video_url', '').strip()

        if not title or not whatsapp:
            return jsonify({'success': False, 'message': 'Title and WhatsApp contact required.'}), 400

        cursor.execute(f'''INSERT INTO products (user_id, title, category, price, description, whatsapp_number, image_url, video_url, listing_type)
        VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})''',
        (uid, title, category, price, description, whatsapp, image_url, video_url, listing_type))
        db.commit()
        return jsonify({'success': True, 'message': f'Listing published on Ijebu {listing_type} Hub!'})

    q = request.args.get('q', '').strip().lower()
    listing_type = request.args.get('type', 'Market').strip()
    sql = f'''
        SELECT p.*, u.full_name AS seller_name, u.username AS seller_username, u.is_verified_merchant, u.user_type
        FROM products p JOIN users u ON p.user_id = u.id
        WHERE p.status = 'active' AND p.listing_type = {p}
    '''
    params = [listing_type]
    if q:
        sql += f" AND (LOWER(p.title) LIKE {p} OR LOWER(p.description) LIKE {p} OR LOWER(p.category) LIKE {p})"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    sql += ' ORDER BY p.id DESC'
    cursor.execute(sql, tuple(params))
    return jsonify([dict(r) for r in cursor.fetchall()])

# ======================================================================
# DATING API
# ======================================================================
@app.route('/api/dating/profile', methods=['POST'])
def update_dating_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    age = int(data.get('age', 18))
    gender = data.get('gender', 'Female')
    intent = data.get('relationship_intent', 'Dating')
    bio = data.get('bio', '').strip()
    occupation = data.get('occupation', '').strip()
    is_active = 1 if data.get('is_dating_active') else 0

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f'''UPDATE users SET age={p}, gender={p}, relationship_intent={p}, bio={p}, occupation={p}, is_dating_active={p}
    WHERE id={p}''', (age, gender, intent, bio, occupation, is_active, session['user_id']))
    db.commit()
    return jsonify({'success': True, 'message': 'Dating profile updated!'})

@app.route('/api/dating/matches', methods=['GET'])
def get_dating_matches():
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    current_uid = session.get('user_id') or 0
    sql = f'''
        SELECT id, full_name, username, user_type, age, gender, relationship_intent, bio, occupation, avatar_url, created_at
        FROM users WHERE is_dating_active = 1 AND id != {p} ORDER BY id DESC LIMIT 50
    '''
    cursor.execute(sql, (current_uid,))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/dating/wink', methods=['POST'])
def send_wink():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    receiver_id = data.get('receiver_id')
    if not receiver_id:
        return jsonify({'success': False, 'message': 'Invalid target.'}), 400

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    try:
        cursor.execute(f"INSERT INTO dating_winks (sender_id, receiver_id) VALUES ({p}, {p})", (uid, receiver_id))
        db.commit()
        add_notification(receiver_id, uid, 'wink', uid, f"{session['full_name']} sent you a Wink 👋 on Dating Match!")
        return jsonify({'success': True, 'message': 'Wink sent successfully!'})
    except Exception:
        return jsonify({'success': False, 'message': 'Already sent a wink to this member.'})

# ======================================================================
# SOCIAL FEED & PAGE FEED INTEGRATION
# ======================================================================
@app.route('/api/posts', methods=['GET', 'POST'])
def handle_posts():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401

        uid = session['user_id']
        cursor.execute(f"SELECT user_type FROM users WHERE id = {p}", (uid,))
        me = cursor.fetchone()
        user_type = me['user_type'] if me else 'Resident'

        data = request.json or {}
        content = (data.get('content') or '').strip()
        image_url = (data.get('image_url') or '').strip()
        video_url = (data.get('video_url') or '').strip()
        post_type = (data.get('post_type') or 'Social').strip()
        group_id = int(data.get('group_id') or 0)

        if not content and not image_url and not video_url:
            return jsonify({'success': False, 'message': 'Write something or attach image/video.'}), 400

        is_advert = '[PRODUCT_ADVERT]' in content or 'wa.me' in content.lower()
        if is_advert and user_type == 'Resident':
            used_listings = count_user_listings(uid)
            if used_listings >= 2:
                return jsonify({
                    'success': False,
                    'message': 'You have used your 2 Free Trial Advert Listings! Upgrade to CPN Partner (₦2,000) for unlimited advertisements.',
                    'requires_upgrade': True
                }), 403

        cursor.execute(
            f"INSERT INTO posts (user_id, group_id, content, image_url, video_url, post_type) VALUES ({p}, {p}, {p}, {p}, {p}, {p})",
            (uid, group_id, content, image_url, video_url, post_type)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Published successfully!'})

    current_uid = session.get('user_id') or 0
    post_type_filter = request.args.get('type', 'Social')
    group_filter = int(request.args.get('group_id') or 0)

    if group_filter > 0:
        cursor.execute(f'''
            SELECT p.id, p.user_id, p.group_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
            u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
            g.name AS group_name,
            (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
            (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
            CASE WHEN EXISTS (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN groups g ON p.group_id = g.id
            WHERE p.group_id = {p}
            ORDER BY p.id DESC LIMIT 60
        ''', (current_uid, group_filter))
    else:
        cursor.execute(f'''
            SELECT p.id, p.user_id, p.group_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
            u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
            g.name AS group_name,
            (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
            (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
            CASE WHEN EXISTS (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN groups g ON p.group_id = g.id
            WHERE p.post_type = {p}
            ORDER BY p.id DESC LIMIT 60
        ''', (current_uid, post_type_filter))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
def toggle_post_like(post_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT user_id FROM posts WHERE id = {p}", (post_id,))
    post = cursor.fetchone()

    cursor.execute(f"SELECT id FROM post_likes WHERE post_id = {p} AND user_id = {p}", (post_id, uid))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(f"DELETE FROM post_likes WHERE id = {p}", (existing['id'],))
        liked = False
    else:
        cursor.execute(f"INSERT INTO post_likes (post_id, user_id) VALUES ({p}, {p})", (post_id, uid))
        liked = True
        if post:
            add_notification(post['user_id'], uid, 'like', post_id, f"{session['full_name']} liked your post.")

    db.commit()
    cursor.execute(f"SELECT COUNT(*) FROM post_likes WHERE post_id = {p}", (post_id,))
    return jsonify({'success': True, 'liked': liked, 'likes_count': cursor.fetchone()[0]})

@app.route('/api/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def handle_comments(post_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401
        data = request.json or {}
        content = data.get('content', '').strip()
        parent_id = int(data.get('parent_id') or 0)

        if not content:
            return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400

        cursor.execute(f"INSERT INTO comments (post_id, user_id, parent_id, content) VALUES ({p}, {p}, {p}, {p})",
                       (post_id, session['user_id'], parent_id, content))
        db.commit()

        cursor.execute(f"SELECT user_id FROM posts WHERE id = {p}", (post_id,))
        post = cursor.fetchone()
        if post:
            add_notification(post['user_id'], session['user_id'], 'comment', post_id, f"{session['full_name']} commented on your post.")

        return jsonify({'success': True, 'message': 'Comment posted!'})

    uid = session.get('user_id') or 0
    cursor.execute(f'''
        SELECT c.*, u.full_name, u.username, u.avatar_url,
        pu.username AS parent_username,
        (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes_count,
        CASE WHEN EXISTS (SELECT 1 FROM comment_likes cl WHERE cl.comment_id = c.id AND cl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
        FROM comments c
        JOIN users u ON c.user_id = u.id
        LEFT JOIN comments pc ON c.parent_id = pc.id
        LEFT JOIN users pu ON pc.user_id = pu.id
        WHERE c.post_id = {p} ORDER BY c.id ASC
    ''', (uid, post_id))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/comments/<int:comment_id>/like', methods=['POST'])
def toggle_comment_like(comment_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id FROM comment_likes WHERE comment_id = {p} AND user_id = {p}", (comment_id, uid))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(f"DELETE FROM comment_likes WHERE id = {p}", (existing['id'],))
        liked = False
    else:
        cursor.execute(f"INSERT INTO comment_likes (comment_id, user_id) VALUES ({p}, {p})", (comment_id, uid))
        liked = True

    db.commit()
    cursor.execute(f"SELECT COUNT(*) FROM comment_likes WHERE comment_id = {p}", (comment_id,))
    return jsonify({'success': True, 'liked': liked, 'likes_count': cursor.fetchone()[0]})

# ======================================================================
# PUBLIC MEMBER PROFILE & WALL
# ======================================================================
@app.route('/api/users/<username>', methods=['GET'])
def get_user_profile(username):
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    cursor.execute(f'''
        SELECT id, full_name, phone, username, user_type, referral_code, wallet_balance, is_verified_merchant,
        age, gender, relationship_intent, bio, occupation, avatar_url, cover_url, created_at
        FROM users WHERE LOWER(username) = {p}
    ''', (username.lower(),))
    user = cursor.fetchone()
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    uid = user['id']
    current_uid = session.get('user_id') or 0

    cursor.execute(f"SELECT COUNT(*) FROM users WHERE referred_by = {p}", (user['referral_code'],))
    recruits_count = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM followers WHERE followed_id = {p}", (uid,))
    followers_count = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM followers WHERE follower_id = {p}", (uid,))
    following_count = cursor.fetchone()[0]

    is_following = False
    if current_uid:
        cursor.execute(f"SELECT 1 FROM followers WHERE follower_id = {p} AND followed_id = {p}", (current_uid, uid))
        is_following = cursor.fetchone() is not None

    cursor.execute(f'''
        SELECT p.id, p.user_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
        u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
        g.name AS group_name,
        (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
        CASE WHEN EXISTS (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p JOIN users u ON p.user_id = u.id
        LEFT JOIN groups g ON p.group_id = g.id
        WHERE p.user_id = {p} ORDER BY p.id DESC
    ''', (current_uid, uid))
    posts = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT * FROM products WHERE user_id = {p} AND status = 'active' ORDER BY id DESC", (uid,))
    products = [dict(r) for r in cursor.fetchall()]

    res = dict(user)
    res['wallet_balance'] = float(res.get('wallet_balance') or 0)
    res['recruits_count'] = recruits_count
    res['followers_count'] = followers_count
    res['following_count'] = following_count
    res['is_following'] = is_following
    res['posts'] = posts
    res['products'] = products
    res['posts_count'] = len(posts)
    res['products_count'] = len(products)
    res['listings_count'] = count_user_listings(uid)

    return jsonify({'success': True, 'user': res})

# ======================================================================
# CHAT API
# ======================================================================
def _is_blocked(cursor, p, a, b):
    cursor.execute(f"SELECT 1 FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}", (a, b))
    return cursor.fetchone() is not None

@app.route('/api/chat/unread', methods=['GET'])
def chat_unread():
    if 'user_id' not in session:
        return jsonify({'success': True, 'count': 0})
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT COUNT(*) FROM messages WHERE receiver_id = {p} AND is_read = 0", (session['user_id'],))
    return jsonify({'success': True, 'count': cursor.fetchone()[0]})

@app.route('/api/chat/partners', methods=['GET'])
def chat_partners():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f'''
        SELECT CASE WHEN sender_id = {p} THEN receiver_id ELSE sender_id END AS other_id, MAX(id) AS last_id
        FROM messages WHERE sender_id = {p} OR receiver_id = {p}
        GROUP BY CASE WHEN sender_id = {p} THEN receiver_id ELSE sender_id END
        ORDER BY last_id DESC
    ''', (uid, uid, uid, uid))

    partners = []
    for row in cursor.fetchall():
        other_id = row['other_id']
        last_id = row['last_id']

        cursor.execute(f"SELECT id, full_name, username, user_type, avatar_url FROM users WHERE id = {p}", (other_id,))
        u = cursor.fetchone()
        if not u:
            continue

        cursor.execute(f"SELECT content, sender_id, created_at FROM messages WHERE id = {p}", (last_id,))
        m = cursor.fetchone()

        cursor.execute(f"SELECT COUNT(*) FROM messages WHERE sender_id = {p} AND receiver_id = {p} AND is_read = 0", (other_id, uid))
        unread = cursor.fetchone()[0]

        partners.append({
            'user': dict(u),
            'last_message': (m['content'] if m else '')[:60],
            'last_from_me': (m['sender_id'] == uid) if m else False,
            'last_time': str(m['created_at']) if m else '',
            'unread': unread
        })

    return jsonify({'success': True, 'partners': partners})

@app.route('/api/chat/<username>', methods=['GET', 'POST'])
def chat_thread(username):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id, full_name, username, user_type, avatar_url FROM users WHERE LOWER(username) = {p}", (username.lower(),))
    other = cursor.fetchone()
    if not other:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
    other_id = other['id']

    if request.method == 'POST':
        data = request.json or {}
        content = (data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400

        if _is_blocked(cursor, p, uid, other_id) or _is_blocked(cursor, p, other_id, uid):
            return jsonify({'success': False, 'message': 'Cannot send message.'}), 403

        cursor.execute(f"INSERT INTO messages (sender_id, receiver_id, content) VALUES ({p}, {p}, {p})", (uid, other_id, content))
        db.commit()
        return jsonify({'success': True, 'message': 'Sent.'})

    cursor.execute(f"UPDATE messages SET is_read = 1 WHERE sender_id = {p} AND receiver_id = {p}", (other_id, uid))
    db.commit()

    cursor.execute(f'''
        SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_read, m.created_at,
        u.full_name, u.username
        FROM messages m JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id = {p} AND m.receiver_id = {p}) OR (m.sender_id = {p} AND m.receiver_id = {p})
        ORDER BY m.id ASC LIMIT 300
    ''', (uid, other_id, other_id, uid))
    messages = [dict(r) for r in cursor.fetchall()]

    return jsonify({'success': True, 'other': dict(other), 'messages': messages, 'me_id': uid})

# ======================================================================
# ROBUST ADMIN API (MONITOR ALL INCOME & WALLETS)
# ======================================================================
@app.route('/api/admin/overview', methods=['GET'])
def get_admin_overview():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE user_type = 'CPN Partner'")
    total_partners = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM products")
    total_products = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM posts")
    total_posts = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM groups")
    total_pages = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(wallet_balance), 0) FROM users")
    total_wallets = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM partner_requests WHERE status = 'pending'")
    pending_partners = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM partner_requests WHERE status = 'approved'")
    total_gross_income = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payout_requests WHERE status = 'approved'")
    total_approved_payouts = float(cursor.fetchone()[0] or 0)

    admin_net_balance = total_gross_income - total_approved_payouts

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_products': total_products,
        'total_posts': total_posts,
        'total_pages': total_pages,
        'total_partner_wallets': total_wallets,
        'pending_partners': pending_partners,
        'total_gross_income': total_gross_income,
        'total_approved_payouts': total_approved_payouts,
        'admin_net_balance': admin_net_balance
    })

@app.route('/api/admin/posts', methods=['GET', 'DELETE'])
def admin_manage_posts():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'DELETE':
        post_id = request.args.get('post_id')
        if not post_id:
            return jsonify({'success': False, 'message': 'Post ID required.'}), 400
        cursor.execute(f"DELETE FROM comments WHERE post_id = {p}", (post_id,))
        cursor.execute(f"DELETE FROM post_likes WHERE post_id = {p}", (post_id,))
        cursor.execute(f"DELETE FROM posts WHERE id = {p}", (post_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Post deleted successfully.'})

    cursor.execute('''
        SELECT p.id, p.content, p.post_type, p.image_url, p.created_at,
        u.full_name, u.username, g.name AS group_name
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN groups g ON p.group_id = g.id
        ORDER BY p.id DESC LIMIT 100
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/users', methods=['GET', 'DELETE'])
def admin_manage_users():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'DELETE':
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'message': 'User ID required.'}), 400
        cursor.execute(f"DELETE FROM comments WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM post_likes WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM posts WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM products WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM messages WHERE sender_id = {p} OR receiver_id = {p}", (user_id, user_id))
        cursor.execute(f"DELETE FROM followers WHERE follower_id = {p} OR followed_id = {p}", (user_id, user_id))
        cursor.execute(f"DELETE FROM users WHERE id = {p}", (user_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Member removed.'})

    cursor.execute("SELECT id, full_name, username, phone, user_type, wallet_balance FROM users ORDER BY id DESC")
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/partner-requests', methods=['GET', 'POST'])
def admin_partner_requests():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        data = request.json or {}
        req_id = data.get('request_id')
        action = data.get('action')

        cursor.execute(f"SELECT user_id FROM partner_requests WHERE id = {p}", (req_id,))
        req = cursor.fetchone()
        if not req:
            return jsonify({'success': False, 'message': 'Request not found.'}), 404

        uid = req['user_id']
        if action == 'approve':
            cursor.execute(f"UPDATE users SET user_type = 'CPN Partner', is_verified_merchant = 1 WHERE id = {p}", (uid,))
            cursor.execute(f"UPDATE partner_requests SET status = 'approved' WHERE id = {p}", (req_id,))
            db.commit()
            process_cpn_commission(uid, upgrade_fee=2000.0)
            add_notification(uid, 0, 'system', 0, "Congratulations! Your CPN Partner upgrade has been approved!")
            return jsonify({'success': True, 'message': 'Member approved as CPN Partner!'})
        else:
            cursor.execute(f"UPDATE partner_requests SET status = 'rejected' WHERE id = {p}", (req_id,))
            db.commit()
            return jsonify({'success': True, 'message': 'Partner claim rejected.'})

    cursor.execute('''SELECT pr.*, u.full_name, u.phone, u.username FROM partner_requests pr JOIN users u ON pr.user_id = u.id ORDER BY pr.id DESC''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/payouts', methods=['GET', 'POST'])
def manage_payouts():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        data = request.json or {}
        payout_id = data.get('payout_id')
        new_status = data.get('status', 'approved')

        if new_status == 'rejected':
            cursor.execute(f"SELECT user_id, amount FROM payout_requests WHERE id = {p}", (payout_id,))
            req = cursor.fetchone()
            if req:
                cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}",
                               (float(req['amount'] or 0), req['user_id']))

        cursor.execute(f"UPDATE payout_requests SET status = {p} WHERE id = {p}", (new_status, payout_id))
        db.commit()
        return jsonify({'success': True, 'message': f'Payout marked as {new_status}.'})

    cursor.execute('''SELECT pr.*, u.full_name, u.phone, u.username FROM payout_requests pr JOIN users u ON pr.user_id = u.id ORDER BY pr.id DESC''')
    return jsonify([dict(r) for r in cursor.fetchall()])

# ======================================================================
# FRONTEND TEMPLATES
# ======================================================================
INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{{ meta_title }}</title>
<meta name="description" content="{{ meta_desc }}">

<!-- Open Graph Preview -->
<meta property="og:site_name" content="Ijebu Connect">
<meta property="og:title" content="{{ meta_title }}">
<meta property="og:description" content="{{ meta_desc }}">
<meta property="og:image" content="{{ meta_image }}">
<meta property="og:url" content="{{ meta_url }}">
<meta property="og:type" content="website">

<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root {
  --fb-blue: #1877f2;
  --navy-blue: #0b1e36;
  --emerald-green: #059669;
  --bg-body: #f0f2f5;
  --border-light: #ced0d4;
  --text-dark: #050505;
  --text-muted: #65676b;
}

* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color:transparent; }
body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; padding-bottom: 70px; }

#toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; }
.toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; }

header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-title { font-size: 1.15rem; font-weight: 800; color: var(--navy-blue); cursor: pointer; }
.brand-title span { color: var(--fb-blue); }

.top-nav-pills { display: flex; gap: 6px; padding: 0.6rem 0.5rem; background: #fff; border-bottom: 1px solid var(--border-light); overflow-x: auto; scrollbar-width: none; }
.top-nav-pills::-webkit-scrollbar { display: none; }
.nav-pill { padding: 6px 14px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; background: #f0f2f5; color: var(--text-muted); cursor: pointer; flex-shrink: 0; }
.nav-pill.active { background: var(--fb-blue); color: #fff; }

.app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.75rem; flex: 1; }
.view-section { display: none; }
.view-section.active { display: block; }
.card { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 1rem; margin-bottom: 0.85rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

/* FACEBOOK STYLE COVER BANNERS */
.fb-group-banner { height: 160px; background: linear-gradient(135deg, #1877f2, #0b1e36); border-radius: 12px 12px 0 0; position: relative; margin: -1rem -1rem 45px -1rem; background-size: cover; background-position: center; }
.fb-group-avatar { position: absolute; bottom: -35px; left: 16px; width: 75px; height: 75px; border-radius: 16px; border: 4px solid #fff; background: var(--fb-blue); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; }

.btn-group-edit {
  background: #f0f2f5;
  border: 1.5px solid var(--border-light);
  padding: 10px 18px;
  border-radius: 10px;
  font-weight: 800;
  font-size: 0.92rem;
  color: var(--navy-blue);
  cursor: pointer;
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 8px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.08);
}

.feed-post { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 0.88rem; margin-bottom: 0.85rem; }
.post-header { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; flex-shrink: 0; background-size: cover; background-position: center; }
.post-actions { display: flex; gap: 6px; padding-top: 8px; margin-top: 8px; border-top: 1px solid var(--border-light); }
.post-action-btn { flex: 1; background: none; border: none; padding: 8px; border-radius: 6px; font-size: 0.8rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 4px; }
.post-action-btn:hover { background: #f0f2f5; }

.group-badge { background: #e7f3ff; color: var(--fb-blue); font-size: 0.72rem; font-weight: 800; padding: 2px 8px; border-radius: 10px; margin-left: auto; cursor: pointer; }
.comments-box { background: #f8fafc; border-radius: 8px; padding: 8px; margin-top: 8px; }
.comment-item { border-bottom: 1px solid #e2e8f0; padding: 6px 0; font-size: 0.82rem; }
.comment-reply-item { margin-left: 18px; padding-left: 8px; border-left: 2px solid var(--fb-blue); }

.btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; min-height: 42px; }
.form-control { padding: 10px 12px; border-radius: 8px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; background: #fff; }

.mobile-bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1px solid var(--border-light); display: flex; justify-content: space-around; padding: 6px 0; z-index: 1000; height: 60px; }
.nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.7rem; font-weight: 700; flex: 1; cursor: pointer; text-decoration: none; }
.nav-item.active { color: var(--fb-blue); }

.app-footer { background: #fff; border-top: 1px solid var(--border-light); padding: 1.2rem; text-align: center; font-size: 0.78rem; color: var(--text-muted); margin-top: 2rem; }
.app-footer a { color: var(--fb-blue); text-decoration: none; font-weight: 700; }
</style>
<script>
window.INITIAL_DEEP_LINK_DATA = {{ deep_link_json | safe }};
</script>
</head>
<body>
<div id="toast-container"></div>

<header>
  <div class="brand-title" onclick="switchNav('feed')">IJEBU <span>CONNECT</span></div>
  <div id="header-auth"></div>
</header>

<div class="top-nav-pills">
  <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Main Feed</div>
  <div class="nav-pill" data-nav="pages" onclick="switchNav('pages')"><i class="fa-solid fa-flag"></i> Pages</div>
  <div class="nav-pill" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days"></i> Events</div>
  <div class="nav-pill" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-pill" data-nav="beauty" onclick="switchNav('beauty')"><i class="fa-solid fa-scissors"></i> Beauty</div>
  <div class="nav-pill" data-nav="jobs" onclick="switchNav('jobs')"><i class="fa-solid fa-briefcase"></i> Jobs</div>
  <div class="nav-pill" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
  <div class="nav-pill" id="admin-pill" style="display:none;" onclick="window.location.href='/admin'"><i class="fa-solid fa-gear"></i> Admin Panel</div>
</div>

<div class="app-container">
  <!-- MAIN FEED -->
  <div id="view-feed" class="view-section active">
    <div class="card">
      <form onsubmit="handlePostSubmit(event, 'Social')">
        <textarea class="form-control" id="post-content" rows="2" placeholder="What's happening in Ijebu today?"></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Publish Update</button>
      </form>
    </div>
    <div id="feed-posts-container"></div>
  </div>

  <!-- PAGES HUB -->
  <div id="view-pages" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:1.1rem;font-weight:800;color:var(--navy-blue);">Facebook Pages</h3>
      <button onclick="openPageCreateModal()" class="btn-submit" style="width:auto;padding:8px 16px;">+ Create Page</button>
    </div>
    <div id="pages-container"></div>
  </div>

  <!-- PAGE DETAIL VIEW -->
  <div id="view-page-detail" class="view-section">
    <button onclick="switchNav('pages')" style="background:#fff;border:1px solid var(--border-light);padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back to Pages</button>
    <div id="page-detail-header" class="card"></div>
    
    <div class="card" id="page-post-composer" style="display:none;">
      <h4 style="font-size:0.88rem; font-weight:800; margin-bottom:6px;">Post to Page (Shows on Main Feed too)</h4>
      <form onsubmit="handlePagePostSubmit(event)">
        <input type="hidden" id="active-page-id" value="0">
        <textarea class="form-control" id="page-post-content" rows="2" placeholder="Write an update on this Page..."></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="page-post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Publish Page Post</button>
      </form>
    </div>
    
    <div id="page-posts-container"></div>
  </div>

  <!-- EVENTS VIEW -->
  <div id="view-events" class="view-section">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h3 style="font-size:1rem;font-weight:800;">📅 Events & Festivals</h3>
        <button onclick="openCreateEventModal()" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.78rem;">+ Create Event</button>
      </div>
    </div>
    <div id="events-feed-container"></div>
  </div>

  <!-- MARKETPLACE -->
  <div id="view-market" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;">Marketplace</h3>
      <button onclick="startSellItem('Market')" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.78rem;">+ List Item</button>
    </div>
    <div id="products-container" class="card"></div>
  </div>

  <!-- BEAUTY & FASHION -->
  <div id="view-beauty" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;">Beauty & Fashion</h3>
      <button onclick="startSellItem('Beauty')" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.78rem;">+ Add Service</button>
    </div>
    <div id="beauty-container" class="card"></div>
  </div>

  <!-- JOBS -->
  <div id="view-jobs" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;">Jobs & Artisans</h3>
      <button onclick="startSellItem('Jobs')" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.78rem;">+ Post Skill</button>
    </div>
    <div id="jobs-container" class="card"></div>
  </div>

  <!-- DATING -->
  <div id="view-dating" class="view-section">
    <div class="card" style="background:linear-gradient(135deg, #4f46e5, #7c3aed);color:#fff;">
      <h3 style="font-weight:800;margin-bottom:4px;">❤️ Ijebu Singles Match</h3>
      <p style="font-size:0.78rem;opacity:0.9;margin-bottom:8px;">Connect with verified singles.</p>
      <button onclick="openDatingSettingsModal()" style="background:#fff;color:#4f46e5;border:none;padding:6px 12px;border-radius:8px;font-weight:800;font-size:0.75rem;">Set Up Dating Profile</button>
    </div>
    <div id="dating-matches-container"></div>
  </div>

  <!-- CHAT -->
  <div id="view-chat" class="view-section">
    <div id="chat-list-wrap">
      <h3 style="font-size:1rem;font-weight:800;margin-bottom:8px;">Messages</h3>
      <div id="chat-partners-container"></div>
    </div>
    <div id="chat-thread-wrap" style="display:none;">
      <button onclick="closeChatThread()" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-size:0.75rem;font-weight:700;margin-bottom:8px;">← Back to Chat</button>
      <div id="chat-thread-header" class="card" style="padding:0.5rem 0.88rem;"></div>
      <div id="chat-messages" style="min-height:220px;max-height:50vh;overflow-y:auto;padding:6px 0;"></div>
      <form onsubmit="sendChatMessage(event)" style="position:sticky;bottom:0;background:var(--bg-body);padding:6px 0;">
        <div style="display:flex;gap:6px;">
          <input type="text" id="chat-input" class="form-control" placeholder="Type a message..." style="flex:1;">
          <button type="submit" class="btn-submit" style="width:auto;padding:10px 16px;">Send</button>
        </div>
      </form>
    </div>
  </div>

  <!-- PUBLIC MEMBER PROFILE VIEW -->
  <div id="view-profile" class="view-section">
    <button onclick="switchNav('feed')" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back</button>
    <div id="profile-wall-container"></div>
  </div>
</div>

<!-- MULTI-PURPOSE SELL / LISTING MODAL -->
<div id="sell-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:440px;width:100%;max-height:90vh;overflow-y:auto;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);" id="modal-sell-title">Publish Listing</h3>
      <button onclick="closeSellModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleProductSubmit(event)">
      <input type="hidden" id="prod-type" value="Market">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Title</label><input type="text" class="form-control" id="prod-title" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Category</label><input type="text" class="form-control" id="prod-category" placeholder="e.g. Electronics, Fashion, Artisans" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Price (₦)</label><input type="number" class="form-control" id="prod-price" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">WhatsApp Contact</label><input type="text" class="form-control" id="prod-whatsapp" placeholder="e.g. 09018363715" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Upload Photo/Video</label><input type="file" id="prod-img-file" class="form-control" accept="image/*,video/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Description</label><textarea class="form-control" id="prod-desc" rows="2"></textarea></div>
      <button type="submit" class="btn-submit">Publish Item</button>
    </form>
  </div>
</div>

<!-- EVENT MODAL -->
<div id="event-create-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Create Event</h3>
      <button onclick="closeEventModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleEventSubmit(event)">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Event Title</label><input type="text" id="evt-title" class="form-control" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Date & Time</label><input type="text" id="evt-date" class="form-control" placeholder="e.g. Saturday, Oct 25 at 4:00 PM"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Location</label><input type="text" id="evt-location" class="form-control" placeholder="e.g. Ijebu Imusin"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Event Banner</label><input type="file" id="evt-image-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Description</label><textarea id="evt-desc" class="form-control" rows="2"></textarea></div>
      <button type="submit" class="btn-submit">Publish Event</button>
    </form>
  </div>
</div>

<!-- DATING SETTINGS MODAL -->
<div id="dating-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:420px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">❤️ Dating Profile Settings</h3>
      <button onclick="closeDatingModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleDatingProfileSubmit(event)">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Age</label><input type="number" id="dt-age" class="form-control" value="24" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Gender</label><select id="dt-gender" class="form-control"><option value="Female">Female</option><option value="Male">Male</option></select></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Looking For</label><select id="dt-intent" class="form-control"><option value="Dating & Relationship">Dating & Relationship</option><option value="Marriage">Marriage</option><option value="Networking & Friends">Networking & Friends</option></select></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Occupation</label><input type="text" id="dt-occupation" class="form-control"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Bio</label><textarea id="dt-bio" class="form-control" rows="2"></textarea></div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:10px;"><input type="checkbox" id="dt-active" checked><label for="dt-active" style="font-size:0.8rem;">Show on Dating Feed</label></div>
      <button type="submit" class="btn-submit">Save Dating Profile</button>
    </form>
  </div>
</div>

<!-- EDIT PROFILE MODAL -->
<div id="edit-profile-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:440px;width:100%;max-height:90vh;overflow-y:auto;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Edit Member Profile</h3>
      <button onclick="closeEditProfileModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleProfileUpdateSubmit(event)">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Full Name</label><input type="text" id="edit-fullname" class="form-control" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Phone Number</label><input type="tel" id="edit-phone" class="form-control" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Occupation</label><input type="text" id="edit-occupation" class="form-control"></div>
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <div style="flex:1;"><label style="font-size:0.8rem;font-weight:700;">Age</label><input type="number" id="edit-age" class="form-control"></div>
        <div style="flex:1;"><label style="font-size:0.8rem;font-weight:700;">Gender</label>
          <select id="edit-gender" class="form-control"><option value="Male">Male</option><option value="Female">Female</option></select>
        </div>
      </div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Bio / About</label><textarea id="edit-bio-text" class="form-control" rows="2"></textarea></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Profile Picture (Avatar)</label><input type="file" id="edit-avatar-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Cover Photo Banner</label><input type="file" id="edit-cover-file" class="form-control" accept="image/*"></div>
      <button type="submit" class="btn-submit">Save Profile Changes</button>
    </form>
  </div>
</div>

<!-- CPN UPGRADE MODAL -->
<div id="cpn-upgrade-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:420px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Upgrade to CPN Partner (₦2,000)</h3>
      <button onclick="closeCPNModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <p style="font-size:0.82rem;color:var(--text-muted);margin-bottom:10px;">Unlock unlimited marketplace listings and earn <strong>10% Tier-1 & 5% Tier-2 referral rewards</strong>!</p>
    <div style="background:#f1f5f9;padding:10px;border-radius:8px;font-size:0.82rem;margin-bottom:10px;border:1px dashed var(--navy-blue);">
      <strong>🏦 Bank Transfer Details:</strong><br>
      Bank: <b>OPay</b><br>
      Account Number: <b style="color:var(--emerald-green);font-size:0.95rem;">09018363715</b><br>
      Account Name: <b>Rotimi Williams Oladele</b><br>
      Fee: <b>₦2,000</b>
    </div>
    <form onsubmit="handleClaimBankTransfer(event)">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Sender Name / Reference Note</label><input type="text" id="cpn-ref-note" class="form-control" placeholder="e.g. Paid via OPay / John Doe" required></div>
      <button type="submit" class="btn-submit">Submit Payment Claim</button>
    </form>
  </div>
</div>

<!-- CREATE/EDIT PAGE MODAL -->
<div id="page-create-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;" id="page-modal-title">Create Page</h3>
      <button onclick="closePageModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handlePageSubmit(event)">
      <input type="hidden" id="edit-page-id" value="0">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Page Name</label><input type="text" id="page-name" class="form-control" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Page Profile Photo (Avatar)</label><input type="file" id="page-avatar-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Page Cover Banner</label><input type="file" id="page-cover-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Description</label><textarea id="page-desc" class="form-control" rows="2"></textarea></div>
      <button type="submit" class="btn-submit">Save Page Details</button>
    </form>
  </div>
</div>

<footer class="app-footer">
  <p><strong>{{ company_name }}</strong> &copy; 2026. All Rights Reserved.</p>
  <p><i class="fa-solid fa-phone"></i> Phone: <strong>09018363715</strong> | <i class="fa-solid fa-envelope"></i> Email: <a href="mailto:{{ contact_email }}">{{ contact_email }}</a></p>
</footer>

<div class="mobile-bottom-nav">
  <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Feed</div>
  <div class="nav-item" data-nav="pages" onclick="switchNav('pages')"><i class="fa-solid fa-flag"></i> Pages</div>
  <div class="nav-item" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-item" data-nav="chat" onclick="switchNav('chat')"><i class="fa-solid fa-comments"></i> Chat</div>
</div>

<script>
let currentUser = null;
let activePageId = 0;
let replyParentCommentId = 0;

function showToast(msg, type = 'success') {
  const box = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerText = msg;
  box.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function switchNav(target) {
  document.querySelectorAll('.nav-pill').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));

  const pill = document.querySelector(`.nav-pill[data-nav="${target}"]`);
  if(pill) pill.classList.add('active');

  const view = document.getElementById(`view-${target}`);
  if(view) view.classList.add('active');

  if(target === 'feed') loadPosts('Social', 'feed-posts-container');
  if(target === 'pages') loadPages();
  if(target === 'events') loadEventsFeed();
  if(target === 'market') loadCategoryListings('Market', 'products-container');
  if(target === 'beauty') loadCategoryListings('Beauty', 'beauty-container');
  if(target === 'jobs') loadCategoryListings('Jobs', 'jobs-container');
  if(target === 'chat') loadChatPartners();
  if(target === 'dating') loadDatingMatches();
}

async function checkSession() {
  try {
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if(data.logged_in) {
      currentUser = data.user;
      renderHeaderAuth();
      if(currentUser.user_type === 'Admin') document.getElementById('admin-pill').style.display = 'flex';
    } else {
      currentUser = null;
      renderHeaderAuth();
      window.location.href = '/auth'; // ALWAYS LOAD AUTH FIRST IF NOT LOGGED IN
    }
  } catch(e){}
}

function renderHeaderAuth() {
  const box = document.getElementById('header-auth');
  if(currentUser) {
    box.innerHTML = `<b style="font-size:0.82rem;cursor:pointer;" onclick="openProfile('${currentUser.username}')">@${currentUser.username}</b>`;
  } else {
    box.innerHTML = `<a href="/auth" style="background:var(--fb-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
  }
}

function startSellItem(type = 'Market') {
  if(!currentUser) return window.location.href = '/auth';
  document.getElementById('prod-type').value = type;
  document.getElementById('modal-sell-title').innerText = type === 'Market' ? 'Publish Marketplace Item' : (type === 'Beauty' ? 'Add Beauty & Fashion Service' : 'Post Job / Skill Listing');
  document.getElementById('sell-modal').style.display = 'flex';
}

function closeSellModal() { document.getElementById('sell-modal').style.display = 'none'; }

async function handleProductSubmit(e) {
  e.preventDefault();
  const type = document.getElementById('prod-type').value;
  const fileInput = document.getElementById('prod-img-file');
  let uploadedImg = '', uploadedVid = '';
  if(fileInput && fileInput.files[0]) {
    const upload = await uploadSelectedFile(fileInput);
    if(upload.is_video) uploadedVid = upload.url;
    else uploadedImg = upload.url;
  }

  const res = await fetch('/api/products', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      title: document.getElementById('prod-title').value,
      category: document.getElementById('prod-category').value,
      price: document.getElementById('prod-price').value,
      whatsapp_number: document.getElementById('prod-whatsapp').value,
      description: document.getElementById('prod-desc').value,
      listing_type: type,
      image_url: uploadedImg,
      video_url: uploadedVid
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closeSellModal();
    if(type === 'Market') loadCategoryListings('Market', 'products-container');
    if(type === 'Beauty') loadCategoryListings('Beauty', 'beauty-container');
    if(type === 'Jobs') loadCategoryListings('Jobs', 'jobs-container');
  } else if(data.requires_upgrade) {
    closeSellModal();
    document.getElementById('cpn-upgrade-modal').style.display = 'flex';
  }
}

async function loadCategoryListings(type, containerId) {
  const res = await fetch(`/api/products?type=${type}`);
  const items = await res.json();
  const c = document.getElementById(containerId);
  if(!items.length) { c.innerHTML = '<div style="text-align:center;padding:1rem;">No listings found.</div>'; return; }
  c.innerHTML = items.map(p => `
    <div style="border-bottom:1px solid var(--border-light);padding-bottom:10px;margin-bottom:10px;display:flex;gap:10px;align-items:center;">
      ${p.image_url ? `<img src="${p.image_url}" style="width:70px;height:70px;border-radius:8px;object-fit:cover;">` : `<div style="width:70px;height:70px;background:#f0f2f5;border-radius:8px;display:flex;align-items:center;justify-content:center;"><i class="fa-solid fa-store"></i></div>`}
      <div style="flex:1;">
        <h4 style="font-weight:800;font-size:0.9rem;">${p.title}</h4>
        <div style="color:var(--emerald-green);font-weight:800;font-size:0.85rem;">₦${p.price.toLocaleString()}</div>
        <div style="font-size:0.72rem;color:var(--text-muted);">${p.category} • @${p.seller_username}</div>
      </div>
      <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" style="background:#25d366;color:#fff;padding:6px 10px;border-radius:6px;font-size:0.75rem;text-decoration:none;font-weight:700;"><i class="fa-brands fa-whatsapp"></i> Chat</a>
    </div>
  `).join('');
}

function openCreateEventModal() { document.getElementById('event-create-modal').style.display = 'flex'; }
function closeEventModal() { document.getElementById('event-create-modal').style.display = 'none'; }

async function handleEventSubmit(e) {
  e.preventDefault();
  const imgInput = document.getElementById('evt-image-file');
  let imgUrl = '';
  if(imgInput && imgInput.files[0]) imgUrl = (await uploadSelectedFile(imgInput)).url;

  const res = await fetch('/api/events', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      title: document.getElementById('evt-title').value,
      event_date: document.getElementById('evt-date').value,
      location: document.getElementById('evt-location').value,
      description: document.getElementById('evt-desc').value,
      image_url: imgUrl,
      group_id: activePageId
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closeEventModal();
    loadEventsFeed();
  }
}

async function loadEventsFeed() {
  const res = await fetch('/api/events');
  const events = await res.json();
  const c = document.getElementById('events-feed-container');
  if(!events.length) { c.innerHTML = '<div class="card">No events listed right now.</div>'; return; }
  c.innerHTML = events.map(e => `
    <div class="card">
      ${e.image_url ? `<img src="${e.image_url}" style="width:100%;border-radius:8px;max-height:180px;object-fit:cover;margin-bottom:8px;">` : ''}
      <h4 style="font-weight:800;font-size:0.98rem;color:var(--navy-blue);">${e.title}</h4>
      <p style="font-size:0.78rem;color:var(--emerald-green);font-weight:700;">📅 ${e.event_date || 'Upcoming'} • 📍 ${e.location || 'Ijebu'}</p>
      <p style="font-size:0.82rem;margin-top:4px;">${e.description}</p>
    </div>
  `).join('');
}

function openDatingSettingsModal() {
  if(!currentUser) return window.location.href = '/auth';
  document.getElementById('dating-modal').style.display = 'flex';
}
function closeDatingModal() { document.getElementById('dating-modal').style.display = 'none'; }

async function handleDatingProfileSubmit(e) {
  e.preventDefault();
  const res = await fetch('/api/dating/profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      age: document.getElementById('dt-age').value,
      gender: document.getElementById('dt-gender').value,
      relationship_intent: document.getElementById('dt-intent').value,
      occupation: document.getElementById('dt-occupation').value,
      bio: document.getElementById('dt-bio').value,
      is_dating_active: document.getElementById('dt-active').checked
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) { closeDatingModal(); loadDatingMatches(); }
}

async function loadDatingMatches() {
  const res = await fetch('/api/dating/matches');
  const matches = await res.json();
  const container = document.getElementById('dating-matches-container');
  if(!matches.length) { container.innerHTML = '<div class="card">No active singles on feed yet.</div>'; return; }
  container.innerHTML = matches.map(m => `
    <div class="card" style="display:flex;gap:10px;align-items:center;">
      <div class="avatar" style="width:48px;height:48px;background:var(--fb-blue);">${m.avatar_url ? `<img src="${m.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : m.full_name.charAt(0)}</div>
      <div style="flex:1;">
        <h4 style="font-weight:800;font-size:0.88rem;">${m.full_name}, ${m.age}</h4>
        <div style="font-size:0.72rem;color:var(--emerald-green);font-weight:700;">${m.relationship_intent} • ${m.gender}</div>
        <div style="font-size:0.78rem;color:var(--text-muted);">"${m.bio || 'Living in Ijebu'}"</div>
      </div>
      <button onclick="sendWink(${m.id})" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;">Wink 👋</button>
    </div>
  `).join('');
}

async function sendWink(receiverId) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch('/api/dating/wink', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({receiver_id: receiverId})
  });
  const data = await res.json();
  showToast(data.message);
}

async function openProfile(username) {
  const res = await fetch(`/api/users/${encodeURIComponent(username)}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');
  const u = data.user;
  const isSelf = currentUser && currentUser.id === u.id;
  const coverBg = u.cover_url ? `style="background-image:url('${u.cover_url}')"` : '';
  
  let walletBlock = '';
  if(isSelf && (u.user_type === 'CPN Partner' || u.user_type === 'Admin')) {
    walletBlock = `
      <div class="card" style="background:linear-gradient(135deg, #0b1e36, #1e3a8a);color:#fff;">
        <div style="font-size:0.85rem;">Wallet Balance: <b style="color:#f59e0b;font-size:1.1rem;">₦${u.wallet_balance.toLocaleString()}</b></div>
        <div style="font-size:0.75rem;margin:4px 0;">Referral Code: <b>${u.referral_code}</b> | Recruits: <b>${u.recruits_count}</b></div>
      </div>
    `;
  }
  const postsHtml = u.posts.length ? u.posts.map(p => renderPostCard(p)).join('') : '<div class="card" style="text-align:center;">No wall updates yet.</div>';

  document.getElementById('profile-wall-container').innerHTML = `
    <div class="card">
      <div class="fb-group-banner" ${coverBg}>
        <div class="fb-group-avatar">${u.avatar_url ? `<img src="${u.avatar_url}" style="width:100%;height:100%;object-fit:cover;">` : u.full_name.charAt(0)}</div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:10px;">
        <div>
          <h2 style="font-size:1.1rem;font-weight:800;">${u.full_name} <span style="font-size:0.65rem;background:#fef3c7;color:#92400e;padding:2px 6px;border-radius:6px;">${u.user_type}</span></h2>
          <p style="font-size:0.75rem;color:var(--text-muted);">@${u.username} • <b>${u.followers_count}</b> Followers</p>
        </div>
      </div>
      <p style="font-size:0.82rem;margin:8px 0;">💼 ${u.occupation || 'Member'} | 📱 ${u.phone || ''}</p>
      <p style="font-size:0.82rem;color:var(--text-muted);">${u.bio || 'Resident of Ijebu'}</p>
      <div style="display:flex;gap:6px;margin-top:10px;">
        ${isSelf ? `<button onclick="openEditProfileModal()" class="btn-submit" style="font-size:0.78rem;">✏️ Edit Profile Details</button>` : `
          <button onclick="toggleFollow('${u.username}')" class="btn-submit" style="font-size:0.78rem;">${u.is_following ? 'Unfollow' : 'Follow'}</button>
        `}
      </div>
    </div>
    ${walletBlock}
    <h4 style="font-size:0.9rem;margin:12px 0 6px;">Profile Wall Updates</h4>
    ${postsHtml}
  `;
  switchNav('profile');
}

function openEditProfileModal() {
  if(!currentUser) return window.location.href = '/auth';
  document.getElementById('edit-fullname').value = currentUser.full_name || '';
  document.getElementById('edit-phone').value = currentUser.phone || '';
  document.getElementById('edit-occupation').value = currentUser.occupation || '';
  document.getElementById('edit-age').value = currentUser.age || 18;
  document.getElementById('edit-gender').value = currentUser.gender || 'Male';
  document.getElementById('edit-bio-text').value = currentUser.bio || '';
  document.getElementById('edit-profile-modal').style.display = 'flex';
}

function closeEditProfileModal() { document.getElementById('edit-profile-modal').style.display = 'none'; }

async function handleProfileUpdateSubmit(e) {
  e.preventDefault();
  const avatarInput = document.getElementById('edit-avatar-file');
  const coverInput = document.getElementById('edit-cover-file');
  let avatarUrl = '', coverUrl = '';

  if(avatarInput && avatarInput.files[0]) avatarUrl = (await uploadSelectedFile(avatarInput)).url;
  if(coverInput && coverInput.files[0]) coverUrl = (await uploadSelectedFile(coverInput)).url;

  const res = await fetch('/api/users/profile/update', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      full_name: document.getElementById('edit-fullname').value,
      phone: document.getElementById('edit-phone').value,
      occupation: document.getElementById('edit-occupation').value,
      age: document.getElementById('edit-age').value,
      gender: document.getElementById('edit-gender').value,
      bio: document.getElementById('edit-bio-text').value,
      avatar_url: avatarUrl,
      cover_url: coverUrl
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closeEditProfileModal();
    checkSession();
    openProfile(currentUser.username);
  }
}

function closeCPNModal() { document.getElementById('cpn-upgrade-modal').style.display = 'none'; }

async function handleClaimBankTransfer(e) {
  e.preventDefault();
  const note = document.getElementById('cpn-ref-note').value;
  const res = await fetch('/api/cpn/claim-bank-transfer', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reference_note: note})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) closeCPNModal();
}

async function loadPages() {
  const res = await fetch('/api/pages');
  const pages = await res.json();
  const c = document.getElementById('pages-container');
  if(!pages.length) { c.innerHTML = '<div class="card">No pages created yet. Click "+ Create Page" to start one!</div>'; return; }
  c.innerHTML = pages.map(g => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div style="display:flex;align-items:center;gap:12px;cursor:pointer;" onclick="openPageDetail(${g.id})">
        ${g.avatar_url ? `<img src="${g.avatar_url}" style="width:50px;height:50px;border-radius:12px;object-fit:cover;">` : `<div class="avatar" style="width:50px;height:50px;border-radius:12px;background:var(--fb-blue);"><i class="fa-solid fa-flag"></i></div>`}
        <div>
          <h4 style="font-weight:800;font-size:0.95rem;color:var(--navy-blue);">${g.name}</h4>
          <p style="font-size:0.75rem;color:var(--text-muted);">${g.member_count} Followers</p>
        </div>
      </div>
      <button onclick="openPageDetail(${g.id})" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;">Visit Page</button>
    </div>
  `).join('');
}

async function openPageDetail(pageId) {
  activePageId = pageId;
  switchNav('page-detail');
  const res = await fetch(`/api/pages/${pageId}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');
  const g = data.page;

  document.getElementById('active-page-id').value = g.id;
  const coverBg = g.cover_url ? `style="background-image:url('${g.cover_url}')"` : '';
  const avatarHtml = g.avatar_url ? `<img src="${g.avatar_url}" style="width:100%;height:100%;object-fit:cover;">` : `<i class="fa-solid fa-flag"></i>`;

  document.getElementById('page-detail-header').innerHTML = `
    <div class="fb-group-banner" ${coverBg}>
      <div class="fb-group-avatar">${avatarHtml}</div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:10px;">
      <div>
        <h2 style="font-size:1.2rem;font-weight:800;">${g.name}</h2>
        <p style="font-size:0.78rem;color:var(--text-muted);">${g.member_count} Followers</p>
      </div>
      <div>
        ${g.is_creator ? `<button onclick="openPageEditModal(${g.id}, '${g.name.replace(/'/g, "\\'")}', '${(g.description||'').replace(/'/g, "\\'")}')" class="btn-group-edit"><i class="fa-solid fa-pen-to-square"></i> Edit Page Details</button>` : ''}
        <button onclick="joinPage(${g.id})" class="btn-submit" style="width:auto;padding:8px 14px;font-size:0.8rem;background:${g.is_member ? '#ef4444' : 'var(--fb-blue)'};">
          ${g.is_member ? 'Unfollow Page' : 'Follow Page'}
        </button>
      </div>
    </div>
    <p style="font-size:0.85rem;margin-top:10px;color:var(--text-muted);">${g.description || ''}</p>
  `;

  if(g.is_member) {
    document.getElementById('page-post-composer').style.display = 'block';
  } else {
    document.getElementById('page-post-composer').style.display = 'none';
  }
  loadPosts('Social', 'page-posts-container', g.id);
}

function openPageCreateModal() {
  document.getElementById('edit-page-id').value = "0";
  document.getElementById('page-modal-title').innerText = "Create Page";
  document.getElementById('page-name').value = "";
  document.getElementById('page-desc').value = "";
  document.getElementById('page-create-modal').style.display = 'flex';
}

function openPageEditModal(gid, name, desc) {
  document.getElementById('edit-page-id').value = gid;
  document.getElementById('page-modal-title').innerText = "Edit Page Details";
  document.getElementById('page-name').value = name;
  document.getElementById('page-desc').value = desc;
  document.getElementById('page-create-modal').style.display = 'flex';
}

function closePageModal() { document.getElementById('page-create-modal').style.display = 'none'; }

async function uploadSelectedFile(fileInput) {
  if(!fileInput || !fileInput.files[0]) return {url:'', is_video: false};
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const res = await fetch('/api/upload', {method:'POST', body: formData});
  const data = await res.json();
  return data.success ? {url: data.url, is_video: data.is_video} : {url:'', is_video: false};
}

async function handlePageSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('page-name').value.trim();
  const desc = document.getElementById('page-desc').value.trim();
  const avatarInput = document.getElementById('page-avatar-file');
  const coverInput = document.getElementById('page-cover-file');
  let avatarUrl = '', coverUrl = '';

  if(avatarInput && avatarInput.files[0]) avatarUrl = (await uploadSelectedFile(avatarInput)).url;
  if(coverInput && coverInput.files[0]) coverUrl = (await uploadSelectedFile(coverInput)).url;

  const editId = parseInt(document.getElementById('edit-page-id').value);
  const endpoint = editId > 0 ? `/api/pages/${editId}/update` : '/api/pages';

  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, description: desc, avatar_url: avatarUrl, cover_url: coverUrl})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closePageModal();
    if(editId > 0) openPageDetail(editId);
    else loadPages();
  }
}

async function joinPage(pageId) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch(`/api/pages/${pageId}/join`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  openPageDetail(pageId);
}

async function loadPosts(postType, containerId, groupId = 0) {
  const res = await fetch(`/api/posts?type=${postType}&group_id=${groupId}`);
  const posts = await res.json();
  const container = document.getElementById(containerId);
  if(!posts.length) {
    container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No posts published yet.</div>`;
    return;
  }
  container.innerHTML = posts.map(p => renderPostCard(p)).join('');
}

function renderPostCard(p) {
  let mediaHtml = '';
  if(p.video_url) mediaHtml = `<video src="${p.video_url}" controls style="width:100%;border-radius:8px;margin-top:6px;"></video>`;
  else if(p.image_url) mediaHtml = `<img src="${p.image_url}" loading="lazy" style="width:100%;border-radius:8px;margin-top:6px;">`;

  const pageBadge = p.group_name ? `<span class="group-badge" onclick="openPageDetail(${p.group_id})"><i class="fa-solid fa-flag"></i> ${p.group_name}</span>` : '';

  return `
    <div class="feed-post">
      <div class="post-header">
        <div class="avatar" style="background:var(--fb-blue);" onclick="openProfile('${p.username}')">
          ${p.avatar_url ? `<img src="${p.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.full_name.charAt(0)}
        </div>
        <div>
          <div style="font-size:0.85rem;font-weight:800;cursor:pointer;" onclick="openProfile('${p.username}')">${p.full_name}</div>
          <div style="font-size:0.7rem;color:var(--text-muted);">@${p.username}</div>
        </div>
        ${pageBadge}
      </div>
      <div style="font-size:0.88rem;line-height:1.4;">${p.content}</div>
      ${mediaHtml}
      <div class="post-actions">
        <button class="post-action-btn" onclick="toggleLike(${p.id})">❤️ ${p.likes_count || 0} Likes</button>
        <button class="post-action-btn" onclick="toggleComments(${p.id})">💬 ${p.comments_count || 0} Comments</button>
        <button class="post-action-btn" onclick="sharePost(${p.id})">↪️ Share</button>
      </div>
      <div id="comments-box-${p.id}" class="comments-box" style="display:none;"></div>
    </div>`;
}

async function handlePostSubmit(e, postType) {
  if(e) e.preventDefault();
  if(!currentUser) return window.location.href = '/auth';

  const content = document.getElementById('post-content').value.trim();
  if(!content) return showToast('Please enter post text', 'error');

  let imageUrl = '', videoUrl = '';
  const fileInput = document.getElementById('post-file-input');
  if(fileInput && fileInput.files[0]) {
    const upload = await uploadSelectedFile(fileInput);
    if(upload.is_video) videoUrl = upload.url;
    else imageUrl = upload.url;
  }

  const res = await fetch('/api/posts', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content, image_url: imageUrl, video_url: videoUrl, post_type: postType, group_id: 0})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    document.getElementById('post-content').value = '';
    loadPosts(postType, 'feed-posts-container');
  } else if(data.requires_upgrade) {
    document.getElementById('cpn-upgrade-modal').style.display = 'flex';
  }
}

async function handlePagePostSubmit(e) {
  e.preventDefault();
  if(!currentUser) return window.location.href = '/auth';

  const pageId = parseInt(document.getElementById('active-page-id').value);
  const content = document.getElementById('page-post-content').value.trim();
  if(!content) return showToast('Please enter post text', 'error');

  let imageUrl = '', videoUrl = '';
  const fileInput = document.getElementById('page-post-file-input');
  if(fileInput && fileInput.files[0]) {
    const upload = await uploadSelectedFile(fileInput);
    if(upload.is_video) videoUrl = upload.url;
    else imageUrl = upload.url;
  }

  const res = await fetch('/api/posts', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content, image_url: imageUrl, video_url: videoUrl, post_type: 'Social', group_id: pageId})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    document.getElementById('page-post-content').value = '';
    loadPosts('Social', 'page-posts-container', pageId);
  }
}

async function toggleLike(pid) {
  if(!currentUser) return window.location.href = '/auth';
  await fetch(`/api/posts/${pid}/like`, {method:'POST'});
  if(activePageId > 0) loadPosts('Social', 'page-posts-container', activePageId);
  loadPosts('Social', 'feed-posts-container');
}

async function toggleComments(pid) {
  const box = document.getElementById(`comments-box-${pid}`);
  if(box.style.display === 'block') { box.style.display = 'none'; return; }
  box.style.display = 'block';

  const res = await fetch(`/api/posts/${pid}/comments`);
  const comments = await res.json();

  box.innerHTML = `
    <div id="comment-list-${pid}">
      ${comments.map(c => `
        <div class="comment-item ${c.parent_id > 0 ? 'comment-reply-item' : ''}">
          <span style="font-weight:800;">@${c.username}</span> ${c.parent_username ? `<small style="color:var(--fb-blue);">replying to @${c.parent_username}</small>` : ''}: ${c.content}
          <div style="display:flex;gap:12px;margin-top:2px;font-size:0.75rem;color:var(--text-muted);">
            <span onclick="toggleCommentLike(${c.id}, ${pid})" style="cursor:pointer;font-weight:700;">❤️ ${c.likes_count || 0}</span>
            <span onclick="setupReply(${pid}, ${c.id}, '${c.username}')" style="cursor:pointer;font-weight:700;color:var(--fb-blue);">↩️ Reply</span>
          </div>
        </div>
      `).join('') || '<small>No comments yet. Be the first to comment!</small>'}
    </div>
    <div style="display:flex;gap:4px;margin-top:8px;">
      <input type="text" id="comment-input-${pid}" class="form-control" placeholder="Write a comment..." style="padding:6px;font-size:0.8rem;">
      <button onclick="submitComment(${pid})" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;">Post</button>
    </div>
  `;
}

function setupReply(pid, commentId, username) {
  replyParentCommentId = commentId;
  const input = document.getElementById(`comment-input-${pid}`);
  input.value = `@${username} `;
  input.focus();
}

async function toggleCommentLike(cid, pid) {
  if(!currentUser) return window.location.href = '/auth';
  await fetch(`/api/comments/${cid}/like`, {method:'POST'});
  toggleComments(pid);
}

async function submitComment(pid) {
  if(!currentUser) return window.location.href = '/auth';
  const input = document.getElementById(`comment-input-${pid}`);
  const content = input.value.trim();
  if(!content) return;

  await fetch(`/api/posts/${pid}/comments`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content, parent_id: replyParentCommentId})
  });
  replyParentCommentId = 0;
  input.value = '';
  toggleComments(pid);
}

function sharePost(postId) {
  const shareUrl = `${window.location.origin}/?post=${postId}`;
  if (navigator.share) {
    navigator.share({ title: 'Ijebu Connect', text: 'Check out this post on Ijebu Connect!', url: shareUrl }).catch(() => {});
  } else {
    navigator.clipboard.writeText(shareUrl).then(() => showToast('Link copied to clipboard!'));
  }
}

// FAST EXECUTION: LOAD AUTH FIRST THEN HYDRATE FEED
window.onload = async function() {
  await checkSession();
  if(currentUser) {
    loadPosts('Social', 'feed-posts-container');
  }
};
</script>
</body>
</html>
"""

AUTH_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auth - Ijebu Connect</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root { --fb-blue: #1877f2; --border-light: #cbd5e1; }
* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; }
body { background: #f0f2f5; color: #0f172a; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
.auth-card { background: #fff; border: 1px solid var(--border-light); border-radius: 18px; padding: 1.5rem; max-width: 440px; width: 100%; text-align: center; }
.brand { font-size: 1.5rem; font-weight: 800; color: var(--fb-blue); margin-bottom: 0.2rem; }
.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.85rem; text-align: left; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }
.btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; }
.app-footer { margin-top: 1.5rem; text-align: center; font-size: 0.75rem; color: #64748b; }
</style>
</head>
<body>
<div class="auth-card">
  <div class="brand">IJEBU CONNECT</div>
  <p style="font-size:0.78rem;color:#64748b;margin-bottom:12px;">Sign in to join pages and connect.</p>
  <form id="form-login" onsubmit="handleLogin(event)">
    <div class="form-group"><label>Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
    <div class="form-group"><label>Password</label><input type="password" id="login-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit">Sign In</button>
  </form>
</div>

<footer class="app-footer">
  <p><strong>Willys Media World</strong> &copy; 2026</p>
  <p>Phone: 09018363715 | willysmediaworld@gmail.com</p>
</footer>

<script>
async function handleLogin(e) {
  e.preventDefault();
  const res = await fetch('/api/auth/login', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      username: document.getElementById('login-uname').value,
      password: document.getElementById('login-pword').value
    })
  });
  const data = await res.json();
  if(data.success) { window.location.href = '/'; }
  else alert(data.message);
}
</script>
</body>
</html>
"""

ADMIN_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Control Panel - Ijebu Connect</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
body { font-family:'Plus Jakarta Sans', sans-serif; background:#f8fafc; color:#0f172a; padding:1rem; max-width:1000px; margin:0 auto; }
.admin-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:0.75rem; margin-bottom:1rem; }
.card { background:#fff; border:1.5px solid #cbd5e1; border-radius:12px; padding:1rem; }
.val { font-size:1.3rem; font-weight:800; color:#059669; }
.lbl { font-size:0.72rem; color:#64748b; font-weight:700; text-transform:uppercase; }
.admin-tabs { display:flex; gap:6px; margin-bottom:1rem; border-bottom:2px solid #cbd5e1; padding-bottom:6px; overflow-x:auto; }
.admin-tab { padding:6px 12px; border-radius:6px; border:none; background:#fff; font-weight:700; font-size:0.8rem; cursor:pointer; color:#64748b; flex-shrink:0; }
.admin-tab.active { background:#0b1e36; color:#fff; }
.tab-sec { display:none; } .tab-sec.active { display:block; }
table { width:100%; border-collapse:collapse; background:#fff; border-radius:10px; overflow:hidden; border:1.5px solid #cbd5e1; font-size:0.82rem; margin-top:0.5rem; }
th, td { padding:8px 10px; text-align:left; border-bottom:1px solid #cbd5e1; }
th { background:#0b1e36; color:#fff; }
.btn-act { padding:4px 8px; border-radius:6px; border:none; color:#fff; font-weight:700; cursor:pointer; font-size:0.72rem; }
.btn-app { background:#059669; } .btn-rej { background:#ef4444; } .btn-del { background:#dc2626; }
.app-footer { margin-top: 2rem; padding: 1rem 0; border-top: 1px solid #cbd5e1; text-align: center; font-size: 0.78rem; color: #64748b; }
</style>
</head>
<body>
<div class="admin-header">
  <h2>⚙️ Rich Admin Control Panel</h2>
  <a href="/" style="color:#0b1e36;font-weight:700;text-decoration:none;font-size:0.85rem;">← Back to App</a>
</div>

<div class="grid">
  <div class="card"><div class="val" id="st-users">0</div><div class="lbl">Total Members</div></div>
  <div class="card"><div class="val" id="st-income" style="color:#2563eb;">₦0.00</div><div class="lbl">Total Gross Revenue</div></div>
  <div class="card"><div class="val" id="st-net" style="color:#059669;">₦0.00</div><div class="lbl">Admin Net Profit</div></div>
  <div class="card"><div class="val" id="st-partners">0</div><div class="lbl">CPN Partners</div></div>
  <div class="card"><div class="val" id="st-wallets">₦0.00</div><div class="lbl">Member Wallet Balances</div></div>
</div>

<div class="admin-tabs">
  <button class="admin-tab active" onclick="switchAdminTab('posts')">Manage Posts</button>
  <button class="admin-tab" onclick="switchAdminTab('members')">Manage Members</button>
  <button class="admin-tab" onclick="switchAdminTab('partners')">CPN Claims</button>
  <button class="admin-tab" onclick="switchAdminTab('payouts')">Bank Cashouts</button>
</div>

<!-- MANAGE POSTS TAB -->
<div id="adm-posts" class="tab-sec active">
  <h3>Platform Posts Moderation</h3>
  <table>
    <thead><tr><th>Author</th><th>Content Preview</th><th>Page / Section</th><th>Action</th></tr></thead>
    <tbody id="posts-body"></tbody>
  </table>
</div>

<!-- MANAGE MEMBERS TAB -->
<div id="adm-members" class="tab-sec">
  <h3>Registered Platform Members</h3>
  <table>
    <thead><tr><th>Full Name</th><th>Username</th><th>Phone</th><th>Type</th><th>Wallet Balance</th><th>Action</th></tr></thead>
    <tbody id="members-body"></tbody>
  </table>
</div>

<!-- CPN CLAIMS TAB -->
<div id="adm-partners" class="tab-sec">
  <h3>Pending CPN Partner Upgrades (₦2,000)</h3>
  <table>
    <thead><tr><th>Member</th><th>Amount</th><th>Reference Note</th><th>Action</th></tr></thead>
    <tbody id="partner-reqs-body"></tbody>
  </table>
</div>

<!-- BANK CASHOUTS TAB -->
<div id="adm-payouts" class="tab-sec">
  <h3>Member Cashout Requests</h3>
  <table>
    <thead><tr><th>User</th><th>Amount</th><th>Bank Details</th><th>Action</th></tr></thead>
    <tbody id="payouts-body"></tbody>
  </table>
</div>

<footer class="app-footer">
  <p><strong>Willys Media World</strong> &copy; 2026 Admin Dashboard</p>
  <p>Phone: 09018363715 | willysmediaworld@gmail.com</p>
</footer>

<script>
function switchAdminTab(t) {
  document.querySelectorAll('.admin-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-sec').forEach(s => s.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById(`adm-${t}`).classList.add('active');
}

async function loadAdminOverview() {
  const res = await fetch('/api/admin/overview');
  const data = await res.json();
  if(!data.success) { alert('Admin access denied.'); window.location.href='/'; return; }

  document.getElementById('st-users').innerText = data.total_users;
  document.getElementById('st-income').innerText = '₦' + data.total_gross_income.toLocaleString();
  document.getElementById('st-net').innerText = '₦' + data.admin_net_balance.toLocaleString();
  document.getElementById('st-partners').innerText = data.total_partners;
  document.getElementById('st-wallets').innerText = '₦' + data.total_partner_wallets.toLocaleString();

  loadAdminPosts();
  loadMembers();
  loadPartnerRequests();
  loadPayouts();
}

async function loadAdminPosts() {
  const res = await fetch('/api/admin/posts');
  const posts = await res.json();
  const body = document.getElementById('posts-body');
  if(!posts.length) { body.innerHTML = '<tr><td colspan="4">No posts found.</td></tr>'; return; }
  body.innerHTML = posts.map(p => `
    <tr>
      <td><b>${p.full_name}</b><br><small>@${p.username}</small></td>
      <td style="max-width:280px;">${p.content}</td>
      <td><b>${p.group_name ? `Page: ${p.group_name}` : p.post_type}</b></td>
      <td><button class="btn-act btn-del" onclick="deleteAdminPost(${p.id})">Delete Post</button></td>
    </tr>
  `).join('');
}

async function deleteAdminPost(pid) {
  if(!confirm('Are you sure you want to delete this post and its comments?')) return;
  const res = await fetch(`/api/admin/posts?post_id=${pid}`, {method:'DELETE'});
  const data = await res.json();
  alert(data.message);
  loadAdminOverview();
}

async function loadMembers() {
  const res = await fetch('/api/admin/users');
  const users = await res.json();
  const body = document.getElementById('members-body');
  body.innerHTML = users.map(u => `
    <tr>
      <td><b>${u.full_name}</b></td>
      <td>@${u.username}</td>
      <td>${u.phone}</td>
      <td><b>${u.user_type}</b></td>
      <td><b>₦${(u.wallet_balance || 0).toLocaleString()}</b></td>
      <td>
        ${u.user_type !== 'Admin' ? `<button class="btn-act btn-del" onclick="deleteMember(${u.id})">Delete Member</button>` : 'System Admin'}
      </td>
    </tr>
  `).join('');
}

async function deleteMember(uid) {
  if(!confirm('Remove this member completely?')) return;
  await fetch(`/api/admin/users?user_id=${uid}`, {method:'DELETE'});
  loadAdminOverview();
}

async function loadPartnerRequests() {
  const res = await fetch('/api/admin/partner-requests');
  const reqs = await res.json();
  const body = document.getElementById('partner-reqs-body');
  body.innerHTML = reqs.map(r => `
    <tr>
      <td><b>${r.full_name}</b> (@${r.username})</td>
      <td>₦${r.amount.toLocaleString()}</td>
      <td>${r.reference_note}</td>
      <td>
        ${r.status === 'pending' ? `
          <button class="btn-act btn-app" onclick="actPartnerReq(${r.id}, 'approve')">Approve</button>
          <button class="btn-act btn-rej" onclick="actPartnerReq(${r.id}, 'reject')">Reject</button>
        ` : `<b>${r.status.toUpperCase()}</b>`}
      </td>
    </tr>
  `).join('');
}

async function actPartnerReq(id, action) {
  await fetch('/api/admin/partner-requests', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({request_id: id, action: action})
  });
  loadAdminOverview();
}

async function loadPayouts() {
  const res = await fetch('/api/admin/payouts');
  const payouts = await res.json();
  const body = document.getElementById('payouts-body');
  body.innerHTML = payouts.map(p => `
    <tr>
      <td><b>${p.full_name}</b></td>
      <td>₦${p.amount.toLocaleString()}</td>
      <td>${p.bank_name} (${p.account_number})</td>
      <td>
        ${p.status === 'pending' ? `
          <button class="btn-act btn-app" onclick="updatePayout(${p.id}, 'approved')">Approve</button>
          <button class="btn-act btn-rej" onclick="updatePayout(${p.id}, 'rejected')">Reject</button>
        ` : `<b>${r.status.toUpperCase()}</b>`}
      </td>
    </tr>
  `).join('');
}

async function updatePayout(id, status) {
  await fetch('/api/admin/payouts', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({payout_id: id, status: status})
  });
  loadPayouts();
}

loadAdminOverview();
</script>
</body>
</html>
"""

# ======================================================================
# ROUTE HANDLERS
# ======================================================================
@app.route('/')
def index():
    if 'user_id' not in session:
        logger.info("Unauthenticated user accessing index. Redirecting to /auth")
        return redirect(url_for('auth_page'))

    host_url = request.host_url
    if not host_url.startswith('https://') and 'localhost' not in host_url and '127.0.0.1' not in host_url:
        host_url = host_url.replace('http://', 'https://')

    meta_title = "Ijebu Connect - Facebook-Style Hub"
    meta_desc = "Connect with pages, friends, and trade on Ijebu Connect."
    meta_image = f"{host_url.rstrip('/')}/static/uploads/default_preview.jpg"
    meta_url = request.url

    return render_template_string(
        INDEX_TEMPLATE,
        contact_email=CONTACT_EMAIL,
        company_name=COMPANY_NAME,
        meta_title=meta_title,
        meta_desc=meta_desc,
        meta_image=meta_image,
        meta_url=meta_url,
        deep_link_json='null'
    )

@app.route('/auth')
def auth_page():
    return render_template_string(AUTH_TEMPLATE, contact_email=CONTACT_EMAIL, company_name=COMPANY_NAME)

@app.route('/admin')
def admin_page():
    return render_template_string(ADMIN_TEMPLATE, contact_email=CONTACT_EMAIL, company_name=COMPANY_NAME)

# GLOBAL ERROR HANDLER FOR SYSTEM RELIABILITY
@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Internal Server Error: {e}")
    return jsonify({'success': False, 'message': 'A system error occurred. Please try again later.'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Ijebu Connect application on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=True)