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

ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
ALLOWED_VIDEO_EXTS = {'mp4', 'webm', 'mov', 'm4v', 'avi'}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTS.union(ALLOWED_VIDEO_EXTS)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_system_logos():
    """Detects and returns logo1 (AUTH) and logo2 (MAIN SYSTEM)."""
    static_dir = os.path.join(app.root_path, 'static')
    valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif')
    found_files = []
    
    if os.path.exists(static_dir):
        for f in sorted(os.listdir(static_dir)):
            if f.lower().endswith(valid_exts) and not f.startswith('.'):
                found_files.append(f"/static/{f}")
    if os.path.exists(UPLOAD_FOLDER):
        for f in sorted(os.listdir(UPLOAD_FOLDER)):
            if f.lower().endswith(valid_exts) and not f.startswith('.'):
                found_files.append(f"/static/uploads/{f}")

    logo1 = found_files[0] if len(found_files) > 0 else "/static/logo1.png"
    logo2 = found_files[1] if len(found_files) > 1 else (found_files[0] if len(found_files) > 0 else "/static/logo2.png")
    return logo1, logo2

@app.after_request
def add_header(response):
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

# ======================================================================
# DATABASE ENGINE (AUTOMATIC POSTGRES / SQLITE DETECTOR)
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

# HARDCODED SEEDING
def seed_hardcoded_data(cursor, db):
    logger.info("Executing persistent seeding...")
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

    hardcoded_members = [
        ("Willys Media Support", "09018363715", "willysmedia", "CPN00002", "CPN Partner"),
        ("Ijebu Imusin Youth Forum", "08000000001", "ijebuyouths", "CPN00003", "Resident")
    ]
    for m_name, m_phone, m_uname, m_ref, m_type in hardcoded_members:
        cursor.execute(f"SELECT id FROM users WHERE username = {p} OR phone = {p}", (m_uname, m_phone))
        if not cursor.fetchone():
            try:
                cursor.execute(f'''
                    INSERT INTO users (full_name, phone, username, password_hash, user_type, referral_code)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p})
                ''', (m_name, m_phone, m_uname, generate_password_hash("Password123"), m_type, m_ref))
            except Exception as e:
                logger.error(f"Error seeding member {m_uname}: {e}")

    db.commit()

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
        )
        ''')

        safe_add_column(cursor, 'users', 'age', 'INTEGER DEFAULT 18')
        safe_add_column(cursor, 'users', 'gender', "TEXT DEFAULT 'Unspecified'")
        safe_add_column(cursor, 'users', 'relationship_intent', "TEXT DEFAULT 'Networking'")
        safe_add_column(cursor, 'users', 'bio', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'occupation', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'avatar_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'users', 'cover_url', "TEXT DEFAULT ''")

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
        )
        ''')

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
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS post_likes (
            id {pk_type},
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, user_id)
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS comments (
            id {pk_type},
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            parent_id INTEGER DEFAULT 0,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS comment_likes (
            id {pk_type},
            comment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(comment_id, user_id)
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS followers (
            id {pk_type},
            follower_id INTEGER NOT NULL,
            followed_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(follower_id, followed_id)
        )
        ''')

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
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS group_members (
            id {pk_type},
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, user_id)
        )
        ''')

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
        )
        ''')

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
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS dating_winks (
            id {pk_type},
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sender_id, receiver_id)
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS partner_requests (
            id {pk_type},
            user_id INTEGER NOT NULL,
            amount REAL DEFAULT 2000.0,
            payment_method TEXT DEFAULT 'Bank Transfer',
            reference_note TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS transactions (
            id {pk_type},
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

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
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS messages (
            id {pk_type},
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_delivered INTEGER DEFAULT 1,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS blocked_users (
            id {pk_type},
            blocker_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(blocker_id, blocked_id)
        )
        ''')

        safe_add_column(cursor, 'messages', 'is_delivered', 'INTEGER DEFAULT 1')

        # SPEED OPTIMIZATION INDEXES
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users (LOWER(username))")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users (phone)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_user ON posts (user_id)")
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
        return jsonify({'success': True, 'url': file_url, 'is_video': is_video})

    return jsonify({'success': False, 'message': 'Unsupported file format.'}), 400

# ======================================================================
# GLOBAL SEARCH API
# ======================================================================
@app.route('/api/search', methods=['GET'])
def global_search():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify({'success': True, 'users': [], 'pages': []})

    db = get_db()
    cursor = db.cursor()
    p = query_param()

    cursor.execute(f'''
        SELECT id, full_name, username, user_type, avatar_url, occupation
        FROM users
        WHERE LOWER(full_name) LIKE {p} OR LOWER(username) LIKE {p} OR LOWER(occupation) LIKE {p}
        ORDER BY id DESC LIMIT 15
    ''', (f"%{q}%", f"%{q}%", f"%{q}%"))
    users = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f'''
        SELECT g.id, g.name, g.category, g.avatar_url, g.description,
               (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id = g.id) AS member_count
        FROM groups g
        WHERE LOWER(g.name) LIKE {p} OR LOWER(g.description) LIKE {p}
        ORDER BY g.id DESC LIMIT 15
    ''', (f"%{q}%", f"%{q}%"))
    pages = [dict(r) for r in cursor.fetchall()]

    return jsonify({'success': True, 'users': users, 'pages': pages})

# ======================================================================
# NOTIFICATIONS API
# ======================================================================
@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session:
        return jsonify({'success': False, 'notifications': [], 'unread_count': 0}), 401
    
    uid = session['user_id']
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    cursor.execute(f'''
        SELECT n.*, u.full_name AS sender_name, u.username AS sender_username, u.avatar_url AS sender_avatar
        FROM notifications n
        LEFT JOIN users u ON n.sender_id = u.id
        WHERE n.user_id = {p}
        ORDER BY n.id DESC LIMIT 30
    ''', (uid,))
    notifs = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {p} AND is_read = 0", (uid,))
    unread_count = cursor.fetchone()[0]

    return jsonify({'success': True, 'notifications': notifs, 'unread_count': unread_count})

@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    uid = session['user_id']
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"UPDATE notifications SET is_read = 1 WHERE user_id = {p}", (uid,))
    db.commit()
    return jsonify({'success': True})

