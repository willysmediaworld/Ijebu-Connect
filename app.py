import os
import re
import sqlite3
import random
import string
import json
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
# CONFIGURATION
# ======================================================================
DATABASE_URL = os.environ.get('DATABASE_URL')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '').strip()
ALLOW_TEST_PAYMENTS = os.environ.get('ALLOW_TEST_PAYMENTS', 'True').lower() == 'true'
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', 'willysmediaworld@gmail.com')
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
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f'''
        INSERT INTO notifications (user_id, sender_id, type, target_id, message)
        VALUES ({p}, {p}, {p}, {p}, {p})
    ''', (user_id, sender_id, notif_type, target_id, message))
    db.commit()

def count_user_listings(user_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f"SELECT COUNT(*) FROM products WHERE user_id = {p} AND status = 'active'", (user_id,))
    prod_count = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM posts WHERE user_id = {p} AND content LIKE '%[PRODUCT_ADVERT]%'", (user_id,))
    ad_post_count = cursor.fetchone()[0]
    return prod_count + ad_post_count

# HARDCODED SEEDING TO PREVENT RENDER DB RESET LOSSES
def seed_hardcoded_data(cursor, db):
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
        except Exception:
            db.rollback()
            admin_id = 1
    else:
        admin_id = existing_admin['id']
        try:
            cursor.execute(f"UPDATE users SET password_hash = {p}, user_type = 'Admin' WHERE id = {p}",
                           (admin_pass_hash, admin_id))
            db.commit()
        except Exception:
            db.rollback()

    # Seed Default Groups if database is fresh
    cursor.execute("SELECT COUNT(*) FROM groups")
    if cursor.fetchone()[0] == 0:
        cursor.execute(f'''
        INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
        VALUES ({p}, 'Ijebu Traders & Business Network', 'The primary networking hub for all merchants and entrepreneurs across Ijebu.', 'Business', '', '')
        ''', (admin_id,))
        g1_id = cursor.lastrowid or 1

        cursor.execute(f'''
        INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
        VALUES ({p}, 'Ojude Oba & Cultural Heritage Club', 'Celebrating the rich cultural festivals and history of Ijebu land.', 'Culture', '', '')
        ''', (admin_id,))
        g2_id = cursor.lastrowid or 2

        # Auto add admin as member
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (g1_id, admin_id))
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (g2_id, admin_id))

        # Seed Welcome Post inside Group (will also appear on Main Feed)
        cursor.execute(f'''
        INSERT INTO posts (user_id, group_id, content, post_type)
        VALUES ({p}, {p}, 'Welcome to Ijebu Connect Community Network! Connect with friends, list your business, and join local groups.', 'Social')
        ''', (admin_id, g1_id))

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

        # INDEX OPTIMIZATIONS
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
        except Exception:
            pass

        # RUN HARDCODED DATA SEEDER
        seed_hardcoded_data(cursor, db)

with app.app_context():
    init_db()

# ======================================================================
# SEO & INDEXING
# ======================================================================
@app.route('/robots.txt')
def robots_txt():
    content = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: {request.host_url}sitemap.xml"
    return Response(content, mimetype='text/plain')

@app.route('/google6c2b1a5f4a3ee8d9.html')
def google_verification():
    return "google-site-verification: google6c2b1a5f4a3ee8d9.html"

