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
        return jsonify({'users': [], 'products': [], 'posts': [], 'groups': []})
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

    return jsonify({'users': users, 'products': products, 'posts': posts, 'groups': groups})

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
    SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_read, m.created_at, u.full_name, u.username
    FROM messages m JOIN users u ON m.sender_id = u.id
    WHERE (m.sender_id = {p} AND m.receiver_id = {p}) OR (m.sender_id = {p} AND m.receiver_id = {p})
    ORDER BY m.id ASC LIMIT 300
    ''', (uid, other_id, other_id, uid))
    messages = [dict(r) for r in cursor.fetchall()]
    return jsonify({'success': True, 'other': dict(other), 'messages': messages, 'me_id': uid})

# ======================================================================
# ADMIN API
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

    cursor.execute("SELECT COALESCE(SUM(wallet_balance), 0) FROM users")
    total_wallets = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM partner_requests WHERE status = 'pending'")
    pending_partners = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM partner_requests WHERE status = 'approved'")
    total_income = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payout_requests WHERE status = 'approved'")
    total_payouts = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE tx_type LIKE '%CPN Commission%'")
    total_commissions = float(cursor.fetchone()[0] or 0)

    admin_net_balance = total_income - total_commissions

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_products': total_products,
        'total_partner_wallets': total_wallets,
        'pending_partners': pending_partners,
        'total_income': total_income,
        'total_payouts': total_payouts,
        'total_commissions': total_commissions,
        'admin_net_balance': admin_net_balance
    })

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
        return jsonify({'success': True, 'message': 'Member and all associated data completely removed.'})

    cursor.execute("SELECT id, full_name, username, phone, user_type, referral_code, created_at FROM users ORDER BY id DESC")
    return jsonify([dict(r) for r in cursor.fetchall()])

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

    cursor.execute('''SELECT p.id, p.content, p.post_type, p.image_url, p.created_at, u.full_name, u.username
    FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.id DESC LIMIT 100''')
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
# FRONTEND TEMPLATE & WHATSAPP/FACEBOOK PREVIEW ENGINE
# ======================================================================
INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">

<!-- COMPREHENSIVE OPENGRAPH & SOCIAL PREVIEW META TAGS -->
<title>{{ meta_title }}</title>
<meta name="description" content="{{ meta_desc }}">

<!-- Open Graph (WhatsApp, Facebook, LinkedIn) -->
<meta property="og:site_name" content="Ijebu Connect">
<meta property="og:title" content="{{ meta_title }}">
<meta property="og:description" content="{{ meta_desc }}">
<meta property="og:image" content="{{ meta_image }}">
<meta property="og:image:secure_url" content="{{ meta_image }}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{{ meta_url }}">
<meta property="og:type" content="website">

<!-- Twitter Cards -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{ meta_title }}">
<meta name="twitter:description" content="{{ meta_desc }}">
<meta name="twitter:image" content="{{ meta_image }}">

<!-- Legacy Image Source (Nairaland & Older Bots) -->
<link rel="image_src" href="{{ meta_image }}">

<!-- Schema.org JSON-LD Structured Data (Google & News Bots) -->
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebPage",
  "name": "{{ meta_title }}",
  "description": "{{ meta_desc }}",
  "image": "{{ meta_image }}",
  "url": "{{ meta_url }}"
}
</script>

<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root {
  --navy-blue: #0b1e36;
  --emerald-green: #059669;
  --amber-gold: #d97706;
  --bg-body: #f8fafc;
  --text-dark: #0f172a;
  --text-muted: #64748b;
  --border-light: #cbd5e1;
}
* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color:transparent; }
body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; padding-bottom: 70px; }

#toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; }
.toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; }
.toast.success { background: var(--emerald-green); }
.toast.error { background: #ef4444; }

header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-box { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.brand-title { font-size: 1.15rem; font-weight: 800; color: var(--navy-blue); line-height: 1.1; }
.brand-title span { color: var(--emerald-green); }
.header-actions { display: flex; align-items: center; gap: 8px; }
.icon-btn { background: #f1f5f9; border: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.95rem; color: var(--navy-blue); position: relative; cursor: pointer; }
.badge-count { position: absolute; top: -2px; right: -2px; background: #ef4444; color: #fff; font-size: 0.65rem; font-weight: 800; padding: 2px 6px; border-radius: 10px; }

.top-nav-pills { display: flex; gap: 6px; padding: 0.6rem 0.5rem; background: #fff; border-bottom: 1px solid var(--border-light); overflow-x: auto; scrollbar-width: none; }
.top-nav-pills::-webkit-scrollbar { display: none; }
.nav-pill { padding: 6px 12px; border-radius: 18px; font-size: 0.78rem; font-weight: 700; background: #f1f5f9; color: var(--text-muted); cursor: pointer; flex-shrink: 0; display: flex; align-items: center; gap: 5px; min-height: 38px; }
.nav-pill.active { background: var(--navy-blue); color: #fff; }

.search-container { padding: 0.5rem 1rem; background: #fff; border-bottom: 1px solid var(--border-light); }
.search-input { width: 100%; padding: 10px 14px; border-radius: 20px; border: 1.5px solid var(--border-light); font-size: 0.85rem; outline: none; background: #f8fafc; }

.mobile-bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1.5px solid var(--border-light); display: flex; justify-content: space-around; padding: 6px 0; z-index: 1000; height: 62px; padding-bottom: env(safe-area-inset-bottom, 0px); }
.nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.7rem; font-weight: 700; flex: 1; cursor: pointer; text-decoration: none; position: relative; }
.nav-item i { font-size: 1.2rem; margin-bottom: 2px; }
.nav-item.active { color: var(--navy-blue); }

.app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.75rem; flex: 1; }
.view-section { display: none; }
.view-section.active { display: block; }
.card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 1rem; margin-bottom: 0.85rem; }

.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.75rem; }
.form-group label { font-size: 0.8rem; font-weight: 700; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; background: #fff; }
.btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; min-height: 44px; }

.feed-post { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 0.88rem; margin-bottom: 0.85rem; content-visibility: auto; contain-intrinsic-size: 140px; }
.post-header { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 0.9rem; flex-shrink: 0; background-size: cover; background-position: center; }
.post-actions { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid var(--border-light); }
.post-action { flex: 1; background: none; border: none; padding: 6px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 4px; min-height: 38px; }

.post-product-badge { background: #ecfdf5; border: 1.5px solid #10b981; border-radius: 12px; padding: 10px; margin-top: 8px; display: flex; justify-content: space-between; align-items: center; }

.fb-cover-banner { height: 110px; background: linear-gradient(135deg, #0b1e36, #1e3a8a); border-radius: 12px 12px 0 0; position: relative; margin: -1rem -1rem 30px -1rem; }
.fb-avatar-wrap { position: absolute; bottom: -25px; left: 16px; width: 64px; height: 64px; border-radius: 50%; border: 3px solid #fff; background: var(--emerald-green); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.4rem; font-weight: 800; }

.product-card { display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--border-light); padding-bottom: 12px; margin-bottom: 12px; }
.product-img-box { width: 70px; height: 70px; border-radius: 12px; background: #f1f5f9; display: flex; align-items: center; justify-content: center; font-size: 1.3rem; overflow: hidden; flex-shrink: 0; }
.product-img-box img, .product-img-box video { width: 100%; height: 100%; object-fit: cover; }
.btn-whatsapp { background: #25d366; color: #fff; border: none; padding: 6px 12px; border-radius: 8px; font-weight: 700; font-size: 0.75rem; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; min-height: 36px; }

.comments-box { background: #f8fafc; border-radius: 12px; padding: 8px; margin-top: 8px; }
.comment-item { border-bottom: 1px solid #e2e8f0; padding: 6px 0; font-size: 0.82rem; }
.comment-item:last-child { border-bottom: none; }
.comment-user { font-weight: 800; color: var(--navy-blue); }

.chat-bubble { max-width:80%; padding:8px 12px; border-radius:14px; margin-bottom:6px; font-size:0.85rem; word-wrap:break-word; }
.chat-bubble.me { background:var(--emerald-green); color:#fff; margin-left:auto; border-bottom-right-radius:2px; }
.chat-bubble.them { background:#fff; border:1.5px solid var(--border-light); border-bottom-left-radius:2px; }

.bank-box { background:#f1f5f9; border:1.5px dashed var(--navy-blue); border-radius:12px; padding:0.88rem; margin:0.75rem 0; font-size:0.85rem; line-height:1.5; }

.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.65); z-index:9999; align-items:center; justify-content:center; padding:0.75rem; backdrop-filter: blur(2px); }
.modal-body-scroll { max-height: 85vh; overflow-y: auto; -webkit-overflow-scrolling: touch; border-radius: 16px; }

.suggestions-scroll { display: flex; gap: 10px; overflow-x: auto; padding: 6px 0; scrollbar-width: none; }
.suggestions-scroll::-webkit-scrollbar { display: none; }
.suggestion-card { min-width: 130px; width: 130px; background: #f8fafc; border: 1px solid var(--border-light); border-radius: 12px; padding: 10px 8px; text-align: center; flex-shrink: 0; }
.suggestion-card .avatar { margin: 0 auto 6px; width: 44px; height: 44px; font-size: 1rem; }
.suggestion-card h5 { font-size: 0.78rem; font-weight: 800; color: var(--navy-blue); line-height: 1.2; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.suggestion-card p { font-size: 0.68rem; color: var(--text-muted); margin-bottom: 6px; }
.suggestion-card button { background: var(--navy-blue); color: #fff; border: none; padding: 4px 10px; border-radius: 6px; font-size: 0.7rem; font-weight: 700; cursor: pointer; width: 100%; min-height: 32px; }

/* SHARED TARGET HIGHLIGHT CARD */
.shared-target-card { border: 2px solid var(--emerald-green) !important; background: #f0fdf4 !important; }
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
  <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-users"></i> Social</div>
  <div class="nav-pill" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-cart-shopping"></i> Market</div>
  <div class="nav-pill" data-nav="beauty" onclick="switchNav('beauty')"><i class="fa-solid fa-scissors" style="color:#d97706;"></i> Beauty</div>
  <div class="nav-pill" data-nav="jobs" onclick="switchNav('jobs')"><i class="fa-solid fa-briefcase" style="color:#2563eb;"></i> Jobs/Skills</div>
  <div class="nav-pill" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days" style="color:#9333ea;"></i> Events</div>
  <div class="nav-pill" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-layer-group"></i> Groups</div>
  <div class="nav-pill" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
  <div class="nav-pill" id="admin-pill" style="display:none;" onclick="window.location.href='/admin'"><i class="fa-solid fa-gear"></i> Admin Panel</div>
</div>

<div class="search-container">
  <input type="text" class="search-input" id="global-search-input" placeholder="🔍 Search people, market, jobs, groups..." onkeyup="handleSearch()">
</div>

<div class="app-container">
  <!-- INSTANT DEEP LINK TARGET CONTAINER (0ms RENDER ON SOCIAL MEDIA CLICK) -->
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
        <textarea class="form-control" id="post-content" rows="2" placeholder="What's happening in Ijebu today?..."></textarea>
        
        <div style="margin: 8px 0; padding: 8px; background: #f1f5f9; border-radius: 10px;">
          <div style="display:flex; align-items:center; gap:8px; cursor:pointer;" onclick="toggleProductAttach()">
            <input type="checkbox" id="attach-product-check">
            <label for="attach-product-check" style="font-size:0.8rem; font-weight:700; cursor:pointer; color:var(--navy-blue);">
              🏷️ Attach Product Advert (Free Trial: 2 Allowed)
            </label>
          </div>
          <div id="product-attach-fields" style="display:none; margin-top:8px;">
            <div style="display:flex; gap:6px; margin-bottom:6px;">
              <input type="text" id="post-prod-title" class="form-control" placeholder="Product / Service Name">
              <input type="number" id="post-prod-price" class="form-control" placeholder="Price (₦)">
            </div>
            <input type="tel" id="post-prod-whatsapp" class="form-control" placeholder="WhatsApp Phone (e.g. 08012345678)">
          </div>
        </div>

        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Publish Update</button>
      </form>
    </div>

    <!-- FRIEND SUGGESTIONS CAROUSEL -->
    <div id="friend-suggestions-wrapper" class="card" style="display:none; padding: 10px 12px; margin-bottom: 0.85rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
        <h4 style="font-size:0.85rem; font-weight:800; color:var(--navy-blue);"><i class="fa-solid fa-user-plus" style="color:var(--emerald-green);"></i> Suggested Connections</h4>
      </div>
      <div id="friend-suggestions-container" class="suggestions-scroll"></div>
    </div>

    <div id="feed-posts-container"></div>
  </div>

  <!-- MARKETPLACE HUB -->
  <div id="view-market" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Marketplace</h3>
      <button onclick="startSellItem('Market')" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ List Item</button>
    </div>
    <div id="products-container" class="card"></div>
  </div>

  <!-- BEAUTY & FASHION -->
  <div id="view-beauty" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Beauty & Fashion Directory</h3>
      <button onclick="startSellItem('Beauty')" style="background:var(--amber-gold);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ Add Service</button>
    </div>
    <div id="beauty-container" class="card"></div>
  </div>

  <!-- JOBS DIRECTORY -->
  <div id="view-jobs" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Jobs & Artisan Directory</h3>
      <button onclick="startSellItem('Jobs')" style="background:#2563eb;color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ Post Job/Skill</button>
    </div>
    <div id="jobs-container" class="card"></div>
  </div>

  <!-- LOCAL EVENTS -->
  <div id="view-events" class="view-section">
    <div class="card">
      <h3 style="font-weight:800;color:var(--navy-blue);margin-bottom:8px;">📅 Local Events & Festivals</h3>
      <form onsubmit="handlePostSubmit(event, 'Event')">
        <textarea class="form-control" id="event-content" rows="2" placeholder="Announce an upcoming party, Ojude Oba, festival..."></textarea>
        <button type="submit" class="btn-submit" style="background:#9333ea;margin-top:8px;">Publish Event</button>
      </form>
    </div>
    <div id="events-container"></div>
  </div>

  <!-- GROUPS HUB -->
  <div id="view-groups" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Community Groups</h3>
      <button onclick="openGroupCreateModal()" style="background:var(--navy-blue);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ Create Group</button>
    </div>
    <div id="groups-container"></div>
  </div>

  <!-- GROUP DETAIL PAGE -->
  <div id="view-group-detail" class="view-section">
    <button onclick="switchNav('groups')" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back to Groups</button>
    <div id="group-detail-header" class="card"></div>
    <div id="group-post-composer" class="card" style="display:none;">
      <h4 style="font-size:0.88rem; font-weight:800; margin-bottom:6px; color:var(--navy-blue);">Post in this Group</h4>
      <form onsubmit="handleGroupPostSubmit(event)">
        <input type="hidden" id="active-group-id" value="0">
        <textarea class="form-control" id="group-post-content" rows="2" placeholder="Share something with group members..."></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="group-post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Publish Group Post</button>
      </form>
    </div>
    <div id="group-posts-container"></div>
  </div>

  <!-- DATING MATCH -->
  <div id="view-dating" class="view-section">
    <div class="card" style="background:linear-gradient(135deg, #4f46e5, #7c3aed);color:#fff;">
      <h3 style="font-weight:800;margin-bottom:4px;">❤️ Ijebu Singles Match</h3>
      <p style="font-size:0.78rem;opacity:0.9;margin-bottom:8px;">Connect with verified singles across Ijebu & Environs.</p>
      <button onclick="openDatingSettingsModal()" style="background:#fff;color:#4f46e5;border:none;padding:6px 12px;border-radius:8px;font-weight:800;font-size:0.75rem;">Set Up Dating Profile</button>
    </div>
    <div id="dating-matches-container"></div>
  </div>

  <!-- CHAT PAGE -->
  <div id="view-chat" class="view-section">
    <div id="chat-list-wrap">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);margin-bottom:8px;">Messages & Conversations</h3>
      <div id="chat-friends-wrapper" style="display:none; margin-bottom: 12px;">
        <div style="font-size:0.75rem; font-weight:700; color:var(--text-muted); margin-bottom:6px;">Message Your Connections</div>
        <div id="chat-friends-container" class="suggestions-scroll"></div>
      </div>
      <div id="chat-partners-container"></div>
    </div>
    <div id="chat-thread-wrap" style="display:none;">
      <button onclick="closeChatThread()" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-size:0.75rem;font-weight:700;margin-bottom:8px;">← Back to Messages</button>
      <div id="chat-thread-header" class="card" style="padding:0.5rem 0.88rem;display:flex;justify-content:space-between;align-items:center;"></div>
      <div id="chat-messages" style="min-height:220px;max-height:50vh;overflow-y:auto;padding:6px 0;"></div>
      <form onsubmit="sendChatMessage(event)" style="position:sticky;bottom:0;background:var(--bg-body);padding:6px 0;">
        <div style="display:flex;gap:6px;">
          <input type="text" id="chat-input" class="form-control" placeholder="Type a message..." style="flex:1;">
          <button type="submit" class="btn-submit" style="width:auto;padding:10px 16px;">Send</button>
        </div>
      </form>
    </div>
  </div>

  <!-- PROFILE VIEW -->
  <div id="view-profile" class="view-section">
    <button onclick="switchNav('feed')" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;margin-bottom:8px;">← Back</button>
    <div id="profile-wall-container"></div>
  </div>
</div>

<!-- CREATE GROUP MODAL -->
<div id="group-create-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:420px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Create Community Group</h3>
      <button onclick="closeGroupModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleGroupSubmit(event)">
      <div class="form-group"><label>Group Name</label><input type="text" id="grp-name" class="form-control" placeholder="e.g. Ijebu Traders Network" required></div>
      <div class="form-group"><label>Group Logo / Profile Photo</label><input type="file" id="grp-avatar-file" class="form-control" accept="image/*"></div>
      <div class="form-group"><label>Group Cover Photo</label><input type="file" id="grp-cover-file" class="form-control" accept="image/*"></div>
      <div class="form-group"><label>Description</label><textarea id="grp-desc" class="form-control" rows="2" placeholder="Tell members what this community group is all about..."></textarea></div>
      <button type="submit" class="btn-submit">Create Group</button>
    </form>
  </div>
</div>

<!-- EDIT FULL MEMBER PROFILE MODAL -->
<div id="edit-profile-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:460px; width:100%;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
      <h3 style="font-weight:800; color:var(--navy-blue);">Edit Member Profile Details</h3>
      <button onclick="closeEditProfileModal()" style="background:none; border:none; font-size:1.5rem; cursor:pointer;">&times;</button>
    </div>
    <form onsubmit="handleProfileUpdateSubmit(event)">
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" id="edit-fullname" class="form-control" required>
      </div>
      <div class="form-group">
        <label>Phone Number</label>
        <input type="tel" id="edit-phone" class="form-control" required>
      </div>
      <div class="form-group">
        <label>Occupation / Business</label>
        <input type="text" id="edit-occupation" class="form-control" placeholder="e.g. Fashion Designer, Trader">
      </div>
      <div style="display:flex; gap:10px;">
        <div class="form-group" style="flex:1;">
          <label>Age</label>
          <input type="number" id="edit-age" class="form-control" min="16" max="100">
        </div>
        <div class="form-group" style="flex:1;">
          <label>Gender</label>
          <select id="edit-gender" class="form-control">
            <option value="Male">Male</option>
            <option value="Female">Female</option>
            <option value="Unspecified">Unspecified</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Bio / About You</label>
        <textarea id="edit-bio-text" class="form-control" rows="2" placeholder="Tell members about yourself..."></textarea>
      </div>
      <div class="form-group">
        <label>Profile Picture (Avatar)</label>
        <input type="file" id="edit-avatar-file" class="form-control" accept="image/*">
      </div>
      <div class="form-group">
        <label>Cover Photo Banner</label>
        <input type="file" id="edit-cover-file" class="form-control" accept="image/*">
      </div>
      <button type="submit" class="btn-submit">Save All Profile Changes</button>
    </form>
  </div>
</div>

<!-- CPN UPGRADE MODAL -->
<div id="cpn-upgrade-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Become CPN Partner (₦2,000)</h3>
      <button onclick="closeCPNModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <p style="font-size:0.82rem;color:var(--text-muted);line-height:1.4;">Unlock unlimited directory listings and earn <strong>10% Tier-1 & 5% Tier-2 referral rewards</strong> on traders you invite!</p>
    <div class="bank-box">
      <strong>🏦 Bank Transfer Details:</strong><br>
      Bank Name: <b>OPay</b><br>
      Account Number: <b style="color:var(--emerald-green);font-size:1rem;">09018363715</b><br>
      Account Name: <b>Rotimi Williams Oladele</b><br>
      Amount: <b style="color:var(--amber-gold);">₦2,000</b>
    </div>
    <form onsubmit="handleClaimBankTransfer(event)">
      <div class="form-group">
        <label>Sender Name / Reference Note</label>
        <input type="text" id="cpn-ref-note" class="form-control" placeholder="e.g. Paid from OPay / John Doe" required>
      </div>
      <button type="submit" class="btn-submit">Submit Payment Claim</button>
    </form>
  </div>
</div>

<!-- SELL / LISTING MODAL -->
<div id="sell-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:440px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);" id="modal-sell-title">Publish Listing</h3>
      <button onclick="closeSellModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleProductSubmit(event)">
      <input type="hidden" id="prod-type" value="Market">
      <div class="form-group"><label>Title</label><input type="text" class="form-control" id="prod-title" required></div>
      <div class="form-group"><label>Category</label><input type="text" class="form-control" id="prod-category" required></div>
      <div class="form-group"><label>Price (₦)</label><input type="number" class="form-control" id="prod-price" required></div>
      <div class="form-group"><label>WhatsApp Contact</label><input type="text" class="form-control" id="prod-whatsapp" required></div>
      <div class="form-group"><label>Upload Photo/Video</label><input type="file" id="prod-img-file" class="form-control" accept="image/*,video/*"></div>
      <div class="form-group"><label>Description</label><textarea class="form-control" id="prod-desc" rows="2"></textarea></div>
      <button type="submit" class="btn-submit">Publish Listing</button>
    </form>
  </div>
</div>

<!-- DATING SETTINGS MODAL -->
<div id="dating-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:420px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">❤️ Dating Profile Settings</h3>
      <button onclick="closeDatingModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handleDatingProfileSubmit(event)">
      <div class="form-group"><label>Age</label><input type="number" id="dt-age" class="form-control" value="24" required></div>
      <div class="form-group"><label>Gender</label><select id="dt-gender" class="form-control"><option value="Female">Female</option><option value="Male">Male</option></select></div>
      <div class="form-group"><label>Looking For</label><select id="dt-intent" class="form-control"><option value="Dating & Relationship">Dating & Relationship</option><option value="Marriage">Marriage</option><option value="Networking & Friends">Networking & Friends</option></select></div>
      <div class="form-group"><label>Occupation</label><input type="text" id="dt-occupation" class="form-control"></div>
      <div class="form-group"><label>Bio</label><textarea id="dt-bio" class="form-control" rows="2"></textarea></div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:10px;"><input type="checkbox" id="dt-active" checked><label for="dt-active">Show on Match Feed</label></div>
      <button type="submit" class="btn-submit" style="background:#4f46e5;">Save Dating Profile</button>
    </form>
  </div>
</div>

<!-- CASHOUT MODAL -->
<div id="cashout-modal" class="modal-overlay">
  <div class="card modal-body-scroll" style="max-width:400px;width:100%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
      <h3 style="font-weight:800;color:var(--navy-blue);">Bank Cashout</h3>
      <button onclick="closeCashoutModal()" style="background:none;border:none;font-size:1.5rem;">&times;</button>
    </div>
    <form onsubmit="handlePayoutRequest(event)">
      <div class="form-group"><label>Amount (₦)</label><input type="number" class="form-control" id="payout-amount" placeholder="Min 1000" required></div>
      <div class="form-group"><label>Bank Name</label><input type="text" class="form-control" id="payout-bank" placeholder="e.g. GTBank / OPay" required></div>
      <div class="form-group"><label>Account Number</label><input type="text" class="form-control" id="payout-acc-num" placeholder="10 Digits" required></div>
      <div class="form-group"><label>Account Name</label><input type="text" class="form-control" id="payout-acc-name" required></div>
      <button type="submit" class="btn-submit">Submit Cashout Request</button>
    </form>
  </div>
</div>

<!-- MOBILE BOTTOM NAVIGATION -->
<div class="mobile-bottom-nav">
  <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Home</div>
  <div class="nav-item" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-item" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-layer-group"></i> Groups</div>
  <div class="nav-item" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
  <div class="nav-item" data-nav="chat" onclick="switchNav('chat')">
    <i class="fa-solid fa-comments"></i> Chat
    <span class="badge-count" id="chat-tab-badge" style="display:none; top:-4px; right:12px;">0</span>
  </div>
</div>

<script>
let currentUser = null;
let currentChatUser = null;

function showToast(msg, type = 'success') {
  const box = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerText = msg;
  box.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function formatNaira(val) {
  return '₦' + parseFloat(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2 });
}

function toggleProductAttach() {
  const check = document.getElementById('attach-product-check');
  const fields = document.getElementById('product-attach-fields');
  fields.style.display = check.checked ? 'block' : 'none';
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

  if(target === 'feed') { loadPosts('Social', 'feed-posts-container'); loadFriendSuggestions(); }
  if(target === 'market') loadCategoryListings('Market', 'products-container');
  if(target === 'beauty') loadCategoryListings('Beauty', 'beauty-container');
  if(target === 'jobs') loadCategoryListings('Jobs', 'jobs-container');
  if(target === 'events') loadPosts('Event', 'events-container');
  if(target === 'groups') loadGroups();
  if(target === 'dating') loadDatingMatches();
  if(target === 'chat') { loadChatPartners(); refreshUnread(); }
}

async function checkSession() {
  try {
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if(data.logged_in) {
      currentUser = data.user;
      renderHeaderAuth();
      renderComposerUserBar();
      if(currentUser.user_type === 'Admin') {
        document.getElementById('admin-pill').style.display = 'flex';
      }
      if(currentUser.unread_notifs > 0) {
        const badge = document.getElementById('notif-badge');
        badge.innerText = currentUser.unread_notifs;
        badge.style.display = 'block';
      }
      refreshUnread();
      loadFriendSuggestions();
    } else {
      currentUser = null;
      renderHeaderAuth();
    }
  } catch(e){}
}

function renderHeaderAuth() {
  const box = document.getElementById('header-auth');
  if(currentUser) {
    box.innerHTML = `
      <button onclick="openProfile('${currentUser.username}')" style="background:#f1f5f9;border:none;padding:6px 10px;border-radius:16px;font-weight:700;font-size:0.75rem;">@${currentUser.username}</button>
      <button onclick="handleLogout()" style="background:#ef4444;color:#fff;border:none;padding:6px 10px;border-radius:16px;font-weight:700;font-size:0.75rem;cursor:pointer;">Logout</button>
    `;
  } else {
    box.innerHTML = `<a href="/auth" style="background:var(--navy-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
  }
}

function renderComposerUserBar() {
  const bar = document.getElementById('composer-user-bar');
  if(!bar) return;
  if(currentUser) {
    bar.innerHTML = `
      <div class="avatar" style="width:34px;height:34px;background:var(--navy-blue);cursor:pointer;" onclick="openProfile('${currentUser.username}')">
        ${currentUser.avatar_url ? `<img src="${currentUser.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : currentUser.full_name.charAt(0)}
      </div>
      <div style="font-size:0.82rem;font-weight:800;color:var(--navy-blue);cursor:pointer;" onclick="openProfile('${currentUser.username}')">${currentUser.full_name}</div>
    `;
  } else {
    bar.innerHTML = '';
  }
}

async function handleLogout() {
  await fetch('/api/auth/logout', {method: 'POST'});
  currentUser = null;
  renderHeaderAuth();
  showToast('Logged out successfully.');
  window.location.href = '/auth';
}

function sharePost(postId) {
  const shareUrl = `${window.location.origin}/?post=${postId}`;
  if (navigator.share) {
    navigator.share({ title: 'Ijebu Connect', text: 'Check out this post on Ijebu Connect!', url: shareUrl }).catch(() => {});
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(shareUrl).then(() => showToast('Post link copied to clipboard!'));
  } else {
    prompt('Copy this post link:', shareUrl);
  }
}

function shareProfileLink(username, refCode) {
  const inviteUrl = `${window.location.origin}/auth?ref=${refCode}`;
  if (navigator.share) {
    navigator.share({
      title: 'Join Ijebu Connect',
      text: `Connect, trade, and network with me on Ijebu Connect! Register here: ${inviteUrl}`,
      url: inviteUrl
    }).catch(() => {});
  } else {
    navigator.clipboard.writeText(inviteUrl).then(() => showToast('Unique referral invitation link copied!'));
  }
}

function shareToWhatsApp(refCode) {
  const inviteUrl = `${window.location.origin}/auth?ref=${refCode}`;
  const text = encodeURIComponent(`Connect, trade, and network with me on Ijebu Connect! Register using my link: ${inviteUrl}`);
  window.open(`https://wa.me/?text=${text}`, '_blank');
}

function openEditProfileModal() {
  if (!currentUser) return window.location.href = '/auth';
  document.getElementById('edit-fullname').value = currentUser.full_name || '';
  document.getElementById('edit-phone').value = currentUser.phone || '';
  document.getElementById('edit-occupation').value = currentUser.occupation || '';
  document.getElementById('edit-age').value = currentUser.age || 18;
  document.getElementById('edit-gender').value = currentUser.gender || 'Unspecified';
  document.getElementById('edit-bio-text').value = currentUser.bio || '';
  document.getElementById('edit-profile-modal').style.display = 'flex';
}

function closeEditProfileModal() {
  document.getElementById('edit-profile-modal').style.display = 'none';
}

async function handleProfileUpdateSubmit(e) {
  e.preventDefault();
  const avatarInput = document.getElementById('edit-avatar-file');
  const coverInput = document.getElementById('edit-cover-file');
  let avatarUrl = '', coverUrl = '';

  if (avatarInput && avatarInput.files[0]) avatarUrl = (await uploadSelectedFile(avatarInput)).url;
  if (coverInput && coverInput.files[0]) coverUrl = (await uploadSelectedFile(coverInput)).url;

  const res = await fetch('/api/users/profile/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
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
  showToast(data.message, data.success ? 'success' : 'error');
  if (data.success) {
    closeEditProfileModal();
    checkSession();
    openProfile(currentUser.username);
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

async function handlePostSubmit(e, postType, groupId = 0) {
  if(e) e.preventDefault();
  if(!currentUser) return window.location.href = '/auth';

  const contentEl = postType === 'Event' ? document.getElementById('event-content') : document.getElementById('post-content');
  let content = contentEl.value.trim();

  const attachCheck = document.getElementById('attach-product-check');
  if (attachCheck && attachCheck.checked) {
    const title = document.getElementById('post-prod-title').value.trim();
    const price = document.getElementById('post-prod-price').value.trim();
    const whatsapp = document.getElementById('post-prod-whatsapp').value.trim();

    if (title && whatsapp) {
      const advertData = JSON.stringify({ title, price: price || 0, whatsapp });
      content += ` [PRODUCT_ADVERT]${advertData}`;
    }
  }

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
    contentEl.value = '';
    if(fileInput) fileInput.value = '';
    if(attachCheck) {
      attachCheck.checked = false;
      toggleProductAttach();
    }
    loadPosts(postType, postType === 'Event' ? 'events-container' : 'feed-posts-container');
  } else {
    showToast(data.message, 'error');
    if (data.requires_upgrade) {
      document.getElementById('cpn-upgrade-modal').style.display = 'flex';
    }
  }
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
  if(p.video_url) mediaHtml = `<video src="${p.video_url}" controls loading="lazy" style="width:100%;border-radius:10px;margin-top:6px;"></video>`;
  else if(p.image_url) mediaHtml = `<img src="${p.image_url}" loading="lazy" style="width:100%;border-radius:10px;margin-top:6px;">`;

  let productAdvertHtml = '';
  if (p.content.includes('[PRODUCT_ADVERT]')) {
    try {
      const parts = p.content.split('[PRODUCT_ADVERT]');
      p.content = parts[0];
      const prodInfo = JSON.parse(parts[1]);
      productAdvertHtml = `
        <div class="post-product-badge">
          <div>
            <div style="font-weight:800; color:var(--navy-blue); font-size:0.88rem;">🏷️ ${prodInfo.title}</div>
            <div style="font-weight:800; color:var(--emerald-green); font-size:0.82rem;">₦${parseFloat(prodInfo.price).toLocaleString()}</div>
          </div>
          <a href="https://wa.me/234${prodInfo.whatsapp.replace(/^0/,'')}" target="_blank" class="btn-whatsapp">
            <i class="fa-brands fa-whatsapp"></i> Chat Seller
          </a>
        </div>`;
    } catch(e){}
  }

  return `
  <div class="feed-post">
    <div class="post-header">
      <div class="avatar" style="background:var(--navy-blue);" onclick="openProfile('${p.username}')">
        ${p.avatar_url ? `<img src="${p.avatar_url}" loading="lazy" style="width:100%;height:100%;border-radius:50%;">` : p.full_name.charAt(0)}
      </div>
      <div>
        <div style="font-size:0.85rem;font-weight:800;cursor:pointer;" onclick="openProfile('${p.username}')">${p.full_name}</div>
        <div style="font-size:0.7rem;color:var(--text-muted);">@${p.username}</div>
      </div>
    </div>
    <div style="font-size:0.88rem;line-height:1.4;">${p.content}</div>
    ${productAdvertHtml}
    ${mediaHtml}
    <div class="post-actions">
      <button class="post-action" onclick="toggleLike(${p.id})">❤️ ${p.likes_count}</button>
      <button class="post-action" onclick="toggleComments(${p.id})">💬 ${p.comments_count || 0} Comments</button>
      <button class="post-action" onclick="sharePost(${p.id})">↪️ Share</button>
    </div>
    <div id="comments-box-${p.id}" class="comments-box" style="display:none;"></div>
  </div>`;
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
          <span class="comment-user" onclick="openProfile('${c.username}')" style="cursor:pointer;">@${c.username}:</span> ${c.content}
          <div style="display:flex;gap:10px;margin-top:2px;font-size:0.72rem;color:var(--text-muted);">
            <span onclick="toggleCommentLike(${c.id}, ${pid})" style="cursor:pointer;font-weight:700;">❤️ ${c.likes_count || 0}</span>
          </div>
        </div>
      `).join('') || '<small>No comments yet.</small>'}
    </div>
    <div style="display:flex;gap:4px;margin-top:6px;">
      <input type="text" id="comment-input-${pid}" class="form-control" placeholder="Write a comment..." style="padding:6px;font-size:0.78rem;">
      <button onclick="submitComment(${pid})" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;">Post</button>
    </div>
  `;
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
    body: JSON.stringify({content})
  });
  input.value = '';
  toggleComments(pid);
}

async function loadCategoryListings(listingType, containerId) {
  const res = await fetch(`/api/products?type=${listingType}`);
  const items = await res.json();
  const container = document.getElementById(containerId);
  if(!items.length) {
    container.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:1rem;">No listings found.</div>`;
    return;
  }
  container.innerHTML = items.map(p => {
    let mediaBox = '<i class="fa-solid fa-store"></i>';
    if(p.video_url) mediaBox = `<video src="${p.video_url}" controls></video>`;
    else if(p.image_url) mediaBox = `<img src="${p.image_url}" loading="lazy">`;
    return `
    <div class="product-card">
      <div class="product-img-box">${mediaBox}</div>
      <div style="flex:1;">
        <h4 style="font-weight:800;color:var(--navy-blue);font-size:0.9rem;">${p.title}</h4>
        <div style="font-weight:800;color:var(--emerald-green);font-size:0.85rem;">${p.price > 0 ? formatNaira(p.price) : 'Negotiable'}</div>
        <div style="font-size:0.72rem;color:var(--text-muted);">${p.category} • By <span onclick="openProfile('${p.seller_username}')" style="cursor:pointer;font-weight:800;">@${p.seller_username}</span></div>
      </div>
      <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" class="btn-whatsapp"><i class="fa-brands fa-whatsapp"></i> Chat</a>
    </div>`;
  }).join('');
}

function startSellItem(type = 'Market') {
  if(!currentUser) return window.location.href = '/auth';
  if(currentUser.user_type === 'Resident' && (currentUser.listings_count || 0) >= 2) {
    document.getElementById('cpn-upgrade-modal').style.display = 'flex';
  } else {
    document.getElementById('prod-type').value = type;
    document.getElementById('modal-sell-title').innerText = `List on Ijebu ${type}`;
    document.getElementById('sell-modal').style.display = 'flex';
  }
}

function closeCPNModal() { document.getElementById('cpn-upgrade-modal').style.display = 'none'; }
function closeSellModal() { document.getElementById('sell-modal').style.display = 'none'; }
function openDatingSettingsModal() { if(!currentUser) return window.location.href = '/auth'; document.getElementById('dating-modal').style.display = 'flex'; }
function closeDatingModal() { document.getElementById('dating-modal').style.display = 'none'; }
function openCashoutModal() { document.getElementById('cashout-modal').style.display = 'flex'; }
function closeCashoutModal() { document.getElementById('cashout-modal').style.display = 'none'; }
function openGroupCreateModal() { if(!currentUser) return window.location.href = '/auth'; document.getElementById('group-create-modal').style.display = 'flex'; }
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

  const res = await fetch('/api/groups', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, description: desc, avatar_url: avatarUrl, cover_url: coverUrl})
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) { closeGroupModal(); loadGroups(); }
}

async function openGroupDetail(groupId) {
  switchNav('group-detail');
  const res = await fetch(`/api/groups/${groupId}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');
  const g = data.group;
  document.getElementById('active-group-id').value = g.id;

  const groupCoverBg = g.cover_url ? `style="background-image:url('${g.cover_url}');background-size:cover;"` : '';
  const groupAvatarHtml = g.avatar_url ? `<img src="${g.avatar_url}" style="width:100%;height:100%;object-fit:cover;">` : `<i class="fa-solid fa-users"></i>`;

  document.getElementById('group-detail-header').innerHTML = `
    <div class="fb-cover-banner" ${groupCoverBg}>
      <div class="fb-avatar-wrap">${groupAvatarHtml}</div>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:flex-end;">
      <div>
        <h3 style="font-size:1.1rem; font-weight:800; color:var(--navy-blue);">${g.name}</h3>
        <p style="font-size:0.75rem; color:var(--text-muted);">${g.member_count} Members • Created by @${g.creator_username}</p>
      </div>
      <button onclick="joinGroup(${g.id})" style="background:${g.is_member ? '#ef4444' : 'var(--emerald-green)'};color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;cursor:pointer;">
        ${g.is_member ? 'Leave Group' : 'Join Group'}
      </button>
    </div>
    <p style="font-size:0.82rem; margin-top:8px; color:var(--text-dark);">${g.description || 'No description provided.'}</p>
  `;

  if(g.is_member) {
    document.getElementById('group-post-composer').style.display = 'block';
  } else {
    document.getElementById('group-post-composer').style.display = 'none';
  }
  loadPosts('Social', 'group-posts-container', g.id);
}

async function handleGroupPostSubmit(e) {
  e.preventDefault();
  if(!currentUser) return window.location.href = '/auth';
  const groupId = parseInt(document.getElementById('active-group-id').value);
  const contentEl = document.getElementById('group-post-content');
  const content = contentEl.value.trim();
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
    contentEl.value = '';
    if(fileInput) fileInput.value = '';
    loadPosts('Social', 'group-posts-container', groupId);
  }
}

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
    checkSession();
    if(type === 'Market') loadCategoryListings('Market', 'products-container');
    if(type === 'Beauty') loadCategoryListings('Beauty', 'beauty-container');
    if(type === 'Jobs') loadCategoryListings('Jobs', 'jobs-container');
  } else {
    if(data.requires_upgrade) closeSellModal(), closeCPNModal(), document.getElementById('cpn-upgrade-modal').style.display = 'flex';
  }
}

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
  if(!matches.length) {
    container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No active singles on match feed yet.</div>`;
    return;
  }
  container.innerHTML = matches.map(m => `
    <div class="card" style="display:flex;gap:10px;align-items:center;">
      <div class="avatar" style="background:#7c3aed;width:48px;height:48px;">${m.avatar_url ? `<img src="${m.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : m.full_name.charAt(0)}</div>
      <div style="flex:1;">
        <h4 style="font-weight:800;color:var(--navy-blue);font-size:0.88rem;">${m.full_name}, ${m.age}</h4>
        <div style="font-size:0.72rem;color:var(--emerald-green);font-weight:700;">${m.relationship_intent} • ${m.gender}</div>
        <div style="font-size:0.78rem;color:var(--text-muted);">"${m.bio || 'Living in Ijebu'}"</div>
      </div>
      <button onclick="sendWink(${m.id})" style="background:#4f46e5;color:#fff;border:none;padding:6px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;cursor:pointer;">Wink 👋</button>
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
  const isPartner = u.user_type === 'CPN Partner' || u.user_type === 'Admin';

  const shareInviteBlock = `
    <div class="card" style="background:#f8fafc; border:1.5px dashed var(--navy-blue); margin-top:10px;">
      <div style="font-weight:800; font-size:0.82rem; color:var(--navy-blue); margin-bottom:4px;">
        🔗 ${isSelf ? 'Your Unique Invitation & Share Link' : `${u.full_name}'s Profile Link`}
      </div>
      <p style="font-size:0.75rem; color:var(--text-muted); margin-bottom:8px;">
        Share this profile to social media or invite friends to join and follow.
      </p>
      <div style="display:flex; gap:6px;">
        <button onclick="shareProfileLink('${u.username}', '${u.referral_code}')" style="background:var(--navy-blue); color:#fff; border:none; padding:6px 12px; border-radius:8px; font-weight:700; font-size:0.75rem; flex:1; cursor:pointer;">
          📋 Copy Link
        </button>
        <button onclick="shareToWhatsApp('${u.referral_code}')" style="background:#25d366; color:#fff; border:none; padding:6px 12px; border-radius:8px; font-weight:700; font-size:0.75rem; flex:1; cursor:pointer;">
          <i class="fa-brands fa-whatsapp"></i> WhatsApp
        </button>
      </div>
    </div>`;

  let cpnWalletBlock = '';
  if(isSelf && isPartner) {
    cpnWalletBlock = `
      <div class="card" style="background:linear-gradient(135deg, #0b1e36, #1e3a8a);color:#fff;">
        <div>Wallet Balance: <strong style="color:#f59e0b;font-size:1.1rem;">${formatNaira(u.wallet_balance)}</strong></div>
        <div style="font-size:0.78rem;margin:4px 0;">Referral Code: <b>${u.referral_code}</b> | Recruits: <b>${u.recruits_count}</b></div>
        <button onclick="openCashoutModal()" style="background:#059669;color:#fff;border:none;padding:8px;border-radius:8px;width:100%;font-weight:800;font-size:0.8rem;margin-top:6px;cursor:pointer;">Request Bank Cashout</button>
      </div>`;
  }

  const postsHtml = u.posts.length
    ? u.posts.map(p => renderPostCard(p)).join('')
    : '<div class="card" style="text-align:center;color:var(--text-muted);">No posts published yet on wall.</div>';

  const coverBg = u.cover_url ? `style="background-image:url('${u.cover_url}');background-size:cover;"` : '';
  const c = document.getElementById('profile-wall-container');

  c.innerHTML = `
    <div class="card">
      <div class="fb-cover-banner" ${coverBg}>
        <div class="fb-avatar-wrap">${u.avatar_url ? `<img src="${u.avatar_url}" style="width:100%;height:100%;object-fit:cover;">` : u.full_name.charAt(0)}</div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:flex-end;">
        <div>
          <h3 style="font-size:1.05rem;font-weight:800;">${u.full_name} <span style="font-size:0.65rem;background:#fef3c7;color:#92400e;padding:2px 6px;border-radius:6px;">${u.user_type}</span></h3>
          <p style="font-size:0.75rem;color:var(--text-muted);">@${u.username} • <b>${u.followers_count}</b> Followers | <b>${u.following_count}</b> Following</p>
        </div>
      </div>
      <p style="font-size:0.82rem;margin:8px 0;font-weight:600;">💼 ${u.occupation || 'Member'} | 📱 ${u.phone || ''}</p>
      <p style="font-size:0.82rem;margin-bottom:8px;">${u.bio || 'Resident of Ijebu'}</p>
      <div style="display:flex;gap:6px;margin-top:8px;">
        ${isSelf ? `<button onclick="openEditProfileModal()" style="background:var(--navy-blue);color:#fff;border:none;padding:8px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;flex:1;cursor:pointer;">✏️ Edit Profile Details</button>` : `
        <button onclick="toggleFollow('${u.username}')" style="background:var(--navy-blue);color:#fff;border:none;padding:8px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;flex:1;cursor:pointer;">${u.is_following ? 'Unfollow' : 'Follow'}</button>
        <button onclick="startChatFromProfile('${u.username}')" style="background:var(--emerald-green);color:#fff;border:none;padding:8px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;flex:1;cursor:pointer;">💬 Message</button>
        `}
      </div>
      ${shareInviteBlock}
    </div>
    ${cpnWalletBlock}
    <h4 style="font-size:0.9rem;margin:12px 0 6px;">Profile Wall Updates</h4>
    ${postsHtml}
  `;
  switchNav('profile');
}

function startChatFromProfile(username) {
  switchNav('chat');
  openChatThread(username);
}

async function toggleFollow(username) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch(`/api/users/${encodeURIComponent(username)}/follow`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  openProfile(username);
}

async function handlePayoutRequest(e) {
  e.preventDefault();
  const res = await fetch('/api/cpn/withdraw', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      amount: document.getElementById('payout-amount').value,
      bank_name: document.getElementById('payout-bank').value,
      account_number: document.getElementById('payout-acc-num').value,
      account_name: document.getElementById('payout-acc-name').value
    })
  });
  const data = await res.json();
  showToast(data.message);
  if(data.success) { closeCashoutModal(); checkSession(); }
}

async function loadGroups() {
  const res = await fetch('/api/groups');
  const groups = await res.json();
  const c = document.getElementById('groups-container');
  if(!groups.length) { c.innerHTML = '<div class="card">No groups created yet. Click "+ Create Group" above to start one!</div>'; return; }
  c.innerHTML = groups.map(g => {
    const grpAvatar = g.avatar_url ? `<img src="${g.avatar_url}" style="width:42px;height:42px;border-radius:50%;object-fit:cover;flex-shrink:0;">` : `<div class="avatar" style="background:var(--navy-blue);width:42px;height:42px;flex-shrink:0;"><i class="fa-solid fa-users"></i></div>`;
    return `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
      <div style="display:flex;align-items:center;gap:10px;cursor:pointer;" onclick="openGroupDetail(${g.id})">
        ${grpAvatar}
        <div>
          <h4 style="font-weight:800;font-size:0.9rem;color:var(--navy-blue);">${g.name}</h4>
          <p style="font-size:0.75rem;color:var(--text-muted);">${g.member_count} Members</p>
        </div>
      </div>
      <button onclick="openGroupDetail(${g.id})" style="background:var(--navy-blue);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.75rem;cursor:pointer;flex-shrink:0;">Enter Group</button>
    </div>`;
  }).join('');
}

async function joinGroup(groupId) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch(`/api/groups/${groupId}/join`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  openGroupDetail(groupId);
}

async function refreshUnread() {
  if(!currentUser) return;
  try {
    const res = await fetch('/api/chat/unread');
    const data = await res.json();
    const chatBadge = document.getElementById('chat-tab-badge');
    if(chatBadge) {
      if(data.count > 0) {
        chatBadge.innerText = data.count;
        chatBadge.style.display = 'block';
      } else {
        chatBadge.style.display = 'none';
      }
    }
  } catch(e){}
}

async function loadChatPartners() {
  const res = await fetch('/api/chat/partners');
  const data = await res.json();
  const c = document.getElementById('chat-partners-container');
  const friendsBox = document.getElementById('chat-friends-container');
  const friendsWrapper = document.getElementById('chat-friends-wrapper');

  if (data.friends && data.friends.length > 0) {
    friendsWrapper.style.display = 'block';
    friendsBox.innerHTML = data.friends.map(f => `
      <div class="suggestion-card" onclick="startChatFromProfile('${f.username}')" style="cursor:pointer;">
        <div class="avatar" style="background:var(--navy-blue);">
          ${f.avatar_url ? `<img src="${f.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : f.full_name.charAt(0)}
        </div>
        <h5>${f.full_name}</h5>
        <p>@${f.username}</p>
        <button style="background:var(--emerald-green);">Message</button>
      </div>
    `).join('');
  } else {
    friendsWrapper.style.display = 'none';
  }

  if(!data.success || !data.partners.length) {
    c.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No message history yet.<br><small>Pick a connection above or visit any wall to start chatting!</small></div>`;
    return;
  }
  c.innerHTML = data.partners.map(p => `
    <div onclick="openChatThread('${p.user.username}')" class="card" style="display:flex;gap:10px;align-items:center;cursor:pointer;">
      <div class="avatar" style="background:var(--navy-blue);">${p.user.avatar_url ? `<img src="${p.user.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.user.full_name.charAt(0)}</div>
      <div style="flex:1;">
        <div style="font-weight:800;font-size:0.88rem;color:var(--navy-blue);">${p.user.full_name}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);">${p.last_message}</div>
      </div>
      ${p.unread ? `<span style="background:#ef4444;color:#fff;font-size:0.65rem;font-weight:800;padding:2px 6px;border-radius:10px;">${p.unread}</span>` : ''}
    </div>
  `).join('');
}

async function openChatThread(username) {
  if(!currentUser) return window.location.href = '/auth';
  currentChatUser = username;
  document.getElementById('chat-list-wrap').style.display = 'none';
  document.getElementById('chat-thread-wrap').style.display = 'block';
  const res = await fetch(`/api/chat/${encodeURIComponent(username)}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');

  document.getElementById('chat-thread-header').innerHTML = `
    <div style="display:flex; align-items:center; gap:8px; cursor:pointer;" onclick="openProfile('${data.other.username}')">
      <div class="avatar" style="width:32px;height:32px;background:var(--navy-blue);font-size:0.8rem;">
        ${data.other.avatar_url ? `<img src="${data.other.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : data.other.full_name.charAt(0)}
      </div>
      <div>
        <b>${data.other.full_name}</b> <small style="color:var(--text-muted);">(@${data.other.username})</small>
      </div>
    </div>`;

  const m = document.getElementById('chat-messages');
  if (!data.messages || data.messages.length === 0) {
    m.innerHTML = `<div style="text-align:center; color:var(--text-muted); font-size:0.8rem; margin-top:20px;">No message history yet. Send a message to start chatting!</div>`;
  } else {
    m.innerHTML = data.messages.map(msg => `
      <div class="chat-bubble ${msg.sender_id === data.me_id ? 'me' : 'them'}">
        ${msg.content}
      </div>
    `).join('');
  }
  m.scrollTop = m.scrollHeight;
  setTimeout(() => document.getElementById('chat-input').focus(), 100);
  refreshUnread();
}

function closeChatThread() {
  currentChatUser = null;
  document.getElementById('chat-list-wrap').style.display = 'block';
  document.getElementById('chat-thread-wrap').style.display = 'none';
  loadChatPartners();
}

async function sendChatMessage(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const content = input.value.trim();
  if(!content || !currentChatUser) return;
  input.value = '';
  await fetch(`/api/chat/${encodeURIComponent(currentChatUser)}`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content})
  });
  openChatThread(currentChatUser);
}

async function loadFriendSuggestions() {
  if(!currentUser) {
    document.getElementById('friend-suggestions-wrapper').style.display = 'none';
    return;
  }
  try {
    const res = await fetch('/api/users/suggestions');
    const users = await res.json();
    const container = document.getElementById('friend-suggestions-container');
    if(!users.length) {
      document.getElementById('friend-suggestions-wrapper').style.display = 'none';
      return;
    }
    document.getElementById('friend-suggestions-wrapper').style.display = 'block';
    container.innerHTML = users.map(u => `
      <div class="suggestion-card">
        <div class="avatar" style="background:var(--navy-blue);cursor:pointer;" onclick="openProfile('${u.username}')">
          ${u.avatar_url ? `<img src="${u.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : u.full_name.charAt(0)}
        </div>
        <h5 onclick="openProfile('${u.username}')" style="cursor:pointer;">${u.full_name}</h5>
        <p>@${u.username}</p>
        <button onclick="quickFollow('${u.username}', this)">Follow</button>
      </div>
    `).join('');
  } catch(e){}
}

async function quickFollow(username, btn) {
  if(!currentUser) return window.location.href = '/auth';
  btn.disabled = true;
  btn.innerText = '...';
  const res = await fetch(`/api/users/${encodeURIComponent(username)}/follow`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  if(data.success) {
    btn.innerText = 'Following';
    btn.style.background = 'var(--emerald-green)';
  } else {
    btn.disabled = false;
    btn.innerText = 'Follow';
  }
}

async function handleSearch() {
  const q = document.getElementById('global-search-input').value.trim();
  if(q.length < 2) return;
  switchNav('search');
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  const c = document.getElementById('search-results-container');
  c.innerHTML = `
    <div class="card">
      <h4>Members (${data.users.length})</h4>
      ${data.users.map(u => `<div onclick="openProfile('${u.username}')" style="cursor:pointer;padding:4px 0;"><b>${u.full_name}</b> (@${u.username})</div>`).join('') || 'None'}
    </div>
    <div class="card">
      <h4>Listings (${data.products.length})</h4>
      ${data.products.map(p => `<div><b>${p.title}</b> - ${formatNaira(p.price)}</div>`).join('') || 'None'}
    </div>
  `;
}

async function openNotifs() {
  switchNav('notifs');
  const res = await fetch('/api/notifications');
  const notifs = await res.json();
  const c = document.getElementById('notifs-container');
  if(!notifs.length) { c.innerHTML = '<div class="card">No notifications yet.</div>'; return; }
  c.innerHTML = notifs.map(n => `<div class="card" style="font-size:0.82rem;cursor:pointer;" onclick="handleNotifClick('${n.type}', ${n.target_id}, '${n.sender_username || ''}')">${n.message}</div>`).join('');
  fetch('/api/notifications', {method:'POST'});
  document.getElementById('notif-badge').style.display = 'none';
}

function handleNotifClick(type, targetId, senderUsername) {
  if (type === 'message' || type === 'wink') {
    if (senderUsername) startChatFromProfile(senderUsername);
    else switchNav('chat');
  } else if (type === 'follow') {
    if (senderUsername) openProfile(senderUsername);
  } else {
    switchNav('feed');
  }
}

// INSTANT DEEP LINK RENDERER (0ms WAIT TIME ON FACEBOOK/WHATSAPP CLICK)
function checkDeepLinkTarget() {
  if (window.INITIAL_DEEP_LINK_DATA) {
    const data = window.INITIAL_DEEP_LINK_DATA;
    const targetBox = document.getElementById('deep-link-target-container');
    if (!targetBox) return;

    if (data.type === 'post') {
      const p = data.item;
      targetBox.innerHTML = `
        <div class="card shared-target-card" style="margin-bottom:1rem;">
          <div style="font-size:0.75rem; font-weight:800; color:var(--emerald-green); margin-bottom:6px;">📌 Shared Content Preview</div>
          ${renderPostCard(p)}
        </div>`;
    } else if (data.type === 'product') {
      const p = data.item;
      let mediaBox = '<i class="fa-solid fa-store"></i>';
      if (p.image_url) mediaBox = `<img src="${p.image_url}" style="width:100%;height:100%;object-fit:cover;">`;
      
      targetBox.innerHTML = `
        <div class="card shared-target-card" style="margin-bottom:1rem;">
          <div style="font-size:0.75rem; font-weight:800; color:var(--emerald-green); margin-bottom:6px;">📌 Shared Product / Listing</div>
          <div class="product-card" style="border-bottom:none; margin-bottom:0; padding-bottom:0;">
            <div class="product-img-box">${mediaBox}</div>
            <div style="flex:1;">
              <h4 style="font-weight:800; color:var(--navy-blue); font-size:0.95rem;">${p.title}</h4>
              <div style="font-weight:800; color:var(--emerald-green); font-size:0.9rem;">₦${parseFloat(p.price).toLocaleString()}</div>
              <div style="font-size:0.75rem; color:var(--text-muted);">${p.category} • By @${p.seller_username}</div>
            </div>
            <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" class="btn-whatsapp"><i class="fa-brands fa-whatsapp"></i> Chat</a>
          </div>
        </div>`;
    }
  }
}

window.onload = function() {
  checkDeepLinkTarget();
  checkSession();
  loadPosts('Social', 'feed-posts-container');
  setInterval(refreshUnread, 10000);
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
:root { --navy-blue: #0b1e36; --emerald-green: #059669; --border-light: #cbd5e1; }
* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; }
body { background: #f8fafc; color: #0f172a; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
.auth-card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 18px; padding: 1.5rem; max-width: 440px; width: 100%; text-align: center; box-shadow:0 8px 24px rgba(0,0,0,0.05); }
.brand { font-size: 1.3rem; font-weight: 800; color: var(--navy-blue); margin-bottom: 0.2rem; }
.brand span { color: var(--emerald-green); }
.auth-tabs { display: flex; gap: 4px; margin: 1rem 0; background: #f1f5f9; padding: 4px; border-radius: 10px; }
.auth-tab { flex: 1; padding: 8px; border-radius: 6px; border: none; font-weight: 700; font-size: 0.8rem; cursor: pointer; color: #64748b; background: transparent; }
.auth-tab.active { background: #fff; color: var(--navy-blue); }
.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.85rem; text-align: left; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }
.btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; margin-top: 4px; }
</style>
</head>
<body>
<div class="auth-card">
  <div class="brand">IJEBU <span>CONNECT</span></div>
  <p style="font-size:0.78rem;color:#64748b;font-style:italic;">Connect. Discover. Trade. Belong.</p>
  <div class="auth-tabs">
    <button class="auth-tab active" id="tab-login" onclick="toggleAuth('login')">Sign In</button>
    <button class="auth-tab" id="tab-register" onclick="toggleAuth('register')">Register Free</button>
  </div>
  <form id="form-login" onsubmit="handleLogin(event)">
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Password</label><input type="password" id="login-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit" style="background:var(--navy-blue);">Sign In</button>
  </form>
  <form id="form-register" style="display:none;" onsubmit="handleRegister(event)">
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Full Name</label><input type="text" id="reg-name" class="form-control" placeholder="Afeez Adebayo" required></div>
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Phone Number</label><input type="tel" id="reg-phone" class="form-control" placeholder="08012345678" required></div>
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Username</label><input type="text" id="reg-uname" class="form-control" placeholder="afeez123" required></div>
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Password</label><input type="password" id="reg-pword" class="form-control" required></div>
    <div class="form-group"><label style="font-size:0.8rem;font-weight:700;">Referral Code (Optional)</label><input type="text" id="reg-ref" class="form-control" placeholder="CPN00001"></div>
    <button type="submit" class="btn-submit">Create Free Account</button>
  </form>
</div>
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
      password: document.getElementById('reg-pword').value,
      referred_by: document.getElementById('reg-ref').value
    })
  });
  const data = await res.json();
  if(data.success) { alert(data.message); toggleAuth('login'); }
  else alert(data.message);
}

window.onload = function() {
  const params = new URLSearchParams(window.location.search);
  const ref = params.get('ref');
  if(ref) {
    document.getElementById('reg-ref').value = ref.toUpperCase();
    toggleAuth('register');
  }
};
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
body { font-family:'Plus Jakarta Sans', sans-serif; background:#f8fafc; color:#0f172a; padding:1rem; max-width:900px; margin:0 auto; }
.admin-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:0.75rem; margin-bottom:1rem; }
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
</style>
</head>
<body>
<div class="admin-header">
  <h2>⚙️ Admin Control Panel</h2>
  <a href="/" style="color:#0b1e36;font-weight:700;text-decoration:none;font-size:0.85rem;">← Back to App</a>
</div>
<div class="grid">
  <div class="card"><div class="val" id="st-users">0</div><div class="lbl">Total Members</div></div>
  <div class="card"><div class="val" id="st-partners">0</div><div class="lbl">CPN Partners</div></div>
  <div class="card"><div class="val" id="st-pending">0</div><div class="lbl">Pending Upgrades</div></div>
  <div class="card"><div class="val" id="st-wallets">₦0.00</div><div class="lbl">Member Balances</div></div>
</div>

<div class="admin-tabs">
  <button class="admin-tab active" onclick="switchAdminTab('partners')">CPN Payment Claims</button>
  <button class="admin-tab" onclick="switchAdminTab('members')">Manage Members</button>
  <button class="admin-tab" onclick="switchAdminTab('posts')">Manage Posts</button>
  <button class="admin-tab" onclick="switchAdminTab('payouts')">Bank Cashouts</button>
</div>

<div id="adm-partners" class="tab-sec active">
  <h3>Pending CPN Partner Upgrades (₦2,000)</h3>
  <table>
    <thead><tr><th>Member</th><th>Amount</th><th>Reference Note</th><th>Action</th></tr></thead>
    <tbody id="partner-reqs-body"></tbody>
  </table>
</div>

<div id="adm-members" class="tab-sec">
  <h3>All Registered Members</h3>
  <table>
    <thead><tr><th>Full Name</th><th>Username</th><th>Phone</th><th>User Type</th><th>Action</th></tr></thead>
    <tbody id="members-body"></tbody>
  </table>
</div>

<div id="adm-posts" class="tab-sec">
  <h3>All Platform Posts & Content</h3>
  <table>
    <thead><tr><th>Author</th><th>Post Content</th><th>Type</th><th>Action</th></tr></thead>
    <tbody id="posts-body"></tbody>
  </table>
</div>

<div id="adm-payouts" class="tab-sec">
  <h3>Member Cashout Requests</h3>
  <table>
    <thead><tr><th>User</th><th>Amount</th><th>Bank Details</th><th>Action</th></tr></thead>
    <tbody id="payouts-body"></tbody>
  </table>
</div>

<script>
function switchAdminTab(t) {
  document.querySelectorAll('.admin-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-sec').forEach(s => s.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById(`adm-${t}`).classList.add('active');
}

async function loadAdmin() {
  const res = await fetch('/api/admin/overview');
  const data = await res.json();
  if(!data.success) { alert('Admin access denied.'); window.location.href='/'; return; }
  document.getElementById('st-users').innerText = data.total_users;
  document.getElementById('st-partners').innerText = data.total_partners;
  document.getElementById('st-pending').innerText = data.pending_partners;
  document.getElementById('st-wallets').innerText = '₦' + data.total_partner_wallets.toLocaleString();
  loadPartnerRequests();
  loadMembers();
  loadAdminPosts();
  loadPayouts();
}

async function loadPartnerRequests() {
  const res = await fetch('/api/admin/partner-requests');
  const reqs = await res.json();
  const body = document.getElementById('partner-reqs-body');
  if(!reqs.length) { body.innerHTML = '<tr><td colspan="4">No pending partner claims.</td></tr>'; return; }
  body.innerHTML = reqs.map(r => `
    <tr>
      <td><b>${r.full_name}</b><br><small>@${r.username} (${r.phone})</small></td>
      <td><b>₦${r.amount.toLocaleString()}</b></td>
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
  const res = await fetch('/api/admin/partner-requests', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({request_id: id, action: action})
  });
  const data = await res.json();
  alert(data.message);
  loadAdmin();
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
      <td>${u.user_type !== 'Admin' ? `<button class="btn-act btn-del" onclick="deleteMember(${u.id})">Delete Member</button>` : 'System Admin'}</td>
    </tr>
  `).join('');
}

async function deleteMember(uid) {
  if(!confirm('Remove this member and all associated posts, messages, and comments?')) return;
  const res = await fetch(`/api/admin/users?user_id=${uid}`, {method:'DELETE'});
  const data = await res.json();
  alert(data.message);
  loadAdmin();
}

async function loadAdminPosts() {
  const res = await fetch('/api/admin/posts');
  const posts = await res.json();
  const body = document.getElementById('posts-body');
  if(!posts.length) { body.innerHTML = '<tr><td colspan="4">No posts published.</td></tr>'; return; }
  body.innerHTML = posts.map(p => `
    <tr>
      <td><b>${p.full_name}</b><br><small>@${p.username}</small></td>
      <td style="max-width:300px;">${p.content}</td>
      <td><b>${p.post_type}</b></td>
      <td><button class="btn-act btn-del" onclick="deleteAdminPost(${p.id})">Delete Post</button></td>
    </tr>
  `).join('');
}

async function deleteAdminPost(pid) {
  if(!confirm('Delete this post and its comments?')) return;
  const res = await fetch(`/api/admin/posts?post_id=${pid}`, {method:'DELETE'});
  const data = await res.json();
  alert(data.message);
  loadAdmin();
}

async function loadPayouts() {
  const res = await fetch('/api/admin/payouts');
  const payouts = await res.json();
  const body = document.getElementById('payouts-body');
  if(!payouts.length) { body.innerHTML = '<tr><td colspan="4">No cashout requests.</td></tr>'; return; }
  body.innerHTML = payouts.map(p => `
    <tr>
      <td><b>${p.full_name}</b><br><small>@${p.username}</small></td>
      <td><b>₦${p.amount.toLocaleString()}</b></td>
      <td>${p.bank_name}<br><small>${p.account_number} (${p.account_name})</small></td>
      <td>
        ${p.status === 'pending' ? `
          <button class="btn-act btn-app" onclick="updatePayout(${p.id}, 'approved')">Approve Cashout</button>
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

loadAdmin();
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
    p = query_param()

    # Guarantee HTTPS Host URL for Facebook & WhatsApp Crawlers
    host_url = request.host_url
    if not host_url.startswith('https://') and 'localhost' not in host_url and '127.0.0.1' not in host_url:
        host_url = host_url.replace('http://', 'https://')

    meta_title = "Ijebu Connect - Mobile Hub"
    meta_desc = "The unified digital hub connecting sons and daughters of Ijebu land. Connect, trade, and build community."
    meta_image = f"{host_url.rstrip('/')}/static/uploads/default_preview.jpg"
    meta_url = request.url
    deep_link_data = None

    # Dynamic WhatsApp/Facebook Preview for Shared Post Link (?post=ID)
    post_id = request.args.get('post')
    if post_id:
        try:
            cursor.execute(f'''
                SELECT p.id, p.content, p.image_url, p.video_url, p.created_at,
                       u.full_name, u.username, u.avatar_url
                FROM posts p JOIN users u ON p.user_id = u.id
                WHERE p.id = {p}
            ''', (post_id,))
            row = cursor.fetchone()
            if row:
                r = dict(row)
                meta_title = f"{r['full_name']} on Ijebu Connect"
                clean_txt = r['content'].split('[PRODUCT_ADVERT]')[0] if '[PRODUCT_ADVERT]' in r['content'] else r['content']
                meta_desc = clean_txt[:150] if clean_txt else "Check out this post on Ijebu Connect!"
                if r['image_url']:
                    meta_image = host_url.rstrip('/') + r['image_url']
                deep_link_data = {'type': 'post', 'item': r}
        except Exception:
            pass

    # Dynamic WhatsApp/Facebook Preview for Shared Product Link (?product=ID)
    product_id = request.args.get('product')
    if product_id:
        try:
            cursor.execute(f'''
                SELECT p.*, u.full_name AS seller_name, u.username AS seller_username
                FROM products p JOIN users u ON p.user_id = u.id
                WHERE p.id = {p}
            ''', (product_id,))
            row = cursor.fetchone()
            if row:
                r = dict(row)
                meta_title = f"₦{float(r['price']):,.2f} - {r['title']}"
                meta_desc = r['description'][:150] if r['description'] else "Available on Ijebu Connect Marketplace!"
                if r['image_url']:
                    meta_image = host_url.rstrip('/') + r['image_url']
                deep_link_data = {'type': 'product', 'item': r}
        except Exception:
            pass

    return render_template_string(
        INDEX_TEMPLATE,
        contact_email=CONTACT_EMAIL,
        meta_title=meta_title,
        meta_desc=meta_desc,
        meta_image=meta_image,
        meta_url=meta_url,
        deep_link_json=json.dumps(deep_link_data) if deep_link_data else 'null'
    )

@app.route('/auth')
def auth_page():
    return render_template_string(AUTH_TEMPLATE)

@app.route('/admin')
def admin_page():
    return render_template_string(ADMIN_TEMPLATE)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
    