# ======================================================================
# FOLLOW / UNFOLLOW ENGINE
# ======================================================================
@app.route('/api/users/<username>/follow', methods=['POST'])
def toggle_follow_user(username):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id, full_name FROM users WHERE LOWER(username) = {p}", (username.lower(),))
    target = cursor.fetchone()
    if not target:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    target_id = target['id']
    if target_id == uid:
        return jsonify({'success': False, 'message': 'You cannot follow yourself.'}), 400

    cursor.execute(f"SELECT id FROM followers WHERE follower_id = {p} AND followed_id = {p}", (uid, target_id))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(f"DELETE FROM followers WHERE id = {p}", (existing['id'],))
        is_following = False
        msg = f"Unfollowed @{username}"
    else:
        cursor.execute(f"INSERT INTO followers (follower_id, followed_id) VALUES ({p}, {p})", (uid, target_id))
        is_following = True
        msg = f"Following @{username}!"
        add_notification(target_id, uid, 'follow', uid, f"{session['full_name']} started following you!")

    db.commit()
    cursor.execute(f"SELECT COUNT(*) FROM followers WHERE followed_id = {p}", (target_id,))
    followers_count = cursor.fetchone()[0]

    return jsonify({'success': True, 'is_following': is_following, 'followers_count': followers_count, 'message': msg})

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

    return jsonify({'success': False, 'message': 'Invalid credentials.'}), 401

@app.route('/api/auth/me', methods=['GET'])
def get_current_user():
    if 'user_id' in session:
        db = get_db()
        cursor = db.cursor()
        p = query_param()
        cursor.execute(
            f'''SELECT id, full_name, phone, username, user_type, referral_code, wallet_balance,
            is_verified_merchant, age, gender, relationship_intent, bio, occupation, avatar_url,
            cover_url, is_dating_active
            FROM users WHERE id = {p}''',
            (session['user_id'],)
        )
        u = cursor.fetchone()
        if u:
            d = dict(u)
            d['wallet_balance'] = float(d.get('wallet_balance') or 0)
            d['listings_count'] = count_user_listings(d['id'])

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
# PROFILE UPDATE ENDPOINT
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

    updates, params = [], []
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

    cursor.execute('''
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
        CASE WHEN EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = g.id AND gm.user_id = {p}) THEN 1
        ELSE 0 END AS is_member
        FROM groups g JOIN users u ON g.user_id = u.id
        WHERE g.id = {p}
    ''', (uid, page_id))
    page = cursor.fetchone()
    if not page:
        return jsonify({'success': False, 'message': 'Page not found.'}), 404
    res = dict(page)
    res['is_creator'] = (uid == page['user_id'])
    return jsonify({'success': True, 'page': res})

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

    cursor.execute(f'''
        SELECT e.*, u.full_name AS creator_name, u.username AS creator_username
        FROM events e JOIN users u ON e.user_id = u.id
        ORDER BY e.id DESC LIMIT 50
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
    return jsonify({'success': True, 'message': 'Payment claim submitted! Admin will verify and activate your CPN Partner status.'})

# ======================================================================
# MULTI-PILLAR PRODUCTS API
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

    listing_type = request.args.get('type', 'Market').strip()
    cursor.execute(f'''
        SELECT p.*, u.full_name AS seller_name, u.username AS seller_username
        FROM products p JOIN users u ON p.user_id = u.id
        WHERE p.status = 'active' AND p.listing_type = {p}
        ORDER BY p.id DESC
    ''', (listing_type,))
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

    cursor.execute(f'''
        SELECT id, full_name, username, user_type, age, gender, relationship_intent, bio, occupation, avatar_url, created_at
        FROM users WHERE is_dating_active = 1 AND id != {p} ORDER BY id DESC LIMIT 50
    ''', (current_uid,))
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
# SOCIAL FEED & POST DETAIL API
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

        cursor.execute(
            f"INSERT INTO posts (user_id, group_id, content, image_url, video_url, post_type) VALUES ({p}, {p}, {p}, {p}, {p}, {p})",
            (uid, group_id, content, image_url, video_url, post_type)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Published successfully!'})

    current_uid = session.get('user_id') or 0
    single_post_id = int(request.args.get('post_id') or 0)
    group_filter = int(request.args.get('group_id') or 0)

    if single_post_id > 0:
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
            WHERE p.id = {p}
        ''', (current_uid, single_post_id))
    elif group_filter > 0:
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
            ORDER BY p.id DESC LIMIT 60
        ''', (current_uid,))

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
        CASE WHEN EXISTS (SELECT 1 FROM comment_likes cl WHERE cl.comment_id = c.id AND cl.user_id = {p}) THEN 1
        ELSE 0 END AS liked_by_me
        FROM comments c
        JOIN users u ON c.user_id = u.id
        LEFT JOIN comments pc ON c.parent_id = pc.id
        LEFT JOIN users pu ON pc.user_id = pu.id
        WHERE c.post_id = {p} ORDER BY c.id ASC
    ''', (uid, post_id))
    return jsonify([dict(r) for r in cursor.fetchall()])

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
# CHAT API (WITH WHATSAPP TICKS)
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
        cursor.execute(f"SELECT COUNT(*) FROM messages WHERE sender_id = {p} AND receiver_id = {p} AND is_read = 0",
                       (other_id, uid))
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

    cursor.execute(f"SELECT id, full_name, username, user_type, avatar_url FROM users WHERE LOWER(username) = {p}",
                   (username.lower(),))
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

        cursor.execute(f"INSERT INTO messages (sender_id, receiver_id, content, is_delivered, is_read) VALUES ({p}, {p}, {p}, 1, 0)",
                       (uid, other_id, content))
        db.commit()
        msg_id = cursor.lastrowid or 0
        add_notification(other_id, uid, 'chat', uid, f"{session['full_name']} sent you a message.")
        return jsonify({'success': True, 'message': 'Sent.', 'msg_id': msg_id})

    # Mark messages as READ when thread opens
    cursor.execute(f"UPDATE messages SET is_read = 1 WHERE sender_id = {p} AND receiver_id = {p}", (other_id, uid))
    db.commit()

    cursor.execute(f'''
        SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_delivered, m.is_read, m.created_at, u.full_name, u.username
        FROM messages m JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id = {p} AND m.receiver_id = {p}) OR (m.sender_id = {p} AND m.receiver_id = {p})
        ORDER BY m.id ASC LIMIT 300
    ''', (uid, other_id, other_id, uid))
    messages = [dict(r) for r in cursor.fetchall()]

    return jsonify({'success': True, 'other': dict(other), 'messages': messages, 'me_id': uid})