@app.route('/sitemap.xml')
def sitemap_xml():
    db = get_db()
    cursor = db.cursor()
    base_url = request.host_url.rstrip('/')
    urls = [f"{base_url}/", f"{base_url}/auth"]
    try:
        cursor.execute("SELECT id FROM products WHERE status = 'active' ORDER BY id DESC LIMIT 500")
        for row in cursor.fetchall():
            urls.append(f"{base_url}/?product={row['id']}")
    except Exception:
        pass
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in urls:
        xml_content += f'<url><loc>{u}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>\n'
    xml_content += '</urlset>'
    return Response(xml_content, mimetype='application/xml')

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
            is_verified_merchant, age, gender, relationship_intent, bio, occupation, avatar_url, cover_url, is_dating_active
            FROM users WHERE id = {p}''',
            (session['user_id'],)
        )
        u = cursor.fetchone()
        if u:
            d = dict(u)
            d['wallet_balance'] = float(d.get('wallet_balance') or 0)
            d['listings_count'] = count_user_listings(d['id'])
            cursor.execute(f"SELECT COUNT(*) FROM users WHERE referred_by = {p}", (d['referral_code'],))
            d['recruits_count'] = cursor.fetchone()[0]
            cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {p} AND is_read = 0", (d['id'],))
            d['unread_notifs'] = cursor.fetchone()[0]
            cursor.execute(f"SELECT COUNT(*) FROM messages WHERE receiver_id = {p} AND is_read = 0", (d['id'],))
            d['unread_chats'] = cursor.fetchone()[0]
            return jsonify({'logged_in': True, 'user': d})
    return jsonify({'logged_in': False})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully.'})

# ======================================================================
# GROUPS API
# ======================================================================
@app.route('/api/groups', methods=['GET', 'POST'])
def handle_groups():
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
            return jsonify({'success': False, 'message': 'Group name required.'}), 400

        cursor.execute(f'''INSERT INTO groups (user_id, name, description, category, avatar_url, cover_url)
        VALUES ({p}, {p}, {p}, 'Community', {p}, {p})''', (session['user_id'], name, desc, avatar, cover))
        group_id = cursor.lastrowid or 0
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (group_id, session['user_id']))
        db.commit()
        return jsonify({'success': True, 'message': f'Group "{name}" created successfully!'})

    cursor.execute(f'''
        SELECT g.*, COUNT(gm.id) AS member_count
        FROM groups g LEFT JOIN group_members gm ON g.id = gm.group_id
        GROUP BY g.id ORDER BY g.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/groups/<int:group_id>', methods=['GET'])
