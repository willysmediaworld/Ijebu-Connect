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

        # NEW: GROUP & LOCAL EVENTS TABLE
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

        # ADMIN SEEDING
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
            except Exception:
                db.rollback()
        else:
            try:
                cursor.execute(f"UPDATE users SET password_hash = {p}, user_type = 'Admin' WHERE id = {p}",
                               (admin_pass_hash, existing_admin['id']))
                db.commit()
            except Exception:
                db.rollback()

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
# FRIEND SUGGESTIONS & SEARCH API
# ======================================================================
@app.route('/api/users/suggestions', methods=['GET'])
def get_friend_suggestions():
    if 'user_id' not in session:
        return jsonify([])
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']
    cursor.execute(f'''
        SELECT id, full_name, username, avatar_url, user_type, occupation
        FROM users
        WHERE id != {p}
        AND id NOT IN (SELECT followed_id FROM followers WHERE follower_id = {p})
        ORDER BY RANDOM() LIMIT 6
    ''', (uid, uid))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/search', methods=['GET'])
def global_search():
    q = request.args.get('q', '').strip().lower()
    if not q or len(q) < 2:
        return jsonify({'users': [], 'products': [], 'posts': [], 'groups': [], 'events': []})

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    term = f"%{q}%"

    cursor.execute(f"SELECT id, full_name, username, user_type, avatar_url FROM users WHERE LOWER(full_name) LIKE {p} OR LOWER(username) LIKE {p} LIMIT 10", (term, term))
    users = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT id, title, category, price, listing_type, image_url FROM products WHERE status='active' AND (LOWER(title) LIKE {p} OR LOWER(category) LIKE {p}) LIMIT 10", (term, term))
    products = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT p.id, p.content, p.post_type, u.full_name, u.username FROM posts p JOIN users u ON p.user_id = u.id WHERE LOWER(p.content) LIKE {p} LIMIT 10", (term,))
    posts = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT id, name, category, description, avatar_url FROM groups WHERE LOWER(name) LIKE {p} OR LOWER(description) LIKE {p} LIMIT 10", (term, term))
    groups = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"SELECT id, title, event_date, location, image_url FROM events WHERE LOWER(title) LIKE {p} OR LOWER(description) LIKE {p} LIMIT 10", (term, term))
    events = [dict(r) for r in cursor.fetchall()]

    return jsonify({'users': users, 'products': products, 'posts': posts, 'groups': groups, 'events': events})

# ======================================================================
# NOTIFICATIONS & FOLLOW API
# ======================================================================
@app.route('/api/notifications', methods=['GET', 'POST'])
def handle_notifications():
    if 'user_id' not in session:
        return jsonify([])
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    if request.method == 'POST':
        cursor.execute(f"UPDATE notifications SET is_read = 1 WHERE user_id = {p}", (uid,))
        db.commit()
        return jsonify({'success': True})

    cursor.execute(f'''
        SELECT n.*, u.username AS sender_username, u.avatar_url AS sender_avatar
        FROM notifications n
        LEFT JOIN users u ON n.sender_id = u.id
        WHERE n.user_id = {p} ORDER BY n.id DESC LIMIT 30
    ''', (uid,))
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/users/<username>/follow', methods=['POST'])
def toggle_follow(username):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id, full_name FROM users WHERE LOWER(username) = {p}", (username.lower(),))
    target = cursor.fetchone()
    if not target or target['id'] == uid:
        return jsonify({'success': False, 'message': 'Invalid operation.'}), 400

    target_id = target['id']
    cursor.execute(f"SELECT id FROM followers WHERE follower_id = {p} AND followed_id = {p}", (uid, target_id))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(f"DELETE FROM followers WHERE id = {p}", (existing['id'],))
        following = False
        msg = f"Unfollowed {target['full_name']}"
    else:
        cursor.execute(f"INSERT INTO followers (follower_id, followed_id) VALUES ({p}, {p})", (uid, target_id))
        following = True
        msg = f"Now following {target['full_name']}!"
        add_notification(target_id, uid, 'follow', uid, f"{session['full_name']} started following you!")

    db.commit()
    return jsonify({'success': True, 'following': following, 'message': msg})

# ======================================================================
# GROUPS API (FACEBOOK-STYLE PROFILE PICTURE, BANNER, MEMBERS & UPDATE)
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
        return jsonify({'success': False, 'message': 'Only the group owner can update group details.'}), 403

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
# EVENTS ENGINE API (LOCAL & GROUP EVENTS)
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
            SELECT e.*, u.full_name AS creator_name, u.username AS creator_username,
            g.name AS group_name
            FROM events e
            JOIN users u ON e.user_id = u.id
            LEFT JOIN groups g ON e.group_id = g.id
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
    return jsonify({'success': True, 'message': 'Cashout request submitted!'})