# ======================================================================
# FRONTEND TEMPLATE
# ======================================================================
INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{{ meta_title }}</title>
    <meta name="description" content="{{ meta_desc }}">

    <!-- DYNAMIC SOCIAL PREVIEW META TAGS (OPEN GRAPH & TWITTER) -->
    <meta property="og:site_name" content="Ijebu Connect">
    <meta property="og:title" content="{{ meta_title }}">
    <meta property="og:description" content="{{ meta_desc }}">
    <meta property="og:image" content="{{ meta_image }}">
    <meta property="og:url" content="{{ meta_url }}">
    <meta property="og:type" content="website">

    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{{ meta_title }}">
    <meta name="twitter:description" content="{{ meta_desc }}">
    <meta name="twitter:image" content="{{ meta_image }}">

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
            --tick-green: #22c55e;
            --tick-gray: #9ca3af;
        }
        * { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color:transparent; }
        body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; padding-bottom: 70px; }

        #toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; pointer-events:none; }
        .toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }

        /* HEADER & LAYOUT */
        header { background: #fff; padding: 0.6rem 0.8rem; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; display: flex; flex-direction: column; gap: 8px; }
        .header-top-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
        .header-brand { display: flex; align-items: center; gap: 8px; cursor: pointer; flex-shrink: 0; }
        .header-logo-img { height: 34px; width: auto; max-width: 110px; object-fit: contain; border-radius: 6px; }
        .brand-title { font-size: 1.05rem; font-weight: 800; color: var(--navy-blue); }
        .brand-title span { color: var(--fb-blue); }

        .global-search-wrap { position: relative; width: 100%; }
        .global-search-input { padding: 8px 12px 8px 36px; border-radius: 20px; border: 1.5px solid var(--border-light); font-size: 0.82rem; outline: none; width: 100%; background: #f0f2f5; }
        .global-search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 0.8rem; }

        .header-right-actions { display: flex; align-items: center; gap: 8px; position: relative; }
        .icon-btn { background: #f0f2f5; border: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--text-dark); cursor: pointer; position: relative; font-size: 1rem; }
        .icon-btn:hover { background: #e4e6eb; }

        /* USER HEADER WITH LOGOUT ON TOP OF NAME */
        .user-header-stacked { display: flex; flex-direction: column; align-items: flex-end; justify-content: center; gap: 2px; }
        .header-logout-btn { background: #ef4444; color: #fff; border: none; padding: 2px 8px; border-radius: 10px; font-size: 0.65rem; font-weight: 800; cursor: pointer; line-height: 1.2; text-transform: uppercase; }
        .header-logout-btn:hover { background: #dc2626; }

        /* NOTIFICATIONS DROPDOWN */
        .notif-dropdown { display: none; position: absolute; top: 46px; right: 0; width: 310px; max-height: 400px; overflow-y: auto; background: #fff; border-radius: 12px; border: 1px solid var(--border-light); box-shadow: 0 4px 16px rgba(0,0,0,0.15); z-index: 1100; padding: 8px; }
        .notif-item { padding: 10px; border-bottom: 1px solid #f0f2f5; display: flex; gap: 10px; align-items: center; font-size: 0.8rem; cursor: pointer; border-radius: 8px; }
        .notif-item:hover { background: #f8fafc; }
        .notif-item.unread { background: #e7f3ff; font-weight: 600; }

        .top-nav-pills { display: flex; gap: 6px; padding: 0.5rem; background: #fff; border-bottom: 1px solid var(--border-light); overflow-x: auto; scrollbar-width: none; }
        .top-nav-pills::-webkit-scrollbar { display: none; }
        .nav-pill { padding: 6px 14px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; background: #f0f2f5; color: var(--text-muted); cursor: pointer; flex-shrink: 0; display: flex; align-items: center; gap: 4px; }
        .nav-pill.active { background: var(--fb-blue); color: #fff; }

        .unread-badge { background: #ef4444; color: #fff; font-size: 0.65rem; font-weight: 800; padding: 2px 6px; border-radius: 10px; line-height: 1; }

        .app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.75rem; flex: 1; }
        .view-section { display: none; }
        .view-section.active { display: block; }
        .card { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 1rem; margin-bottom: 0.85rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

        .clickable-name { cursor: pointer; color: var(--navy-blue); font-weight: 800; }
        .clickable-name:hover { text-decoration: underline; color: var(--fb-blue); }

        /* PROFILE BANNER & AVATAR */
        .fb-group-banner { height: 160px; background: linear-gradient(135deg, #1877f2, #0b1e36); border-radius: 12px 12px 0 0; position: relative; margin: -1rem -1rem 45px -1rem; background-size: cover; background-position: center; }
        .fb-group-avatar { position: absolute; bottom: -35px; left: 16px; width: 75px; height: 75px; border-radius: 16px; border: 4px solid #fff; background: var(--fb-blue); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; }

        .feed-post { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 0.88rem; margin-bottom: 0.85rem; }
        .post-header { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; flex-shrink: 0; background-size: cover; background-position: center; cursor: pointer; }

        .post-actions { display: flex; gap: 6px; padding-top: 8px; margin-top: 8px; border-top: 1px solid var(--border-light); }
        .post-action-btn { flex: 1; background: none; border: none; padding: 8px; border-radius: 6px; font-size: 0.8rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 4px; }
        .post-action-btn:hover { background: #f0f2f5; }

        .btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; min-height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
        .btn-secondary { background: var(--navy-blue); color: #fff; }
        .form-control { padding: 10px 12px; border-radius: 8px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; background: #fff; }

        .mobile-bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1px solid var(--border-light); display: flex; justify-content: space-around; padding: 6px 0; z-index: 1000; height: 60px; }
        .nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.7rem; font-weight: 700; flex: 1; cursor: pointer; text-decoration: none; position: relative; }
        .nav-item.active { color: var(--fb-blue); }

        .app-footer { background: #fff; border-top: 1px solid var(--border-light); padding: 1.2rem; text-align: center; font-size: 0.78rem; color: var(--text-muted); margin-top: 2rem; }
        .app-footer a { color: var(--fb-blue); text-decoration: none; font-weight: 700; }

        /* CHAT TICKS */
        .chat-tick { font-size: 0.75rem; margin-left: 4px; }
        .tick-sent { color: var(--tick-gray); }
        .tick-delivered { color: var(--tick-gray); }
        .tick-read { color: var(--tick-green); }

        @media(min-width: 600px) {
            header { flex-direction: row; align-items: center; justify-content: space-between; }
            .global-search-wrap { max-width: 260px; }
        }
    </style>
    <script>
        window.INITIAL_DEEP_LINK_DATA = {{ deep_link_json | safe }};
    </script>
</head>
<body>
<div id="toast-container"></div>
<header>
    <div class="header-top-row">
        <div class="header-brand" onclick="switchNav('feed')">
            <img src="{{ app_logo }}" alt="Logo" class="header-logo-img" onerror="this.style.display='none'">
            <div class="brand-title">IJEBU <span>CONNECT</span></div>
        </div>
        <div class="header-right-actions">
            <button class="icon-btn" onclick="sharePlatform()" title="Share Platform">
                <i class="fa-solid fa-share-nodes"></i>
            </button>
            <button class="icon-btn" onclick="toggleNotificationsMenu()" title="Notifications">
                <i class="fa-solid fa-bell"></i>
                <span class="unread-badge notif-unread-badge" id="notif-badge-count" style="display:none; position:absolute; top:-2px; right:-2px;">0</span>
            </button>
            <div class="notif-dropdown" id="notif-dropdown-menu"></div>
            <div id="header-auth"></div>
        </div>
    </div>
    <div class="global-search-wrap">
        <i class="fa-solid fa-magnifying-glass global-search-icon"></i>
        <input type="text" class="global-search-input" placeholder="🔍 Search members, pages, posts..." onkeyup="handleGlobalSearch(this.value)">
    </div>
</header>

<div class="top-nav-pills">
    <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Feed</div>
    <div class="nav-pill" data-nav="pages" onclick="switchNav('pages')"><i class="fa-solid fa-flag"></i> Pages</div>
    <div class="nav-pill" data-nav="chat" onclick="switchNav('chat')">
        <i class="fa-solid fa-comments"></i> Chat
        <span class="unread-badge chat-unread-badge" style="display:none;">0</span>
    </div>
    <div class="nav-pill" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days"></i> Events</div>
    <div class="nav-pill" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
    <div class="nav-pill" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
</div>

<div class="app-container">
    <!-- SEARCH VIEW -->
    <div id="view-search" class="view-section">
        <h3 style="font-size:1.05rem;font-weight:800;margin-bottom:10px;color:var(--navy-blue);">🔍 Live Search Results</h3>
        <div id="search-results-container"></div>
    </div>

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
            <h3 style="font-size:1.1rem;font-weight:800;color:var(--navy-blue);">Community Pages</h3>
            <button onclick="openPageCreateModal()" class="btn-submit" style="width:auto;padding:8px 16px;">+ Create Page</button>
        </div>
        <div id="pages-container"></div>
    </div>

    <!-- PAGE DETAIL VIEW -->
    <div id="view-page-detail" class="view-section">
        <button onclick="switchNav('pages')" style="background:#fff;border:1px solid var(--border-light);padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back to Pages</button>
        <div id="page-detail-header" class="card"></div>
        <div class="card" id="page-post-composer" style="display:none;">
            <h4 style="font-size:0.88rem; font-weight:800; margin-bottom:6px;">Post to Page</h4>
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

    <!-- CHAT HUB -->
    <div id="view-chat" class="view-section">
        <div id="chat-list-wrap">
            <h3 style="font-size:1rem;font-weight:800;margin-bottom:8px;color:var(--navy-blue);">💬 Messages & Discussions</h3>
            <div id="chat-partners-container"></div>
        </div>
        <div id="chat-thread-wrap" style="display:none;">
            <button onclick="closeChatThread()" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-size:0.75rem;font-weight:700;margin-bottom:8px;">← Back to All Messages</button>
            <div id="chat-thread-header" class="card" style="padding:0.6rem 0.8rem;margin-bottom:6px;"></div>
            <div id="chat-messages" style="min-height:240px;max-height:55vh;overflow-y:auto;padding:8px;background:#fff;border-radius:12px;border:1px solid var(--border-light);margin-bottom:8px;"></div>
            <form onsubmit="sendChatMessage(event)" style="position:sticky;bottom:0;background:var(--bg-body);padding:4px 0;">
                <div style="display:flex;gap:6px;">
                    <input type="text" id="chat-input" class="form-control" placeholder="Write a message..." style="flex:1;" required>
                    <button type="submit" class="btn-submit" style="width:auto;padding:10px 18px;">Send</button>
                </div>
            </form>
        </div>
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

    <!-- DATING -->
    <div id="view-dating" class="view-section">
        <div class="card" style="background:linear-gradient(135deg, #4f46e5, #7c3aed);color:#fff;">
            <h3 style="font-weight:800;margin-bottom:4px;">❤️ Ijebu Singles Match</h3>
            <p style="font-size:0.78rem;opacity:0.9;margin-bottom:8px;">Connect with verified singles across Ijebu.</p>
            <button onclick="openDatingSettingsModal()" style="background:#fff;color:#4f46e5;border:none;padding:6px 12px;border-radius:8px;font-weight:800;font-size:0.75rem;">Set Up Dating Profile</button>
        </div>
        <div id="dating-matches-container"></div>
    </div>

    <!-- PUBLIC MEMBER PROFILE VIEW -->
    <div id="view-profile" class="view-section">
        <button onclick="switchNav('feed')" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back</button>
        <div id="profile-wall-container"></div>
    </div>
</div>

<!-- ALL SYSTEM MODALS -->
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

<footer class="app-footer">
    <p><strong>{{ company_name }}</strong> &copy; 2026. All Rights Reserved.</p>
    <p><i class="fa-solid fa-phone"></i> Phone: <strong>09018363715</strong> | <i class="fa-solid fa-envelope"></i> Email: <a href="mailto:{{ contact_email }}">{{ contact_email }}</a></p>
    <div style="margin-top:10px;">
        <button onclick="sharePlatform()" class="btn-submit btn-secondary" style="width:auto;padding:6px 14px;font-size:0.75rem;"><i class="fa-solid fa-share-nodes"></i> Share Ijebu Connect</button>
    </div>
</footer>

<div class="mobile-bottom-nav">
    <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Feed</div>
    <div class="nav-item" data-nav="pages" onclick="switchNav('pages')"><i class="fa-solid fa-flag"></i> Pages</div>
    <div class="nav-item" data-nav="chat" onclick="switchNav('chat')">
        <i class="fa-solid fa-comments"></i> Chat
        <span class="unread-badge chat-unread-badge" style="display:none;position:absolute;top:4px;right:18px;">0</span>
    </div>
    <div class="nav-item" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
</div>

<script>
let currentUser = null;
let activePageId = 0;
let activeChatPartner = null;

function showToast(msg, type = 'success') {
    const box = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerText = msg;
    box.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

function formatTimestamp(ts) {
    if (!ts) return '';
    try {
        const cleanTs = ts.replace ? ts.replace(' ', 'T') : ts;
        const d = new Date(cleanTs);
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch(e) {
        return ts;
    }
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
    if(target === 'chat') loadChatPartners();
    if(target === 'events') loadEventsFeed();
    if(target === 'market') loadCategoryListings('Market', 'products-container');
    if(target === 'dating') loadDatingMatches();
}

async function checkSession() {
    try {
        const res = await fetch('/api/auth/me');
        const data = await res.json();
        if(data.logged_in) {
            currentUser = data.user;
            renderHeaderAuth();
            updateUnreadChatBadges(currentUser.unread_chats || 0);
            loadNotifications();
        } else {
            currentUser = null;
            renderHeaderAuth();
            window.location.href = '/auth';
        }
    } catch(e){}
}

/* NOTIFICATIONS WITH DIRECT NAVIGATION */
async function loadNotifications() {
    if(!currentUser) return;
    try {
        const res = await fetch('/api/notifications');
        const data = await res.json();
        if(data.success) {
            const badge = document.getElementById('notif-badge-count');
            if(data.unread_count > 0) {
                badge.innerText = data.unread_count;
                badge.style.display = 'inline-block';
            } else {
                badge.style.display = 'none';
            }

            const menu = document.getElementById('notif-dropdown-menu');
            if(!data.notifications.length) {
                menu.innerHTML = '<div style="padding:10px;text-align:center;color:var(--text-muted);font-size:0.8rem;">No notifications yet.</div>';
                return;
            }

            menu.innerHTML = data.notifications.map(n => `
                <div class="notif-item ${n.is_read ? '' : 'unread'}" onclick="handleNotifClick('${n.type}', ${n.target_id}, '${n.sender_username || ''}')">
                    <div class="avatar" style="width:32px;height:32px;background:var(--fb-blue);">${n.sender_avatar ? `<img src="${n.sender_avatar}" style="width:100%;height:100%;border-radius:50%;">` : '🔔'}</div>
                    <div>
                        <div>${n.message}</div>
                        <div style="font-size:0.65rem;color:var(--text-muted);">${formatTimestamp(n.created_at)}</div>
                    </div>
                </div>
            `).join('');
        }
    } catch(e){}
}

async function handleNotifClick(type, targetId, senderUsername) {
    toggleNotificationsMenu();
    if(type === 'post' || type === 'like' || type === 'comment') {
        switchNav('feed');
        loadPosts('Social', 'feed-posts-container', 0, targetId);
    } else if(type === 'chat') {
        switchNav('chat');
        if(senderUsername) openChatThread(senderUsername);
    } else if(type === 'follow' || type === 'wink') {
        if(senderUsername) openProfile(senderUsername);
    } else {
        switchNav('feed');
    }
}

async function toggleNotificationsMenu() {
    const menu = document.getElementById('notif-dropdown-menu');
    const isVisible = menu.style.display === 'block';
    menu.style.display = isVisible ? 'none' : 'block';
    if(!isVisible && currentUser) {
        await fetch('/api/notifications/read', {method:'POST'});
        document.getElementById('notif-badge-count').style.display = 'none';
    }
}

function updateUnreadChatBadges(count) {
    const badges = document.querySelectorAll('.chat-unread-badge');
    badges.forEach(b => {
        if(count > 0) {
            b.innerText = count;
            b.style.display = 'inline-block';
        } else {
            b.style.display = 'none';
        }
    });
}

/* HEADER WITH LOGOUT BUTTON DIRECTLY ON TOP OF USERNAME */
function renderHeaderAuth() {
    const box = document.getElementById('header-auth');
    if(currentUser) {
        box.innerHTML = `
            <div class="user-header-stacked">
                <button onclick="handleLogout()" class="header-logout-btn">Logout</button>
                <span class="clickable-name" style="font-size:0.75rem;" onclick="openProfile('${currentUser.username}')">@${currentUser.username}</span>
            </div>`;
    } else {
        box.innerHTML = `<a href="/auth" style="background:var(--fb-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
    }
}

async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    currentUser = null;
    window.location.href = '/auth';
}

/* CHAT THREAD WITH WHATSAPP-STYLE MARKS */
async function loadChatPartners() {
    if(!currentUser) return window.location.href = '/auth';
    const res = await fetch('/api/chat/partners');
    const data = await res.json();
    const c = document.getElementById('chat-partners-container');

    if(!data.success || !data.partners.length) {
        c.innerHTML = '<div class="card" style="text-align:center;">No messages yet. Use the search bar or user profiles to start a discussion!</div>';
        updateUnreadChatBadges(0);
        return;
    }

    let totalUnread = 0;
    c.innerHTML = data.partners.map(p => {
        totalUnread += (p.unread || 0);
        return `
            <div class="card" style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;" onclick="openChatThread('${p.user.username}')">
                <div style="display:flex;align-items:center;gap:10px;flex:1;">
                    <div class="avatar" style="width:44px;height:44px;background:var(--fb-blue);">${p.user.avatar_url ? `<img src="${p.user.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.user.full_name.charAt(0)}</div>
                    <div style="flex:1;">
                        <h4 style="font-weight:800;font-size:0.88rem;display:flex;align-items:center;justify-content:space-between;">
                            ${p.user.full_name}
                            ${p.unread > 0 ? `<span class="unread-badge">${p.unread} New</span>` : ''}
                        </h4>
                        <p style="font-size:0.78rem;color:var(--text-muted);">${p.last_from_me ? 'You: ' : ''}${p.last_message || 'Started a chat'}</p>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    updateUnreadChatBadges(totalUnread);
}

function startChatWith(username) {
    switchNav('chat');
    openChatThread(username);
}

async function openChatThread(username) {
    if(!currentUser) return window.location.href = '/auth';
    activeChatPartner = username;
    document.getElementById('chat-list-wrap').style.display = 'none';
    document.getElementById('chat-thread-wrap').style.display = 'block';

    const res = await fetch(`/api/chat/${encodeURIComponent(username)}`);
    const data = await res.json();
    if(!data.success) return showToast(data.message, 'error');

    const other = data.other;
    document.getElementById('chat-thread-header').innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;">
            <div class="avatar" style="width:36px;height:36px;background:var(--fb-blue);">${other.avatar_url ? `<img src="${other.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : other.full_name.charAt(0)}</div>
            <div>
                <h4 class="clickable-name" style="font-size:0.9rem;" onclick="openProfile('${other.username}')">${other.full_name}</h4>
                <p style="font-size:0.7rem;color:var(--text-muted);">@${other.username}</p>
            </div>
        </div>
    `;

    const msgsBox = document.getElementById('chat-messages');
    if(!data.messages.length) {
        msgsBox.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:1rem;">Send a message to start chatting!</div>';
    } else {
        msgsBox.innerHTML = data.messages.map(m => {
            const isMe = m.sender_id === data.me_id;
            
            // WHATSAPP-STYLE MARKS
            let ticksHtml = '';
            if(isMe) {
                if(m.is_read) {
                    ticksHtml = `<span class="chat-tick tick-read" title="Read"><i class="fa-solid fa-check-double"></i></span>`;
                } else if(m.is_delivered) {
                    ticksHtml = `<span class="chat-tick tick-delivered" title="Delivered"><i class="fa-solid fa-check-double"></i></span>`;
                } else {
                    ticksHtml = `<span class="chat-tick tick-sent" title="Sent"><i class="fa-solid fa-check"></i></span>`;
                }
            }

            return `
                <div style="display:flex;justify-content:${isMe ? 'flex-end' : 'flex-start'};margin-bottom:6px;">
                    <div style="max-width:75%;padding:8px 12px;border-radius:12px;font-size:0.82rem;background:${isMe ? 'var(--fb-blue)' : '#f0f2f5'};color:${isMe ? '#fff' : '#050505'};">
                        ${m.content}
                        <div style="font-size:0.62rem;opacity:0.85;text-align:right;margin-top:2px;display:flex;align-items:center;justify-content:flex-end;gap:2px;">
                            ${formatTimestamp(m.created_at)} ${ticksHtml}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }
    msgsBox.scrollTop = msgsBox.scrollHeight;
    loadChatPartners();
}

function closeChatThread() {
    activeChatPartner = null;
    document.getElementById('chat-thread-wrap').style.display = 'none';
    document.getElementById('chat-list-wrap').style.display = 'block';
    loadChatPartners();
}

async function sendChatMessage(e) {
    e.preventDefault();
    if(!activeChatPartner) return;
    const input = document.getElementById('chat-input');
    const content = input.value.trim();
    if(!content) return;

    const res = await fetch(`/api/chat/${encodeURIComponent(activeChatPartner)}`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({content})
    });
    const data = await res.json();
    if(data.success) {
        input.value = '';
        openChatThread(activeChatPartner);
    } else {
        showToast(data.message, 'error');
    }
}

/* RESTORED COMPLETE MEMBER PROFILE ENGINE */
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

    let productsHtml = '';
    if(u.products && u.products.length) {
        productsHtml = `
            <h4 style="font-size:0.9rem;margin:12px 0 6px;">Directory Listings (${u.products.length})</h4>
            <div class="card">
                ${u.products.map(p => `
                    <div style="border-bottom:1px solid var(--border-light);padding-bottom:8px;margin-bottom:8px;display:flex;gap:10px;align-items:center;">
                        ${p.image_url ? `<img src="${p.image_url}" style="width:50px;height:50px;border-radius:8px;object-fit:cover;">` : `<div style="width:50px;height:50px;background:#f0f2f5;border-radius:8px;display:flex;align-items:center;justify-content:center;"><i class="fa-solid fa-store"></i></div>`}
                        <div style="flex:1;">
                            <h4 style="font-weight:800;font-size:0.85rem;">${p.title}</h4>
                            <div style="color:var(--emerald-green);font-weight:800;font-size:0.8rem;">₦${p.price.toLocaleString()}</div>
                        </div>
                        <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" style="background:#25d366;color:#fff;padding:4px 8px;border-radius:6px;font-size:0.7rem;text-decoration:none;font-weight:700;"><i class="fa-brands fa-whatsapp"></i> Chat</a>
                    </div>
                `).join('')}
            </div>
        `;
    }

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
                    <button onclick="startChatWith('${u.username}')" class="btn-submit" style="font-size:0.78rem;background:var(--navy-blue);"><i class="fa-solid fa-paper-plane"></i> Message</button>
                    <button onclick="toggleFollow('${u.username}')" class="btn-submit" style="font-size:0.78rem;background:${u.is_following ? '#ef4444' : 'var(--fb-blue)'};">${u.is_following ? '✓ Following' : '+ Follow'}</button>
                `}
                <button onclick="shareMemberProfile('${u.username}')" class="btn-submit btn-secondary" style="font-size:0.78rem;width:auto;"><i class="fa-solid fa-share"></i> Share</button>
            </div>
        </div>
        ${walletBlock}
        ${productsHtml}
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

async function toggleFollow(username) {
    if(!currentUser) return window.location.href = '/auth';
    const res = await fetch(`/api/users/${encodeURIComponent(username)}/follow`, {method:'POST'});
    const data = await res.json();
    showToast(data.message);
    if(data.success) openProfile(username);
}

/* POST CARDS & FEED */
async function loadPosts(postType, containerId, groupId = 0, singlePostId = 0) {
    let url = `/api/posts?type=${postType}&group_id=${groupId}`;
    if(singlePostId > 0) url = `/api/posts?post_id=${singlePostId}`;

    const res = await fetch(url);
    const posts = await res.json();
    const container = document.getElementById(containerId);

    if(!posts.length) {
        container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No posts found.</div>`;
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
        <div class="feed-post" id="post-card-${p.id}">
            <div class="post-header">
                <div class="avatar" style="background:var(--fb-blue);" onclick="openProfile('${p.username}')">
                    ${p.avatar_url ? `<img src="${p.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.full_name.charAt(0)}
                </div>
                <div>
                    <div class="clickable-name" style="font-size:0.85rem;" onclick="openProfile('${p.username}')">${p.full_name}</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">@${p.username} • ${formatTimestamp(p.created_at)}</div>
                </div>
                ${pageBadge}
            </div>
            <div style="font-size:0.88rem;line-height:1.4;">${p.content}</div>
            ${mediaHtml}
            <div class="post-actions">
                <button class="post-action-btn" onclick="toggleLike(${p.id})">❤️ ${p.likes_count || 0} Likes</button>
                <button class="post-action-btn" onclick="toggleComments(${p.id})">💬 ${p.comments_count || 0} Comments</button>
                <button class="post-action-btn" onclick="sharePost(${p.id})">↪️ Share Post</button>
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
    }
}

async function uploadSelectedFile(fileInput) {
    if(!fileInput || !fileInput.files[0]) return {url:'', is_video: false};
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    const res = await fetch('/api/upload', {method:'POST', body: formData});
    const data = await res.json();
    return data.success ? {url: data.url, is_video: data.is_video} : {url:'', is_video: false};
}

async function toggleLike(pid) {
    if(!currentUser) return window.location.href = '/auth';
    await fetch(`/api/posts/${pid}/like`, {method:'POST'});
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
                <div class="comment-item">
                    <span class="clickable-name" onclick="openProfile('${c.username}')">@${c.username}</span>: ${c.content}
                    <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px;">${formatTimestamp(c.created_at)}</div>
                </div>
            `).join('') || '<small>No comments yet.</small>'}
        </div>
        <div style="display:flex;gap:4px;margin-top:8px;">
            <input type="text" id="comment-input-${pid}" class="form-control" placeholder="Write a comment..." style="padding:6px;font-size:0.8rem;">
            <button onclick="submitComment(${pid})" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;">Post</button>
        </div>
    `;
}

async function submitComment(pid) {
    if(!currentUser) return window.location.href = '/auth';
    const input = document.getElementById(`comment-input-${pid}`);
    const content = input.value.trim();
    if(!content) return;

    await fetch(`/api/posts/${pid}/comments`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({content, parent_id: 0})
    });
    input.value = '';
    toggleComments(pid);
}

/* SHARING PREVIEW JS HELPERS */
function sharePost(postId) {
    const shareUrl = `${window.location.origin}/?post=${postId}`;
    if (navigator.share) {
        navigator.share({ title: 'Ijebu Connect Post', text: 'Check out this post on Ijebu Connect!', url: shareUrl }).catch(() => {});
    } else {
        navigator.clipboard.writeText(shareUrl).then(() => showToast('Post link copied to clipboard!'));
    }
}

function shareMemberProfile(username) {
    const shareUrl = `${window.location.origin}/?user=${encodeURIComponent(username)}`;
    if (navigator.share) {
        navigator.share({ title: `${username} on Ijebu Connect`, text: `Connect with @${username} on Ijebu Connect!`, url: shareUrl }).catch(() => {});
    } else {
        navigator.clipboard.writeText(shareUrl).then(() => showToast('Profile link copied to clipboard!'));
    }
}

function sharePlatform() {
    const shareUrl = `${window.location.origin}/`;
    if (navigator.share) {
        navigator.share({ title: 'Ijebu Connect', text: 'Join Ijebu Connect to network, post, and explore opportunities!', url: shareUrl }).catch(() => {});
    } else {
        navigator.clipboard.writeText(shareUrl).then(() => showToast('Platform link copied to clipboard!'));
    }
}

window.onload = async function() {
    await checkSession();
    if(currentUser) {
        if (window.INITIAL_DEEP_LINK_DATA && window.INITIAL_DEEP_LINK_DATA.type === 'post') {
            loadPosts('Social', 'feed-posts-container', 0, window.INITIAL_DEEP_LINK_DATA.id);
        } else if (window.INITIAL_DEEP_LINK_DATA && window.INITIAL_DEEP_LINK_DATA.type === 'user') {
            openProfile(window.INITIAL_DEEP_LINK_DATA.username);
        } else {
            loadPosts('Social', 'feed-posts-container');
        }
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
        :root { --fb-blue: #1877f2; --navy-blue: #0b1e36; --border-light: #cbd5e1; }
        * { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; }
        body { background: #f0f2f5; color: #0f172a; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
        .auth-card { background: #fff; border: 1px solid var(--border-light); border-radius: 18px; padding: 1.5rem; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
        .auth-logo-img { max-height: 75px; width: auto; object-fit: contain; margin-bottom: 10px; border-radius: 8px; }
        .brand { font-size: 1.5rem; font-weight: 800; color: var(--fb-blue); margin-bottom: 0.2rem; }
        .auth-tabs { display: flex; margin: 12px 0; border-bottom: 2px solid #e2e8f0; }
        .auth-tab-btn { flex: 1; padding: 10px; border: none; background: none; font-weight: 700; font-size: 0.88rem; color: #64748b; cursor: pointer; }
        .auth-tab-btn.active { color: var(--fb-blue); border-bottom: 3px solid var(--fb-blue); }
        .form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.85rem; text-align: left; }
        .form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }
        .btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; margin-top: 6px; }
        .app-footer { margin-top: 1.5rem; text-align: center; font-size: 0.75rem; color: #64748b; }
    </style>
</head>
<body>
<div class="auth-card">
    <img src="{{ auth_logo }}" alt="Logo" class="auth-logo-img" onerror="this.style.display='none'">
    <div class="brand">IJEBU CONNECT</div>
    <p style="font-size:0.78rem;color:#64748b;">Connect, network, and trade across Ijebu.</p>
    <div class="auth-tabs">
        <button class="auth-tab-btn active" id="tab-login" onclick="switchAuthTab('login')">Sign In</button>
        <button class="auth-tab-btn" id="tab-register" onclick="switchAuthTab('register')">Register New Member</button>
    </div>

    <form id="form-login" onsubmit="handleLogin(event)">
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Password</label><input type="password" id="login-pword" class="form-control" required></div>
        <button type="submit" class="btn-submit">Sign In</button>
    </form>

    <form id="form-register" onsubmit="handleRegister(event)" style="display:none;">
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Full Name</label><input type="text" id="reg-fullname" class="form-control" placeholder="e.g. Adewale Adebayo" required></div>
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Phone Number</label><input type="tel" id="reg-phone" class="form-control" placeholder="e.g. 09018363715" required></div>
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Username</label><input type="text" id="reg-username" class="form-control" placeholder="e.g. adewale2026" required></div>
        <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Password</label><input type="password" id="reg-password" class="form-control" required></div>
        <button type="submit" class="btn-submit" style="background:var(--navy-blue);">Create Account</button>
    </form>
</div>
<footer class="app-footer">
    <p><strong>Willys Media World</strong> &copy; 2026</p>
</footer>
<script>
function switchAuthTab(type) {
    if (type === 'login') {
        document.getElementById('tab-login').classList.add('active');
        document.getElementById('tab-register').classList.remove('active');
        document.getElementById('form-login').style.display = 'block';
        document.getElementById('form-register').style.display = 'none';
    } else {
        document.getElementById('tab-register').classList.add('active');
        document.getElementById('tab-login').classList.remove('active');
        document.getElementById('form-register').style.display = 'block';
        document.getElementById('form-login').style.display = 'none';
    }
}

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

async function handleRegister(e) {
    e.preventDefault();
    const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
            full_name: document.getElementById('reg-fullname').value,
            phone: document.getElementById('reg-phone').value,
            username: document.getElementById('reg-username').value,
            password: document.getElementById('reg-password').value
        })
    });
    const data = await res.json();
    alert(data.message);
    if (data.success) {
        switchAuthTab('login');
        document.getElementById('login-uname').value = document.getElementById('reg-username').value;
    }
}
</script>
</body>
</html>
"""

# ======================================================================
# ROUTE HANDLERS & DYNAMIC OPENGRAPH SOCIAL SHARE PREVIEW ENGINE
# ======================================================================
@app.route('/')
def index():
    post_id = request.args.get('post')
    user_param = request.args.get('user')

    # IF NO PREVIEW PARAMS AND NOT LOGGED IN, REDIRECT USER TO /auth
    if 'user_id' not in session and not post_id and not user_param:
        return redirect(url_for('auth_page'))

    host_url = request.host_url
    if not host_url.startswith('https://') and 'localhost' not in host_url and '127.0.0.1' not in host_url:
        host_url = host_url.replace('http://', 'https://')

    logo1, logo2 = get_system_logos()
    system_logo_url = f"{host_url.rstrip('/')}{logo2}"

    # DEFAULT PLATFORM PREVIEW METADATA
    meta_title = "Ijebu Connect - Community Platform"
    meta_desc = "Connect with pages, friends, and trade on Ijebu Connect."
    meta_image = system_logo_url
    meta_url = request.url
    deep_link_json = 'null'

    db = get_db()
    cursor = db.cursor()
    p = query_param()

    # DYNAMIC POST PREVIEW GENERATOR
    if post_id:
        try:
            cursor.execute(f'''
                SELECT p.content, p.image_url, u.full_name
                FROM posts p JOIN users u ON p.user_id = u.id
                WHERE p.id = {p}
            ''', (int(post_id),))
            post_row = cursor.fetchone()
            if post_row:
                meta_title = f"Post by {post_row['full_name']} | Ijebu Connect"
                content_clean = post_row['content'].strip() if post_row['content'] else ""
                if len(content_clean) > 140:
                    meta_desc = content_clean[:137] + "..."
                else:
                    meta_desc = content_clean if content_clean else "Check out this post on Ijebu Connect!"

                if post_row['image_url']:
                    meta_image = f"{host_url.rstrip('/')}{post_row['image_url']}" if post_row['image_url'].startswith('/') else post_row['image_url']
                else:
                    meta_image = system_logo_url

                deep_link_json = json.dumps({'type': 'post', 'id': int(post_id)})
        except Exception as e:
            logger.error(f"Error parsing deep link post metadata: {e}")

    # DYNAMIC MEMBER PROFILE PREVIEW GENERATOR
    elif user_param:
        try:
            cursor.execute(f'''
                SELECT full_name, bio, avatar_url FROM users WHERE LOWER(username) = {p}
            ''', (user_param.lower(),))
            user_row = cursor.fetchone()
            if user_row:
                meta_title = f"{user_row['full_name']} (@{user_param}) | Ijebu Connect"
                meta_desc = user_row['bio'] if user_row['bio'] else f"Connect with {user_row['full_name']} on Ijebu Connect."
                
                if user_row['avatar_url']:
                    meta_image = f"{host_url.rstrip('/')}{user_row['avatar_url']}" if user_row['avatar_url'].startswith('/') else user_row['avatar_url']
                else:
                    meta_image = system_logo_url

                deep_link_json = json.dumps({'type': 'user', 'username': user_param})
        except Exception as e:
            logger.error(f"Error parsing deep link user metadata: {e}")

    return render_template_string(
        INDEX_TEMPLATE,
        contact_email=CONTACT_EMAIL,
        company_name=COMPANY_NAME,
        meta_title=meta_title,
        meta_desc=meta_desc,
        meta_image=meta_image,
        meta_url=meta_url,
        app_logo=logo2,
        deep_link_json=deep_link_json
    )

@app.route('/auth')
def auth_page():
    logo1, logo2 = get_system_logos()
    return render_template_string(
        AUTH_TEMPLATE,
        contact_email=CONTACT_EMAIL,
        company_name=COMPANY_NAME,
        auth_logo=logo1
    )

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Internal Server Error: {e}")
    return jsonify({'success': False, 'message': 'A system error occurred. Please try again later.'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Ijebu Connect application on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=True)