def get_group_detail(group_id):
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
    ''', (uid, group_id))
    group = cursor.fetchone()
    if not group:
        return jsonify({'success': False, 'message': 'Group not found.'}), 404

    res = dict(group)
    res['is_creator'] = (uid == group['user_id'])
    return jsonify({'success': True, 'group': res})

@app.route('/api/groups/<int:group_id>/update', methods=['POST'])
def update_group(group_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT user_id FROM groups WHERE id = {p}", (group_id,))
    g_row = cursor.fetchone()
    if not g_row or g_row['user_id'] != uid:
        return jsonify({'success': False, 'message': 'Only group owners can update group details.'}), 403

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
        params.append(group_id)
        cursor.execute(f"UPDATE groups SET {', '.join(updates)} WHERE id = {p}", tuple(params))
        db.commit()

    return jsonify({'success': True, 'message': 'Group details updated successfully!'})

@app.route('/api/groups/<int:group_id>/members', methods=['GET'])
def get_group_members(group_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f'''
        SELECT u.id, u.full_name, u.username, u.avatar_url, u.user_type, gm.created_at AS joined_at
        FROM group_members gm
        JOIN users u ON gm.user_id = u.id
        WHERE gm.group_id = {p} ORDER BY gm.id ASC
    ''', (group_id,))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/groups/<int:group_id>/join', methods=['POST'])
def join_group(group_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id FROM group_members WHERE group_id = {p} AND user_id = {p}", (group_id, uid))
    if cursor.fetchone():
        cursor.execute(f"DELETE FROM group_members WHERE group_id = {p} AND user_id = {p}", (group_id, uid))
        db.commit()
        return jsonify({'success': True, 'joined': False, 'message': 'Left group.'})
    else:
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (group_id, uid))
        db.commit()
        return jsonify({'success': True, 'joined': True, 'message': 'Joined group!'})

# ======================================================================
# SOCIAL FEED & GROUP FEED INTEGRATION (POSTS & COMMENTS)
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
    post_type_filter = request.args.get('type', 'Social')
    group_filter = int(request.args.get('group_id') or 0)

    # GROUP POSTS ALSO APPEAR ON MAIN FEED (group_filter == 0)
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
# ADMIN API (WITH POST DELETION & MODERATION)
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

    cursor.execute("SELECT COUNT(*) FROM posts")
    total_posts = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM groups")
    total_groups = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(wallet_balance), 0) FROM users")
    total_wallets = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM partner_requests WHERE status = 'pending'")
    pending_partners = cursor.fetchone()[0]

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_posts': total_posts,
        'total_groups': total_groups,
        'total_partner_wallets': total_wallets,
        'pending_partners': pending_partners
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
        return jsonify({'success': True, 'message': 'Post removed successfully.'})

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
        cursor.execute(f"DELETE FROM users WHERE id = {p}", (user_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Member removed.'})

    cursor.execute("SELECT id, full_name, username, phone, user_type FROM users ORDER BY id DESC")
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

* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; }
body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; padding-bottom: 70px; }

#toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; }
.toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; }

header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-title { font-size: 1.15rem; font-weight: 800; color: var(--navy-blue); }
.brand-title span { color: var(--fb-blue); }

.top-nav-pills { display: flex; gap: 6px; padding: 0.6rem 0.5rem; background: #fff; border-bottom: 1px solid var(--border-light); overflow-x: auto; }
.nav-pill { padding: 6px 14px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; background: #f0f2f5; color: var(--text-muted); cursor: pointer; flex-shrink: 0; }
.nav-pill.active { background: var(--fb-blue); color: #fff; }

.app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.75rem; flex: 1; }
.view-section { display: none; }
.view-section.active { display: block; }
.card { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 1rem; margin-bottom: 0.85rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

/* FACEBOOK GROUP BANNER & PROMINENT EDIT BUTTON */
.fb-group-banner { height: 160px; background: linear-gradient(135deg, #1877f2, #0b1e36); border-radius: 12px 12px 0 0; position: relative; margin: -1rem -1rem 45px -1rem; background-size: cover; background-position: center; }
.fb-group-avatar { position: absolute; bottom: -35px; left: 16px; width: 75px; height: 75px; border-radius: 16px; border: 4px solid #fff; background: var(--fb-blue); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; }

.btn-group-edit {
  background: #f0f2f5;
  border: 1.5px solid var(--border-light);
  padding: 10px 18px;
  border-radius: 10px;
  font-weight: 800;
  font-size: 0.9rem;
  color: var(--navy-blue);
  cursor: pointer;
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 8px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.05);
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
.form-control { padding: 10px 12px; border-radius: 8px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }

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
  <div class="nav-pill" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-users"></i> Groups</div>
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
        <button type="submit" class="btn-submit">Publish Post</button>
      </form>
    </div>
    <div id="feed-posts-container"></div>
  </div>

  <!-- GROUPS HUB -->
  <div id="view-groups" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:1.1rem;font-weight:800;color:var(--navy-blue);">Facebook Groups</h3>
      <button onclick="openGroupCreateModal()" class="btn-submit" style="width:auto;padding:8px 16px;">+ Create Group</button>
    </div>
    <div id="groups-container"></div>
  </div>

  <!-- GROUP DETAIL VIEW -->
  <div id="view-group-detail" class="view-section">
    <button onclick="switchNav('groups')" style="background:#fff;border:1px solid var(--border-light);padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back to Groups</button>

    <div id="group-detail-header" class="card"></div>

    <div class="card" id="group-post-composer" style="display:none;">
      <h4 style="font-size:0.88rem; font-weight:800; margin-bottom:6px;">Post to Group (Will also show on Main Feed)</h4>
      <form onsubmit="handleGroupPostSubmit(event)">
        <input type="hidden" id="active-group-id" value="0">
        <textarea class="form-control" id="group-post-content" rows="2" placeholder="Write something in this group..."></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="group-post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Post to Group</button>
      </form>
    </div>

    <div id="group-posts-container"></div>
  </div>

</div>

<!-- CREATE/EDIT GROUP MODAL -->
<div id="group-create-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;padding:1rem;">
  <div class="card" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;" id="group-modal-title">Create Group</h3>
      <button onclick="closeGroupModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleGroupSubmit(event)">
      <input type="hidden" id="edit-group-id" value="0">
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Group Name</label><input type="text" id="grp-name" class="form-control" required></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Group Profile Photo (Avatar)</label><input type="file" id="grp-avatar-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Group Cover Banner</label><input type="file" id="grp-cover-file" class="form-control" accept="image/*"></div>
      <div style="margin-bottom:8px;"><label style="font-size:0.8rem;font-weight:700;">Description</label><textarea id="grp-desc" class="form-control" rows="2"></textarea></div>
      <button type="submit" class="btn-submit">Save Group Details</button>
    </form>
  </div>
</div>

<footer class="app-footer">
  <p><strong>{{ company_name }}</strong> &copy; 2026. All Rights Reserved.</p>
  <p><i class="fa-solid fa-envelope"></i> Email: <a href="mailto:{{ contact_email }}">{{ contact_email }}</a></p>
</footer>

<div class="mobile-bottom-nav">
  <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Feed</div>
  <div class="nav-item" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-users"></i> Groups</div>
</div>

<script>
let currentUser = null;
let activeGroupId = 0;
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
  if(target === 'groups') loadGroups();
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
    }
  } catch(e){}
}

function renderHeaderAuth() {
  const box = document.getElementById('header-auth');
  if(currentUser) {
    box.innerHTML = `<b style="font-size:0.82rem;">@${currentUser.username}</b>`;
  } else {
    box.innerHTML = `<a href="/auth" style="background:var(--fb-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
  }
}

/* GROUPS & FACEBOOK STYLE LOGIC */
async function loadGroups() {
  const res = await fetch('/api/groups');
  const groups = await res.json();
  const c = document.getElementById('groups-container');
  if(!groups.length) { c.innerHTML = '<div class="card">No groups created yet. Click "+ Create Group" to start one!</div>'; return; }

  c.innerHTML = groups.map(g => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div style="display:flex;align-items:center;gap:12px;cursor:pointer;" onclick="openGroupDetail(${g.id})">
        ${g.avatar_url ? `<img src="${g.avatar_url}" style="width:50px;height:50px;border-radius:12px;object-fit:cover;">` : `<div class="avatar" style="width:50px;height:50px;border-radius:12px;background:var(--fb-blue);"><i class="fa-solid fa-users"></i></div>`}
        <div>
          <h4 style="font-weight:800;font-size:0.95rem;color:var(--navy-blue);">${g.name}</h4>
          <p style="font-size:0.75rem;color:var(--text-muted);">${g.member_count} Members</p>
        </div>
      </div>
      <button onclick="openGroupDetail(${g.id})" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;">Visit Group</button>
    </div>
  `).join('');
}

async function openGroupDetail(groupId) {
  activeGroupId = groupId;
  switchNav('group-detail');
  const res = await fetch(`/api/groups/${groupId}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');

  const g = data.group;
  document.getElementById('active-group-id').value = g.id;

  const coverBg = g.cover_url ? `style="background-image:url('${g.cover_url}')"` : '';
  const avatarHtml = g.avatar_url ? `<img src="${g.avatar_url}" style="width:100%;height:100%;object-fit:cover;">` : `<i class="fa-solid fa-users"></i>`;

  document.getElementById('group-detail-header').innerHTML = `
    <div class="fb-group-banner" ${coverBg}>
      <div class="fb-group-avatar">${avatarHtml}</div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:10px;">
      <div>
        <h2 style="font-size:1.2rem;font-weight:800;">${g.name}</h2>
        <p style="font-size:0.78rem;color:var(--text-muted);">${g.member_count} Members</p>
      </div>
      <div>
        ${g.is_creator ? `<button onclick="openGroupEditModal(${g.id}, '${g.name.replace(/'/g, "\\'")}', '${(g.description||'').replace(/'/g, "\\'")}')" class="btn-group-edit"><i class="fa-solid fa-pen"></i> Edit Group</button>` : ''}
        <button onclick="joinGroup(${g.id})" class="btn-submit" style="width:auto;padding:8px 14px;font-size:0.8rem;background:${g.is_member ? '#ef4444' : 'var(--fb-blue)'};">
          ${g.is_member ? 'Leave Group' : 'Join Group'}
        </button>
      </div>
    </div>
    <p style="font-size:0.85rem;margin-top:10px;color:var(--text-muted);">${g.description || ''}</p>
  `;

  if(g.is_member) {
    document.getElementById('group-post-composer').style.display = 'block';
  } else {
    document.getElementById('group-post-composer').style.display = 'none';
  }

  loadPosts('Social', 'group-posts-container', g.id);
}

function openGroupCreateModal() {
  document.getElementById('edit-group-id').value = "0";
  document.getElementById('group-modal-title').innerText = "Create Group";
  document.getElementById('group-create-modal').style.display = 'flex';
}

function openGroupEditModal(gid, name, desc) {
  document.getElementById('edit-group-id').value = gid;
  document.getElementById('group-modal-title').innerText = "Edit Group Details";
  document.getElementById('grp-name').value = name;
  document.getElementById('grp-desc').value = desc;
  document.getElementById('group-create-modal').style.display = 'flex';
}

function closeGroupModal() { document.getElementById('group-create-modal').style.display = 'none'; }

async function uploadSelectedFile(fileInput) {
  if(!fileInput || !fileInput.files[0]) return {url:'', is_video: false};
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const res = await fetch('/api/upload', {method:'POST', body: formData});
  const data = await res.json();
  return data.success ? {url: data.url, is_video: data.is_video} : {url:'', is_video: false};
}

async function handleGroupSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('grp-name').value.trim();
  const desc = document.getElementById('grp-desc').value.trim();
  const avatarInput = document.getElementById('grp-avatar-file');
  const coverInput = document.getElementById('grp-cover-file');

  let avatarUrl = '', coverUrl = '';
  if(avatarInput && avatarInput.files[0]) avatarUrl = (await uploadSelectedFile(avatarInput)).url;
  if(coverInput && coverInput.files[0]) coverUrl = (await uploadSelectedFile(coverInput)).url;

  const editId = parseInt(document.getElementById('edit-group-id').value);
  const endpoint = editId > 0 ? `/api/groups/${editId}/update` : '/api/groups';

  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, description: desc, avatar_url: avatarUrl, cover_url: coverUrl})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closeGroupModal();
    if(editId > 0) openGroupDetail(editId);
    else loadGroups();
  }
}

async function joinGroup(groupId) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch(`/api/groups/${groupId}/join`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  openGroupDetail(groupId);
}

/* POSTS & COMMENT REPLIES ENGINE */
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

  const groupBadge = p.group_name ? `<span class="group-badge" onclick="openGroupDetail(${p.group_id})"><i class="fa-solid fa-users"></i> ${p.group_name}</span>` : '';

  return `
    <div class="feed-post">
      <div class="post-header">
        <div class="avatar" style="background:var(--fb-blue);">
          ${p.avatar_url ? `<img src="${p.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.full_name.charAt(0)}
        </div>
        <div>
          <div style="font-size:0.85rem;font-weight:800;">${p.full_name}</div>
          <div style="font-size:0.7rem;color:var(--text-muted);">@${p.username}</div>
        </div>
        ${groupBadge}
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
  }
}

async function handleGroupPostSubmit(e) {
  e.preventDefault();
  if(!currentUser) return window.location.href = '/auth';
  const groupId = parseInt(document.getElementById('active-group-id').value);
  const content = document.getElementById('group-post-content').value.trim();
  if(!content) return showToast('Please enter post text', 'error');

  let imageUrl = '', videoUrl = '';
  const fileInput = document.getElementById('group-post-file-input');
  if(fileInput && fileInput.files[0]) {
    const upload = await uploadSelectedFile(fileInput);
    if(upload.is_video) videoUrl = upload.url;
    else imageUrl = upload.url;
  }

  const res = await fetch('/api/posts', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content, image_url: imageUrl, video_url: videoUrl, post_type: 'Social', group_id: groupId})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    document.getElementById('group-post-content').value = '';
    loadPosts('Social', 'group-posts-container', groupId);
  }
}

async function toggleLike(pid) {
  if(!currentUser) return window.location.href = '/auth';
  await fetch(`/api/posts/${pid}/like`, {method:'POST'});
  if(activeGroupId > 0) loadPosts('Social', 'group-posts-container', activeGroupId);
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

window.onload = function() {
  checkSession();
  loadPosts('Social', 'feed-posts-container');
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
  <p style="font-size:0.78rem;color:#64748b;margin-bottom:12px;">Sign in to join groups and participate in discussion.</p>
  <form id="form-login" onsubmit="handleLogin(event)">
    <div class="form-group"><label>Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
    <div class="form-group"><label>Password</label><input type="password" id="login-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit">Sign In</button>
  </form>
</div>

<footer class="app-footer">
  <p><strong>Willys Media World</strong> &copy; 2026</p>
  <p>willysmediaworld@gmail.com</p>
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
.btn-del { background:#dc2626; }
.app-footer { margin-top: 2rem; padding: 1rem 0; border-top: 1px solid #cbd5e1; text-align: center; font-size: 0.78rem; color: #64748b; }
</style>
</head>
<body>

<div class="admin-header">
  <h2>⚙️ Admin Control Panel</h2>
  <a href="/" style="color:#0b1e36;font-weight:700;text-decoration:none;font-size:0.85rem;">← Back to App</a>
</div>

<div class="grid">
  <div class="card"><div class="val" id="st-users">0</div><div class="lbl">Total Members</div></div>
  <div class="card"><div class="val" id="st-posts">0</div><div class="lbl">Total Posts</div></div>
  <div class="card"><div class="val" id="st-groups">0</div><div class="lbl">Community Groups</div></div>
</div>

<div class="admin-tabs">
  <button class="admin-tab active" onclick="switchAdminTab('posts')">Manage Posts & Content</button>
  <button class="admin-tab" onclick="switchAdminTab('members')">Manage Members</button>
</div>

<!-- MANAGE POSTS TAB -->
<div id="adm-posts" class="tab-sec active">
  <h3>Platform Posts Moderation</h3>
  <table>
    <thead><tr><th>Author</th><th>Content Preview</th><th>Group / Type</th><th>Action</th></tr></thead>
    <tbody id="posts-body"></tbody>
  </table>
</div>

<!-- MANAGE MEMBERS TAB -->
<div id="adm-members" class="tab-sec">
  <h3>Registered Platform Members</h3>
  <table>
    <thead><tr><th>Full Name</th><th>Username</th><th>Phone</th><th>Type</th><th>Action</th></tr></thead>
    <tbody id="members-body"></tbody>
  </table>
</div>

<footer class="app-footer">
  <p><strong>Willys Media World</strong> &copy; 2026 Admin Dashboard</p>
  <p>willysmediaworld@gmail.com</p>
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
  document.getElementById('st-posts').innerText = data.total_posts;
  document.getElementById('st-groups').innerText = data.total_groups;

  loadAdminPosts();
  loadMembers();
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
      <td><b>${p.group_name ? `Group: ${p.group_name}` : p.post_type}</b></td>
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
    db = get_db()
    host_url = request.host_url
    if not host_url.startswith('https://') and 'localhost' not in host_url and '127.0.0.1' not in host_url:
        host_url = host_url.replace('http://', 'https://')

    meta_title = "Ijebu Connect - Facebook-Style Hub"
    meta_desc = "Connect with groups, friends, and trade on Ijebu Connect."
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)