# ======================================================================
# MULTI-PILLAR API (2 FREE LISTINGS ENFORCEMENT)
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
        SELECT p.*, u.full_name AS seller_name, u.username AS seller_username,
        u.is_verified_merchant, u.user_type
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
# SOCIAL FEED & ADVERT ENFORCEMENT
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
                    'message': 'You have used your 2 Free Trial Advert Listings! Upgrade to CPN Partner (₦2,000) for unlimited product advertisements.',
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

    cursor.execute(f'''
        SELECT p.id, p.user_id, p.group_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
        u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
        (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
        CASE WHEN EXISTS (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p JOIN users u ON p.user_id = u.id
        WHERE p.post_type = {p} AND p.group_id = {p}
        ORDER BY p.id DESC LIMIT 60
    ''', (current_uid, post_type_filter, group_filter))
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
        (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes_count,
        CASE WHEN EXISTS (SELECT 1 FROM comment_likes cl WHERE cl.comment_id = c.id AND cl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
        FROM comments c JOIN users u ON c.user_id = u.id
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
        (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
        CASE WHEN EXISTS (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p JOIN users u ON p.user_id = u.id
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
    chatted_ids = set()

    for row in cursor.fetchall():
        other_id = row['other_id']
        chatted_ids.add(other_id)
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

    friends = []
    try:
        cursor.execute(f'''
            SELECT DISTINCT u.id, u.full_name, u.username, u.avatar_url, u.user_type
            FROM users u
            JOIN followers f ON (f.follower_id = {p} AND f.followed_id = u.id) OR (f.followed_id = {p} AND f.follower_id = u.id)
            WHERE u.id != {p} LIMIT 15
        ''', (uid, uid, uid))
        all_friends = cursor.fetchall()
        for f in all_friends:
            if f['id'] not in chatted_ids:
                friends.append(dict(f))
    except Exception:
        friends = []

    return jsonify({'success': True, 'partners': partners, 'friends': friends})

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
# ROBUST ADMIN API
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

    cursor.execute("SELECT COUNT(*) FROM groups")
    total_groups = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM events")
    total_events = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(wallet_balance), 0) FROM users")
    total_wallets = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM partner_requests WHERE status = 'pending'")
    pending_partners = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM partner_requests WHERE status = 'approved'")
    total_income = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payout_requests WHERE status = 'approved'")
    total_payouts = float(cursor.fetchone()[0] or 0)

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_products': total_products,
        'total_groups': total_groups,
        'total_events': total_events,
        'total_partner_wallets': total_wallets,
        'pending_partners': pending_partners,
        'total_income': total_income,
        'total_payouts': total_payouts
    })

@app.route('/api/admin/users', methods=['GET', 'DELETE', 'POST'])
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
        return jsonify({'success': True, 'message': 'Member and all associated data removed.'})

    if request.method == 'POST':
        data = request.json or {}
        user_id = data.get('user_id')
        action = data.get('action')
        if action == 'toggle_verify':
            cursor.execute(f"UPDATE users SET is_verified_merchant = CASE WHEN is_verified_merchant = 1 THEN 0 ELSE 1 END WHERE id = {p}", (user_id,))
            db.commit()
            return jsonify({'success': True, 'message': 'Verification status toggled.'})

    cursor.execute("SELECT id, full_name, username, phone, user_type, is_verified_merchant, referral_code, created_at FROM users ORDER BY id DESC")
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/groups', methods=['GET', 'DELETE'])
def admin_manage_groups():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'DELETE':
        group_id = request.args.get('group_id')
        if not group_id:
            return jsonify({'success': False, 'message': 'Group ID required.'}), 400
        cursor.execute(f"DELETE FROM group_members WHERE group_id = {p}", (group_id,))
        cursor.execute(f"DELETE FROM posts WHERE group_id = {p}", (group_id,))
        cursor.execute(f"DELETE FROM events WHERE group_id = {p}", (group_id,))
        cursor.execute(f"DELETE FROM groups WHERE id = {p}", (group_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Group deleted.'})

    cursor.execute('''
        SELECT g.*, u.full_name AS creator_name,
        (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id = g.id) AS member_count
        FROM groups g JOIN users u ON g.user_id = u.id ORDER BY g.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/events', methods=['GET', 'DELETE'])
def admin_manage_events():
    admin, err = require_admin()
    if err:
        return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'DELETE':
        event_id = request.args.get('event_id')
        if not event_id:
            return jsonify({'success': False, 'message': 'Event ID required.'}), 400
        cursor.execute(f"DELETE FROM events WHERE id = {p}", (event_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Event removed.'})

    cursor.execute('''
        SELECT e.*, u.full_name AS creator_name, g.name AS group_name
        FROM events e JOIN users u ON e.user_id = u.id
        LEFT JOIN groups g ON e.group_id = g.id ORDER BY e.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/broadcast', methods=['POST'])
def admin_broadcast_notification():
    admin, err = require_admin()
    if err:
        return err
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'success': False, 'message': 'Broadcast message cannot be empty.'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE user_type != 'Admin'")
    users = cursor.fetchall()

    p = query_param()
    for u in users:
        cursor.execute(f'''
            INSERT INTO notifications (user_id, sender_id, type, target_id, message)
            VALUES ({p}, {p}, 'system', 0, {p})
        ''', (u['id'], session['user_id'], message))
    db.commit()
    return jsonify({'success': True, 'message': f'Broadcast notification sent to {len(users)} members!'})

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
# FRONTEND TEMPLATES (INDEX, AUTH, ADMIN)
# ======================================================================
INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">

<title>{{ meta_title }}</title>
<meta name="description" content="{{ meta_desc }}">

<!-- Open Graph -->
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
  --amber-gold: #d97706;
  --bg-body: #f0f2f5;
  --card-bg: #ffffff;
  --text-dark: #050505;
  --text-muted: #65676b;
  --border-light: #ced0d4;
}

* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color:transparent; }
body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; padding-bottom: 70px; }

#toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; }
.toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
.toast.success { background: var(--emerald-green); }
.toast.error { background: #ef4444; }

header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-box { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.brand-title { font-size: 1.15rem; font-weight: 800; color: var(--navy-blue); line-height: 1.1; }
.brand-title span { color: var(--fb-blue); }

.header-actions { display: flex; align-items: center; gap: 8px; }
.icon-btn { background: #f0f2f5; border: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.95rem; color: var(--navy-blue); position: relative; cursor: pointer; }
.badge-count { position: absolute; top: -2px; right: -2px; background: #ef4444; color: #fff; font-size: 0.65rem; font-weight: 800; padding: 2px 6px; border-radius: 10px; }

.top-nav-pills { display: flex; gap: 6px; padding: 0.6rem 0.5rem; background: #fff; border-bottom: 1px solid var(--border-light); overflow-x: auto; scrollbar-width: none; }
.top-nav-pills::-webkit-scrollbar { display: none; }
.nav-pill { padding: 6px 14px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; background: #f0f2f5; color: var(--text-muted); cursor: pointer; flex-shrink: 0; display: flex; align-items: center; gap: 6px; min-height: 36px; }
.nav-pill.active { background: var(--fb-blue); color: #fff; }

.search-container { padding: 0.5rem 1rem; background: #fff; border-bottom: 1px solid var(--border-light); }
.search-input { width: 100%; padding: 10px 14px; border-radius: 20px; border: 1.5px solid var(--border-light); font-size: 0.85rem; outline: none; background: #f0f2f5; }

.app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.75rem; flex: 1; }
.view-section { display: none; }
.view-section.active { display: block; }
.card { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 1rem; margin-bottom: 0.85rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

/* FACEBOOK STYLE GROUP COVER & AVATAR */
.fb-group-banner { height: 160px; background: linear-gradient(135deg, #1877f2, #0b1e36); border-radius: 12px 12px 0 0; position: relative; margin: -1rem -1rem 45px -1rem; background-size: cover; background-position: center; }
.fb-group-avatar { position: absolute; bottom: -35px; left: 16px; width: 75px; height: 75px; border-radius: 16px; border: 4px solid #fff; background: var(--fb-blue); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; box-shadow: 0 4px 10px rgba(0,0,0,0.15); }
.fb-tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--border-light); margin-bottom: 12px; overflow-x: auto; }
.fb-tab { padding: 8px 12px; font-size: 0.82rem; font-weight: 700; color: var(--text-muted); cursor: pointer; border-bottom: 3px solid transparent; }
.fb-tab.active { color: var(--fb-blue); border-bottom-color: var(--fb-blue); }

.feed-post { background: #fff; border: 1px solid var(--border-light); border-radius: 12px; padding: 0.88rem; margin-bottom: 0.85rem; }
.post-header { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 0.9rem; flex-shrink: 0; background-size: cover; background-position: center; }

.btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; min-height: 42px; }
.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.75rem; }
.form-group label { font-size: 0.8rem; font-weight: 700; }
.form-control { padding: 10px 12px; border-radius: 8px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; background: #fff; }

.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.65); z-index:9999; align-items:center; justify-content:center; padding:0.75rem; backdrop-filter: blur(2px); }
.modal-body-scroll { max-height: 85vh; overflow-y: auto; -webkit-overflow-scrolling: touch; border-radius: 16px; }

.mobile-bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1px solid var(--border-light); display: flex; justify-content: space-around; padding: 6px 0; z-index: 1000; height: 60px; padding-bottom: env(safe-area-inset-bottom, 0px); }
.nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.7rem; font-weight: 700; flex: 1; cursor: pointer; text-decoration: none; position: relative; }
.nav-item i { font-size: 1.2rem; margin-bottom: 2px; }
.nav-item.active { color: var(--fb-blue); }

.app-footer { background: #fff; border-top: 1px solid var(--border-light); padding: 1rem; text-align: center; font-size: 0.78rem; color: var(--text-muted); margin-top: 2rem; }
.app-footer a { color: var(--fb-blue); text-decoration: none; font-weight: 700; }
</style>

<script>
window.INITIAL_DEEP_LINK_DATA = {{ deep_link_json | safe }};
</script>
</head>
<body>
<div id="toast-container"></div>

<header>
  <div class="brand-box" onclick="switchNav('feed')">
    <div class="brand-title">IJEBU <span>CONNECT</span></div>
  </div>
  <div class="header-actions">
    <button class="icon-btn" onclick="openNotifs()"><i class="fa-solid fa-bell"></i><span class="badge-count" id="notif-badge" style="display:none;">0</span></button>
    <div id="header-auth"></div>
  </div>
</header>

<div class="top-nav-pills">
  <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Feed</div>
  <div class="nav-pill" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-users-rectangle"></i> Groups</div>
  <div class="nav-pill" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days"></i> Events</div>
  <div class="nav-pill" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-pill" data-nav="beauty" onclick="switchNav('beauty')"><i class="fa-solid fa-scissors"></i> Beauty</div>
  <div class="nav-pill" data-nav="jobs" onclick="switchNav('jobs')"><i class="fa-solid fa-briefcase"></i> Jobs</div>
  <div class="nav-pill" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
  <div class="nav-pill" id="admin-pill" style="display:none;" onclick="window.location.href='/admin'"><i class="fa-solid fa-gear"></i> Admin Panel</div>
</div>

<div class="search-container">
  <input type="text" class="search-input" id="global-search-input" placeholder="🔍 Search Facebook groups, people, market, events..." onkeyup="handleSearch()">
</div>

<div class="app-container">
  <div id="deep-link-target-container"></div>

  <!-- SEARCH RESULTS VIEW -->
  <div id="view-search" class="view-section">
    <h3 style="font-size:1rem;margin-bottom:8px;">Search Results</h3>
    <div id="search-results-container"></div>
  </div>

  <!-- NOTIFICATIONS VIEW -->
  <div id="view-notifs" class="view-section">
    <h3 style="font-size:1rem;margin-bottom:8px;">Notifications</h3>
    <div id="notifs-container"></div>
  </div>

  <!-- SOCIAL FEED -->
  <div id="view-feed" class="view-section active">
    <div class="card">
      <div id="composer-user-bar" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;"></div>
      <form onsubmit="handlePostSubmit(event, 'Social')">
        <textarea class="form-control" id="post-content" rows="2" placeholder="What's on your mind?"></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Post Update</button>
      </form>
    </div>
    <div id="feed-posts-container"></div>
  </div>

  <!-- GROUPS HUB -->
  <div id="view-groups" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:1.1rem;font-weight:800;color:var(--navy-blue);">Community Groups</h3>
      <button onclick="openGroupCreateModal()" class="btn-submit" style="width:auto;padding:6px 14px;font-size:0.8rem;">+ Create Group</button>
    </div>
    <div id="groups-container"></div>
  </div>

  <!-- GROUP DETAIL PAGE (FACEBOOK STYLE) -->
  <div id="view-group-detail" class="view-section">
    <button onclick="switchNav('groups')" style="background:#fff;border:1px solid var(--border-light);padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back to Groups</button>

    <div id="group-detail-header" class="card" style="padding-bottom:8px;"></div>

    <div class="fb-tabs">
      <div class="fb-tab active" id="gtab-discussion" onclick="switchGroupTab('discussion')">Discussion</div>
      <div class="fb-tab" id="gtab-events" onclick="switchGroupTab('events')">Events</div>
      <div class="fb-tab" id="gtab-members" onclick="switchGroupTab('members')">Members</div>
      <div class="fb-tab" id="gtab-about" onclick="switchGroupTab('about')">About</div>
    </div>

    <!-- GROUP TAB CONTENT -->
    <div id="group-sec-discussion">
      <div id="group-post-composer" class="card" style="display:none;">
        <h4 style="font-size:0.88rem; font-weight:800; margin-bottom:6px;">Create a Group Post</h4>
        <form onsubmit="handleGroupPostSubmit(event)">
          <input type="hidden" id="active-group-id" value="0">
          <textarea class="form-control" id="group-post-content" rows="2" placeholder="Write something to the group..."></textarea>
          <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
            <input type="file" id="group-post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
          </div>
          <button type="submit" class="btn-submit">Publish Post</button>
        </form>
      </div>
      <div id="group-posts-container"></div>
    </div>

    <div id="group-sec-events" style="display:none;">
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h4 style="font-size:0.9rem;font-weight:800;">Group Events</h4>
          <button onclick="openCreateEventModal()" class="btn-submit" style="width:auto;padding:4px 10px;font-size:0.75rem;">+ Create Event</button>
        </div>
      </div>
      <div id="group-events-container"></div>
    </div>

    <div id="group-sec-members" style="display:none;">
      <div class="card" id="group-members-container"></div>
    </div>

    <div id="group-sec-about" style="display:none;">
      <div class="card" id="group-about-container"></div>
    </div>
  </div>

  <!-- EVENTS HUB VIEW -->
  <div id="view-events" class="view-section">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">📅 Local Events & Festivals</h3>
        <button onclick="openCreateEventModal()" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;">+ Post Event</button>
      </div>
    </div>
    <div id="events-feed-container"></div>
  </div>

  <!-- MARKETPLACE HUB -->
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

  <!-- JOBS DIRECTORY -->
  <div id="view-jobs" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;">Jobs & Artisans</h3>
      <button onclick="startSellItem('Jobs')" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.78rem;">+ Post Skill</button>
    </div>
    <div id="jobs-container" class="card"></div>
  </div>

  <!-- DATING MATCH -->
  <div id="view-dating" class="view-section">
    <div class="card" style="background:linear-gradient(135deg, #4f46e5, #7c3aed);color:#fff;">
      <h3 style="font-weight:800;margin-bottom:4px;">❤️ Ijebu Singles Match</h3>
      <p style="font-size:0.78rem;opacity:0.9;margin-bottom:8px;">Connect with verified singles.</p>
      <button onclick="openDatingSettingsModal()" style="background:#fff;color:#4f46e5;border:none;padding:6px 12px;border-radius:8px;font-weight:800;font-size:0.75rem;">Set Up Profile</button>
    </div>
    <div id="dating-matches-container"></div>
  </div>

  <!-- CHAT PAGE -->
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

  <!-- MEMBER PROFILE VIEW -->
  <div id="view-profile" class="view-section">
    <button onclick="switchNav('feed')" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back</button>
    <div id="profile-wall-container"></div>
  </div>
</div>

<!-- CREATE / EDIT GROUP MODAL -->
<div id="group-create-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);" id="group-modal-title">Create Facebook Group</h3>
      <button onclick="closeGroupModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleGroupSubmit(event)">
      <input type="hidden" id="edit-group-id" value="0">
      <div class="form-group"><label>Group Name</label><input type="text" id="grp-name" class="form-control" placeholder="e.g. Ijebu Traders Hub" required></div>
      <div class="form-group"><label>Group Profile Picture (Avatar)</label><input type="file" id="grp-avatar-file" class="form-control" accept="image/*"></div>
      <div class="form-group"><label>Group Cover Banner Photo</label><input type="file" id="grp-cover-file" class="form-control" accept="image/*"></div>
      <div class="form-group"><label>Description</label><textarea id="grp-desc" class="form-control" rows="2" placeholder="Describe the purpose of this group..."></textarea></div>
      <button type="submit" class="btn-submit">Save Group</button>
    </form>
  </div>
</div>

<!-- CREATE EVENT MODAL -->
<div id="event-create-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Create Event</h3>
      <button onclick="closeEventModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleEventSubmit(event)">
      <div class="form-group"><label>Event Title</label><input type="text" id="evt-title" class="form-control" placeholder="e.g. Ojude Oba Festival Prep" required></div>
      <div class="form-group"><label>Date & Time</label><input type="text" id="evt-date" class="form-control" placeholder="e.g. Saturday, Oct 25 at 4:00 PM"></div>
      <div class="form-group"><label>Venue / Location</label><input type="text" id="evt-location" class="form-control" placeholder="e.g. Awujale Pavilion, Ijebu Ode"></div>
      <div class="form-group"><label>Banner Image</label><input type="file" id="evt-image-file" class="form-control" accept="image/*"></div>
      <div class="form-group"><label>Description</label><textarea id="evt-desc" class="form-control" rows="2" placeholder="Event details..."></textarea></div>
      <button type="submit" class="btn-submit">Publish Event</button>
    </form>
  </div>
</div>

<!-- FOOTER -->
<footer class="app-footer">
  <p><strong>{{ company_name }}</strong> &copy; 2026. All Rights Reserved.</p>
  <p><i class="fa-solid fa-envelope"></i> Email: <a href="mailto:{{ contact_email }}">{{ contact_email }}</a></p>
</footer>

<!-- MOBILE BOTTOM NAVIGATION -->
<div class="mobile-bottom-nav">
  <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Home</div>
  <div class="nav-item" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-users-rectangle"></i> Groups</div>
  <div class="nav-item" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days"></i> Events</div>
  <div class="nav-item" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-item" data-nav="chat" onclick="switchNav('chat')">
    <i class="fa-solid fa-comments"></i> Chat
    <span class="badge-count" id="chat-tab-badge" style="display:none; top:-4px; right:12px;">0</span>
  </div>
</div>

<script>
let currentUser = null;
let currentChatUser = null;
let activeGroupId = 0;

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
  const bottomItem = document.querySelector(`.nav-item[data-nav="${target}"]`);
  if(bottomItem) bottomItem.classList.add('active');

  const view = document.getElementById(`view-${target}`);
  if(view) view.classList.add('active');

  if(target === 'feed') loadPosts('Social', 'feed-posts-container');
  if(target === 'groups') loadGroups();
  if(target === 'events') loadEventsFeed();
  if(target === 'market') loadCategoryListings('Market', 'products-container');
  if(target === 'beauty') loadCategoryListings('Beauty', 'beauty-container');
  if(target === 'jobs') loadCategoryListings('Jobs', 'jobs-container');
  if(target === 'chat') { loadChatPartners(); }
}

async function checkSession() {
  try {
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if(data.logged_in) {
      currentUser = data.user;
      renderHeaderAuth();
      if(currentUser.user_type === 'Admin') {
        document.getElementById('admin-pill').style.display = 'flex';
      }
    } else {
      currentUser = null;
      renderHeaderAuth();
    }
  } catch(e){}
}

function renderHeaderAuth() {
  const box = document.getElementById('header-auth');
  if(currentUser) {
    box.innerHTML = `<button onclick="openProfile('${currentUser.username}')" style="background:#f0f2f5;border:none;padding:6px 10px;border-radius:16px;font-weight:700;font-size:0.75rem;">@${currentUser.username}</button>`;
  } else {
    box.innerHTML = `<a href="/auth" style="background:var(--fb-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
  }
}

/* GROUPS & FACEBOOK STYLE INTERFACE */
async function loadGroups() {
  const res = await fetch('/api/groups');
  const groups = await res.json();
  const c = document.getElementById('groups-container');
  if(!groups.length) { c.innerHTML = '<div class="card">No groups created yet. Click "+ Create Group" to start one!</div>'; return; }

  c.innerHTML = groups.map(g => {
    const avatar = g.avatar_url ? `<img src="${g.avatar_url}" style="width:50px;height:50px;border-radius:12px;object-fit:cover;">` : `<div class="avatar" style="width:50px;height:50px;border-radius:12px;background:var(--fb-blue);"><i class="fa-solid fa-users"></i></div>`;
    return `
      <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
        <div style="display:flex;align-items:center;gap:12px;cursor:pointer;" onclick="openGroupDetail(${g.id})">
          ${avatar}
          <div>
            <h4 style="font-weight:800;font-size:0.95rem;color:var(--navy-blue);">${g.name}</h4>
            <p style="font-size:0.75rem;color:var(--text-muted);">${g.member_count} Members</p>
          </div>
        </div>
        <button onclick="openGroupDetail(${g.id})" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;">Visit Group</button>
      </div>
    `;
  }).join('');
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
    <div style="display:flex;justify-content:space-between;align-items:flex-end;">
      <div>
        <h2 style="font-size:1.2rem;font-weight:800;">${g.name}</h2>
        <p style="font-size:0.78rem;color:var(--text-muted);">${g.member_count} Members • Public Group</p>
      </div>
      <div>
        ${g.is_creator ? `<button onclick="openGroupEditModal(${g.id}, '${g.name}', '${g.description}')" style="background:#f0f2f5;border:none;padding:6px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-right:4px;">Edit</button>` : ''}
        <button onclick="joinGroup(${g.id})" class="btn-submit" style="width:auto;padding:6px 12px;font-size:0.75rem;background:${g.is_member ? '#ef4444' : 'var(--fb-blue)'};">
          ${g.is_member ? 'Leave Group' : 'Join Group'}
        </button>
      </div>
    </div>
  `;

  document.getElementById('group-about-container').innerHTML = `<p style="font-size:0.85rem;">${g.description || 'No group description available.'}</p>`;
  if(g.is_member) {
    document.getElementById('group-post-composer').style.display = 'block';
  } else {
    document.getElementById('group-post-composer').style.display = 'none';
  }

  switchGroupTab('discussion');
}

function switchGroupTab(tab) {
  document.querySelectorAll('.fb-tab').forEach(t => t.classList.remove('active'));
  document.getElementById(`gtab-${tab}`).classList.add('active');

  document.getElementById('group-sec-discussion').style.display = tab === 'discussion' ? 'block' : 'none';
  document.getElementById('group-sec-events').style.display = tab === 'events' ? 'block' : 'none';
  document.getElementById('group-sec-members').style.display = tab === 'members' ? 'block' : 'none';
  document.getElementById('group-sec-about').style.display = tab === 'about' ? 'block' : 'none';

  if(tab === 'discussion') loadPosts('Social', 'group-posts-container', activeGroupId);
  if(tab === 'events') loadGroupEvents(activeGroupId);
  if(tab === 'members') loadGroupMembers(activeGroupId);
}

async function loadGroupMembers(groupId) {
  const res = await fetch(`/api/groups/${groupId}/members`);
  const members = await res.json();
  const c = document.getElementById('group-members-container');
  c.innerHTML = members.map(m => `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--border-light);">
      <div class="avatar" style="width:36px;height:36px;background:var(--fb-blue);">${m.avatar_url ? `<img src="${m.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : m.full_name.charAt(0)}</div>
      <div>
        <div style="font-weight:800;font-size:0.85rem;">${m.full_name}</div>
        <div style="font-size:0.7rem;color:var(--text-muted);">@${m.username}</div>
      </div>
    </div>
  `).join('');
}

async function loadGroupEvents(groupId) {
  const res = await fetch(`/api/events?group_id=${groupId}`);
  const events = await res.json();
  const c = document.getElementById('group-events-container');
  if(!events.length) { c.innerHTML = '<div class="card">No scheduled events in this group yet.</div>'; return; }

  c.innerHTML = events.map(e => `
    <div class="card">
      ${e.image_url ? `<img src="${e.image_url}" style="width:100%;border-radius:8px;max-height:160px;object-fit:cover;margin-bottom:8px;">` : ''}
      <h4 style="font-weight:800;font-size:0.95rem;color:var(--navy-blue);">${e.title}</h4>
      <p style="font-size:0.78rem;color:var(--emerald-green);font-weight:700;">📅 ${e.event_date || 'Date TBD'} • 📍 ${e.location || 'Ijebu'}</p>
      <p style="font-size:0.82rem;margin-top:4px;">${e.description}</p>
    </div>
  `).join('');
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
      <p style="font-size:0.78rem;color:var(--emerald-green);font-weight:700;">📅 ${e.event_date || 'Upcoming'} • 📍 ${e.location || 'Ijebu Land'}</p>
      <p style="font-size:0.82rem;margin-top:4px;">${e.description}</p>
      <p style="font-size:0.7rem;color:var(--text-muted);margin-top:6px;">Posted by @${e.creator_username} ${e.group_name ? `in ${e.group_name}` : ''}</p>
    </div>
  `).join('');
}

/* FILE UPLOADER UTILITY */
async function uploadSelectedFile(fileInput) {
  if(!fileInput || !fileInput.files[0]) return {url:'', is_video: false};
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const res = await fetch('/api/upload', {method:'POST', body: formData});
  const data = await res.json();
  return data.success ? {url: data.url, is_video: data.is_video} : {url:'', is_video: false};
}

/* EVENT CREATION */
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
      group_id: activeGroupId
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    closeEventModal();
    if(activeGroupId > 0) loadGroupEvents(activeGroupId);
    else loadEventsFeed();
  }
}

/* GROUP CREATION / MODALS */
function openGroupCreateModal() {
  document.getElementById('edit-group-id').value = "0";
  document.getElementById('group-modal-title').innerText = "Create Facebook Group";
  document.getElementById('group-create-modal').style.display = 'flex';
}
function closeGroupModal() { document.getElementById('group-create-modal').style.display = 'none'; }

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

/* FEED & POSTS ENGINE */
async function loadPosts(postType, containerId, groupId = 0) {
  const res = await fetch(`/api/posts?type=${postType}&group_id=${groupId}`);
  const posts = await res.json();
  const container = document.getElementById(containerId);
  if(!posts.length) {
    container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No posts here yet.</div>`;
    return;
  }
  container.innerHTML = posts.map(p => renderPostCard(p)).join('');
}

function renderPostCard(p) {
  let mediaHtml = '';
  if(p.video_url) mediaHtml = `<video src="${p.video_url}" controls style="width:100%;border-radius:8px;margin-top:6px;"></video>`;
  else if(p.image_url) mediaHtml = `<img src="${p.image_url}" loading="lazy" style="width:100%;border-radius:8px;margin-top:6px;">`;

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
      </div>
      <div style="font-size:0.88rem;line-height:1.4;">${p.content}</div>
      ${mediaHtml}
    </div>`;
}

async function handlePostSubmit(e, postType, groupId = 0) {
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
    body: JSON.stringify({content, image_url: imageUrl, video_url: videoUrl, post_type: postType, group_id: groupId})
  });
  const data = await res.json();
  if(data.success) {
    showToast(data.message);
    document.getElementById('post-content').value = '';
    loadPosts(postType, 'feed-posts-container');
  } else {
    showToast(data.message, 'error');
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
.auth-card { background: #fff; border: 1px solid var(--border-light); border-radius: 18px; padding: 1.5rem; max-width: 440px; width: 100%; text-align: center; box-shadow:0 8px 24px rgba(0,0,0,0.05); }
.brand { font-size: 1.5rem; font-weight: 800; color: var(--fb-blue); margin-bottom: 0.2rem; }
.auth-tabs { display: flex; gap: 4px; margin: 1rem 0; background: #f0f2f5; padding: 4px; border-radius: 10px; }
.auth-tab { flex: 1; padding: 8px; border-radius: 6px; border: none; font-weight: 700; font-size: 0.8rem; cursor: pointer; color: #64748b; background: transparent; }
.auth-tab.active { background: #fff; color: var(--fb-blue); }
.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.85rem; text-align: left; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }
.btn-submit { background: var(--fb-blue); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; margin-top: 4px; }
.app-footer { margin-top: 1.5rem; text-align: center; font-size: 0.75rem; color: #64748b; }
</style>
</head>
<body>
<div class="auth-card">
  <div class="brand">IJEBU CONNECT</div>
  <p style="font-size:0.78rem;color:#64748b;">Sign in to join groups, connect, and trade.</p>
  <div class="auth-tabs">
    <button class="auth-tab active" id="tab-login" onclick="toggleAuth('login')">Sign In</button>
    <button class="auth-tab" id="tab-register" onclick="toggleAuth('register')">Register Free</button>
  </div>
  <form id="form-login" onsubmit="handleLogin(event)">
    <div class="form-group"><label>Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
    <div class="form-group"><label>Password</label><input type="password" id="login-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit">Sign In</button>
  </form>
  <form id="form-register" style="display:none;" onsubmit="handleRegister(event)">
    <div class="form-group"><label>Full Name</label><input type="text" id="reg-name" class="form-control" required></div>
    <div class="form-group"><label>Phone Number</label><input type="tel" id="reg-phone" class="form-control" required></div>
    <div class="form-group"><label>Username</label><input type="text" id="reg-uname" class="form-control" required></div>
    <div class="form-group"><label>Password</label><input type="password" id="reg-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit">Create Free Account</button>
  </form>
</div>

<footer class="app-footer">
  <p><strong>Willys Media World</strong> &copy; 2026</p>
  <p>willysmediaworld@gmail.com</p>
</footer>

<script>
function toggleAuth(mode) {
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
  if(mode === 'login') {
    document.getElementById('tab-login').classList.add('active');
    document.getElementById('form-login').style.display = 'block';
    document.getElementById('form-register').style.display = 'none';
  } else {
    document.getElementById('tab-register').classList.add('active');
    document.getElementById('form-login').style.display = 'none';
    document.getElementById('form-register').style.display = 'block';
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
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      full_name: document.getElementById('reg-name').value,
      phone: document.getElementById('reg-phone').value,
      username: document.getElementById('reg-uname').value,
      password: document.getElementById('reg-pword').value
    })
  });
  const data = await res.json();
  if(data.success) { alert(data.message); toggleAuth('login'); }
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
  <div class="card"><div class="val" id="st-partners">0</div><div class="lbl">CPN Partners</div></div>
  <div class="card"><div class="val" id="st-groups">0</div><div class="lbl">Facebook Groups</div></div>
  <div class="card"><div class="val" id="st-events">0</div><div class="lbl">Total Events</div></div>
  <div class="card"><div class="val" id="st-wallets">₦0.00</div><div class="lbl">Member Balances</div></div>
</div>

<div class="admin-tabs">
  <button class="admin-tab active" onclick="switchAdminTab('members')">Members Management</button>
  <button class="admin-tab" onclick="switchAdminTab('groups')">Manage Groups</button>
  <button class="admin-tab" onclick="switchAdminTab('events')">Manage Events</button>
  <button class="admin-tab" onclick="switchAdminTab('broadcast')">System Broadcast</button>
  <button class="admin-tab" onclick="switchAdminTab('partners')">CPN Claims</button>
  <button class="admin-tab" onclick="switchAdminTab('payouts')">Bank Cashouts</button>
</div>

<div id="adm-members" class="tab-sec active">
  <h3>Registered Platform Members</h3>
  <table>
    <thead><tr><th>Full Name</th><th>Username</th><th>Phone</th><th>Type</th><th>Verified</th><th>Action</th></tr></thead>
    <tbody id="members-body"></tbody>
  </table>
</div>

<div id="adm-groups" class="tab-sec">
  <h3>Community Groups Moderation</h3>
  <table>
    <thead><tr><th>Group Name</th><th>Creator</th><th>Members</th><th>Action</th></tr></thead>
    <tbody id="groups-body"></tbody>
  </table>
</div>

<div id="adm-events" class="tab-sec">
  <h3>Platform Events Moderation</h3>
  <table>
    <thead><tr><th>Title</th><th>Date</th><th>Creator</th><th>Action</th></tr></thead>
    <tbody id="events-body"></tbody>
  </table>
</div>

<div id="adm-broadcast" class="tab-sec">
  <h3>Send System Broadcast Notification</h3>
  <div class="card" style="max-width:500px;">
    <form onsubmit="sendBroadcast(event)">
      <label style="font-weight:700;font-size:0.85rem;">Broadcast Message</label>
      <textarea id="bc-msg" style="width:100%;padding:8px;margin:8px 0;border-radius:6px;border:1px solid #cbd5e1;" rows="3" required></textarea>
      <button type="submit" class="btn-act btn-app" style="padding:8px 16px;font-size:0.82rem;">Send Notification to All Users</button>
    </form>
  </div>
</div>

<div id="adm-partners" class="tab-sec">
  <h3>Pending CPN Partner Upgrades</h3>
  <table>
    <thead><tr><th>Member</th><th>Amount</th><th>Reference Note</th><th>Action</th></tr></thead>
    <tbody id="partner-reqs-body"></tbody>
  </table>
</div>

<div id="adm-payouts" class="tab-sec">
  <h3>Bank Cashout Requests</h3>
  <table>
    <thead><tr><th>User</th><th>Amount</th><th>Bank Details</th><th>Action</th></tr></thead>
    <tbody id="payouts-body"></tbody>
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
  document.getElementById('st-partners').innerText = data.total_partners;
  document.getElementById('st-groups').innerText = data.total_groups;
  document.getElementById('st-events').innerText = data.total_events;
  document.getElementById('st-wallets').innerText = '₦' + data.total_partner_wallets.toLocaleString();

  loadMembers();
  loadAdminGroups();
  loadAdminEvents();
  loadPartnerRequests();
  loadPayouts();
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
      <td>${u.is_verified_merchant ? '✅ Yes' : '❌ No'}</td>
      <td>
        <button class="btn-act btn-app" onclick="toggleVerify(${u.id})">Toggle Badge</button>
        ${u.user_type !== 'Admin' ? `<button class="btn-act btn-del" onclick="deleteMember(${u.id})">Delete</button>` : ''}
      </td>
    </tr>
  `).join('');
}

async function toggleVerify(uid) {
  await fetch('/api/admin/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user_id: uid, action: 'toggle_verify'})
  });
  loadMembers();
}

async function deleteMember(uid) {
  if(!confirm('Remove this member completely?')) return;
  await fetch(`/api/admin/users?user_id=${uid}`, {method:'DELETE'});
  loadAdminOverview();
}

async function loadAdminGroups() {
  const res = await fetch('/api/admin/groups');
  const groups = await res.json();
  const body = document.getElementById('groups-body');
  body.innerHTML = groups.map(g => `
    <tr>
      <td><b>${g.name}</b></td>
      <td>@${g.creator_name}</td>
      <td>${g.member_count}</td>
      <td><button class="btn-act btn-del" onclick="deleteGroup(${g.id})">Delete Group</button></td>
    </tr>
  `).join('');
}

async function deleteGroup(gid) {
  if(!confirm('Delete this group?')) return;
  await fetch(`/api/admin/groups?group_id=${gid}`, {method:'DELETE'});
  loadAdminOverview();
}

async function loadAdminEvents() {
  const res = await fetch('/api/admin/events');
  const events = await res.json();
  const body = document.getElementById('events-body');
  body.innerHTML = events.map(e => `
    <tr>
      <td><b>${e.title}</b></td>
      <td>${e.event_date || 'N/A'}</td>
      <td>@${e.creator_name}</td>
      <td><button class="btn-act btn-del" onclick="deleteEvent(${e.id})">Delete Event</button></td>
    </tr>
  `).join('');
}

async function deleteEvent(eid) {
  if(!confirm('Delete event?')) return;
  await fetch(`/api/admin/events?event_id=${eid}`, {method:'DELETE'});
  loadAdminOverview();
}

async function sendBroadcast(e) {
  e.preventDefault();
  const msg = document.getElementById('bc-msg').value.trim();
  const res = await fetch('/api/admin/broadcast', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg})
  });
  const data = await res.json();
  alert(data.message);
  document.getElementById('bc-msg').value = '';
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
        ` : `<b>${p.status.toUpperCase()}</b>`}
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
# ROUTE HANDLERS WITH DYNAMIC OPEN GRAPH META PREVIEWS
# ======================================================================
@app.route('/')
def index():
    db = get_db()
    cursor = db.cursor()

    host_url = request.host_url
    if not host_url.startswith('https://') and 'localhost' not in host_url and '127.0.0.1' not in host_url:
        host_url = host_url.replace('http://', 'https://')

    meta_title = "Ijebu Connect - Mobile Hub"
    meta_desc = "The unified digital hub connecting sons and daughters of Ijebu land."
    meta_image = f"{host_url.rstrip('/')}/static/uploads/default_preview.jpg"
    meta_url = request.url
    deep_link_data = None

    return render_template_string(
        INDEX_TEMPLATE,
        contact_email=CONTACT_EMAIL,
        company_name=COMPANY_NAME,
        meta_title=meta_title,
        meta_desc=meta_desc,
        meta_image=meta_image,
        meta_url=meta_url,
        deep_link_json=json.dumps(deep_link_data) if deep_link_data else 'null'
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
    