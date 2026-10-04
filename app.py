import os
import sqlite3
import random
import string
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

# ======================================================================
# DATABASE ENGINE
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
    if user_id == sender_id:
        return
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(f'''
        INSERT INTO notifications (user_id, sender_id, type, target_id, message)
        VALUES ({p}, {p}, {p}, {p}, {p})
    ''', (user_id, sender_id, notif_type, target_id, message))
    db.commit()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        p = query_param()
        is_postgres = bool(DATABASE_URL)
        pk_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"

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
        safe_add_column(cursor, 'posts', 'group_id', 'INTEGER DEFAULT 0')

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
            category TEXT DEFAULT 'General',
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

        db.commit()

        # ADMIN SEEDING
        admin_username = os.environ.get('ADMIN_SEED_USERNAME', 'ijebuconnect').lower()
        admin_password = os.environ.get('ADMIN_SEED_PASSWORD', 'Rotimi1972connect')
        admin_phone = os.environ.get('ADMIN_SEED_PHONE', '09018363715')
        admin_name = os.environ.get('ADMIN_SEED_NAME', "Sir Ola'Rotimi")
        admin_ref = os.environ.get('ADMIN_SEED_REF', 'CPN00001')
        admin_pass_hash = generate_password_hash(admin_password)

        cursor.execute(
            f"SELECT id FROM users WHERE username = {p} OR phone = {p} OR referral_code = {p}",
            (admin_username, admin_phone, admin_ref)
        )
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
                cursor.execute(f'''
                UPDATE users SET password_hash = {p}, user_type = 'Admin' WHERE id = {p}
                ''', (admin_pass_hash, existing_admin['id']))
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
    content = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Sitemap: {}sitemap.xml
""".format(request.host_url)
    return Response(content, mimetype='text/plain')

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
# FILE UPLOADER
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
        cursor.execute(f'''INSERT INTO transactions (user_id, amount, tx_type, description)
        VALUES ({p}, {p}, 'Tier-1 CPN Commission', {p})''',
        (t1['id'], bonus1, f"10% CPN Reward from {buyer['full_name']}"))
        add_notification(t1['id'], user_id, 'commission', 0, f"You earned ₦{bonus1:.2f} Tier-1 CPN Reward from {buyer['full_name']}!")

        if t1['referred_by']:
            cursor.execute(f"SELECT id FROM users WHERE referral_code = {p}", (t1['referred_by'],))
            t2 = cursor.fetchone()
            if t2:
                bonus2 = upgrade_fee * 0.05
                cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}", (bonus2, t2['id']))
                cursor.execute(f'''INSERT INTO transactions (user_id, amount, tx_type, description)
                VALUES ({p}, {p}, 'Tier-2 CPN Commission', {p})''',
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
                'username': user['username'],
                'user_type': user['user_type'],
                'referral_code': user['referral_code'],
                'wallet_balance': float(user['wallet_balance'] or 0),
                'is_verified_merchant': user['is_verified_merchant'],
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
            f'''SELECT id, full_name, username, user_type, referral_code, wallet_balance, is_verified_merchant,
            age, gender, relationship_intent, bio, occupation, avatar_url, cover_url, is_dating_active
            FROM users WHERE id = {p}''',
            (session['user_id'],)
        )
        u = cursor.fetchone()
        if u:
            d = dict(u)
            d['wallet_balance'] = float(d.get('wallet_balance') or 0)
            cursor.execute(f"SELECT COUNT(*) FROM users WHERE referred_by = {p}", (d['referral_code'],))
            d['recruits_count'] = cursor.fetchone()[0]
            cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {p} AND is_read = 0", (d['id'],))
            d['unread_notifs'] = cursor.fetchone()[0]
            return jsonify({'logged_in': True, 'user': d})
    return jsonify({'logged_in': False})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out.'})

# ======================================================================
# GLOBAL SEARCH API
# ======================================================================
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

    cursor.execute(f"SELECT id, name, category, description, avatar_url FROM groups WHERE LOWER(name) LIKE {p} OR LOWER(category) LIKE {p} LIMIT 10", (term, term))
    groups = [dict(r) for r in cursor.fetchall()]

    return jsonify({'users': users, 'products': products, 'posts': posts, 'groups': groups})

# ======================================================================
# NOTIFICATIONS API
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

# ======================================================================
# FOLLOW / UNFOLLOW SYSTEM
# ======================================================================
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
# GROUPS (COMMUNITY HUBS) API
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
        cat = data.get('category', 'General').strip()
        avatar = data.get('avatar_url', '').strip()

        if not name:
            return jsonify({'success': False, 'message': 'Group name required.'}), 400

        cursor.execute(f'''INSERT INTO groups (user_id, name, description, category, avatar_url)
        VALUES ({p}, {p}, {p}, {p}, {p})''', (session['user_id'], name, desc, cat, avatar))
        group_id = cursor.lastrowid or 0
        
        # Auto join creator
        cursor.execute(f"INSERT INTO group_members (group_id, user_id) VALUES ({p}, {p})", (group_id, session['user_id']))
        db.commit()
        return jsonify({'success': True, 'message': 'Group created successfully!'})

    cursor.execute(f'''
    SELECT g.*, COUNT(gm.id) AS member_count
    FROM groups g LEFT JOIN group_members gm ON g.id = gm.group_id
    GROUP BY g.id ORDER BY g.id DESC
    ''')
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
    cursor.execute(
        f"INSERT INTO partner_requests (user_id, amount, reference_note) VALUES ({p}, 2000.0, {p})",
        (session['user_id'], note)
    )
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

    cursor.execute(
        f"UPDATE users SET wallet_balance = wallet_balance - {p} WHERE id = {p} AND wallet_balance >= {p}",
        (amount, uid, amount)
    )
    if cursor.rowcount == 0:
        return jsonify({'success': False, 'message': 'Insufficient wallet balance.'}), 400

    cursor.execute(
        f'''INSERT INTO payout_requests (user_id, amount, bank_name, account_number, account_name)
        VALUES ({p}, {p}, {p}, {p}, {p})''',
        (uid, amount, bank_name, account_number, account_name)
    )
    cursor.execute(
        f'''INSERT INTO transactions (user_id, amount, tx_type, description)
        VALUES ({p}, {p}, 'Bank Cashout Request', {p})''',
        (uid, amount, f"Cashout to {bank_name} ({account_number})")
    )
    db.commit()
    return jsonify({'success': True, 'message': 'Cashout request submitted!'})

# ======================================================================
# MULTI-PILLAR API
# ======================================================================
@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401
        cursor.execute(f"SELECT user_type FROM users WHERE id = {p}", (session['user_id'],))
        me = cursor.fetchone()
        if not me or me['user_type'] not in ('CPN Partner', 'Admin'):
            return jsonify({
                'success': False,
                'message': 'You must be a CPN Partner to list items/services. Upgrade to CPN Partner first.',
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

        cursor.execute(
            f'''INSERT INTO products (user_id, title, category, price, description, whatsapp_number, image_url, video_url, listing_type)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})''',
            (session['user_id'], title, category, price, description, whatsapp, image_url, video_url, listing_type)
        )
        db.commit()
        return jsonify({'success': True, 'message': f'Listing published on Ijebu {listing_type} Hub!'})

    q = request.args.get('q', '').strip().lower()
    listing_type = request.args.get('type', 'Market').strip()
    sql = f'''
    SELECT p.*, u.full_name AS seller_name, u.username AS seller_username,
           u.is_verified_merchant, u.user_type
    FROM products p
    JOIN users u ON p.user_id = u.id
    WHERE p.status = 'active' AND p.listing_type = {p}
    '''
    params = [listing_type]
    if q:
        sql += f" AND (LOWER(p.title) LIKE {p} OR LOWER(p.description) LIKE {p} OR LOWER(p.category) LIKE {p})"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    sql += ' ORDER BY p.id DESC'
    cursor.execute(sql, tuple(params))
    result = [dict(r) for r in cursor.fetchall()]
    return jsonify(result)

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
    cursor.execute(
        f'''UPDATE users SET age={p}, gender={p}, relationship_intent={p}, bio={p}, occupation={p}, is_dating_active={p}
        WHERE id={p}''',
        (age, gender, intent, bio, occupation, is_active, session['user_id'])
    )
    db.commit()
    return jsonify({'success': True, 'message': 'Dating profile updated!'})

@app.route('/api/dating/matches', methods=['GET'])
def get_dating_matches():
    db = get_db()
    cursor = db.cursor()
    p = query_param()
    current_uid = session.get('user_id') or 0

    sql = f'''
    SELECT id, full_name, username, user_type, age, gender,
           relationship_intent, bio, occupation, avatar_url, created_at
    FROM users
    WHERE is_dating_active = 1 AND id != {p}
    ORDER BY id DESC LIMIT 50
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
        add_notification(receiver_id, uid, 'wink', uid, f"{session['full_name']} sent you a Wink / Icebreaker 👋 on Dating Match!")
        return jsonify({'success': True, 'message': 'Wink sent successfully!'})
    except Exception:
        return jsonify({'success': False, 'message': 'Already sent a wink to this member.'})

@app.route('/api/users/profile/update', methods=['POST'])
def update_user_profile_media():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    avatar_url = data.get('avatar_url', '').strip()
    cover_url = data.get('cover_url', '').strip()
    bio = data.get('bio', '').strip()

    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if avatar_url:
        cursor.execute(f"UPDATE users SET avatar_url = {p} WHERE id = {p}", (avatar_url, session['user_id']))
    if cover_url:
        cursor.execute(f"UPDATE users SET cover_url = {p} WHERE id = {p}", (cover_url, session['user_id']))
    if bio:
        cursor.execute(f"UPDATE users SET bio = {p} WHERE id = {p}", (bio, session['user_id']))
    db.commit()
    return jsonify({'success': True, 'message': 'Profile updated!'})

# ======================================================================
# SOCIAL FEED, EVENTS & NESTED COMMENTS
# ======================================================================
@app.route('/api/posts', methods=['GET', 'POST'])
def handle_posts():
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401
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
            (session['user_id'], group_id, content, image_url, video_url, post_type)
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
    FROM posts p
    JOIN users u ON p.user_id = u.id
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
    FROM comments c
    JOIN users u ON c.user_id = u.id
    WHERE c.post_id = {p}
    ORDER BY c.id ASC
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
    SELECT id, full_name, username, user_type, referral_code, wallet_balance,
           is_verified_merchant, age, gender, relationship_intent, bio, occupation,
           avatar_url, cover_url, created_at
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
    return jsonify({'success': True, 'user': res})

# ======================================================================
# CHAT API (FIXED FOR SENDER & RECEIVER THREADS)
# ======================================================================
def _is_blocked(cursor, p, a, b):
    cursor.execute(f"SELECT 1 FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}", (a, b))
    return cursor.fetchone() is not None

@app.route('/api/chat/unread', methods=['GET'])
def chat_unread():
    if 'user_id' not in session:
        return jsonify({'success': True, 'count': 0})
    db = get_db(); cursor = db.cursor(); p = query_param()
    cursor.execute(f"SELECT COUNT(*) FROM messages WHERE receiver_id = {p} AND is_read = 0", (session['user_id'],))
    return jsonify({'success': True, 'count': cursor.fetchone()[0]})

@app.route('/api/chat/partners', methods=['GET'])
def chat_partners():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db(); cursor = db.cursor(); p = query_param()
    uid = session['user_id']

    cursor.execute(f'''
    SELECT CASE WHEN sender_id = {p} THEN receiver_id ELSE sender_id END AS other_id,
           MAX(id) AS last_id
    FROM messages
    WHERE sender_id = {p} OR receiver_id = {p}
    GROUP BY other_id ORDER BY MAX(id) DESC
    ''', (uid, uid))

    result = []
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

        result.append({
            'user': dict(u),
            'last_message': (m['content'] if m else '')[:60],
            'last_from_me': (m['sender_id'] == uid) if m else False,
            'last_time': str(m['created_at']) if m else '',
            'unread': unread
        })
    return jsonify({'success': True, 'partners': result})

@app.route('/api/chat/<username>', methods=['GET', 'POST'])
def chat_thread(username):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db(); cursor = db.cursor(); p = query_param()
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
        add_notification(other_id, uid, 'message', uid, f"New chat message from {session['full_name']}")
        return jsonify({'success': True, 'message': 'Sent.'})

    cursor.execute(f"UPDATE messages SET is_read = 1 WHERE sender_id = {p} AND receiver_id = {p}", (other_id, uid))
    db.commit()

    cursor.execute(f'''
    SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_read, m.created_at,
           u.full_name, u.username
    FROM messages m JOIN users u ON m.sender_id = u.id
    WHERE (m.sender_id = {p} AND m.receiver_id = {p})
       OR (m.sender_id = {p} AND m.receiver_id = {p})
    ORDER BY m.id ASC LIMIT 300
    ''', (uid, other_id, other_id, uid))
    messages = [dict(r) for r in cursor.fetchall()]
    return jsonify({'success': True, 'other': dict(other), 'messages': messages, 'me_id': uid})

@app.route('/api/chat/block/<username>', methods=['POST'])
def block_user(username):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db(); cursor = db.cursor(); p = query_param()
    uid = session['user_id']
    cursor.execute(f"SELECT id FROM users WHERE LOWER(username) = {p}", (username.lower(),))
    other = cursor.fetchone()
    if not other:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    cursor.execute(f"SELECT id FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}", (uid, other['id']))
    if cursor.fetchone():
        cursor.execute(f"DELETE FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}", (uid, other['id']))
        db.commit()
        return jsonify({'success': True, 'blocked': False, 'message': 'User unblocked.'})
    else:
        cursor.execute(f"INSERT INTO blocked_users (blocker_id, blocked_id) VALUES ({p}, {p})", (uid, other['id']))
        db.commit()
        return jsonify({'success': True, 'blocked': True, 'message': 'User blocked.'})

# ======================================================================
# ADMIN API
# ======================================================================
@app.route('/api/admin/overview', methods=['GET'])
def get_admin_overview():
    admin, err = require_admin()
    if err: return err
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

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_products': total_products,
        'total_partner_wallets': total_wallets,
        'pending_partners': pending_partners
    })

@app.route('/api/admin/users', methods=['GET', 'DELETE'])
def admin_manage_users():
    admin, err = require_admin()
    if err: return err
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'DELETE':
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'message': 'User ID required.'}), 400
        cursor.execute(f"DELETE FROM posts WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM products WHERE user_id = {p}", (user_id,))
        cursor.execute(f"DELETE FROM users WHERE id = {p}", (user_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'User account removed.'})

    cursor.execute("SELECT id, full_name, username, phone, user_type, referral_code, created_at FROM users ORDER BY id DESC")
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/partner-requests', methods=['GET', 'POST'])
def admin_partner_requests():
    admin, err = require_admin()
    if err: return err
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
            add_notification(uid, 0, 'system', 0, "Congratulations! Your CPN Partner status has been approved!")
            return jsonify({'success': True, 'message': 'Member approved as CPN Partner!'})
        else:
            cursor.execute(f"UPDATE partner_requests SET status = 'rejected' WHERE id = {p}", (req_id,))
            db.commit()
            return jsonify({'success': True, 'message': 'Partner claim rejected.'})

    cursor.execute('''
    SELECT pr.*, u.full_name, u.phone, u.username
    FROM partner_requests pr
    JOIN users u ON pr.user_id = u.id ORDER BY pr.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

@app.route('/api/admin/payouts', methods=['GET', 'POST'])
def manage_payouts():
    admin, err = require_admin()
    if err: return err
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
                cursor.execute(
                    f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}",
                    (float(req['amount'] or 0), req['user_id'])
                )
        cursor.execute(f"UPDATE payout_requests SET status = {p} WHERE id = {p}", (new_status, payout_id))
        db.commit()
        return jsonify({'success': True, 'message': f'Payout marked as {new_status}.'})

    cursor.execute('''
    SELECT pr.*, u.full_name, u.phone, u.username
    FROM payout_requests pr
    JOIN users u ON pr.user_id = u.id ORDER BY pr.id DESC
    ''')
    return jsonify([dict(r) for r in cursor.fetchall()])

# ======================================================================
# FRONTEND MOBILE TEMPLATES & VIEW ROUTES
# ======================================================================
INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Ijebu Connect - Mobile Hub</title>
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
body { background: var(--bg-body); color: var(--text-dark); pb: 70px; display: flex; flex-direction: column; min-height: 100vh; }

#toast-container { position: fixed; top: 12px; right: 12px; left: 12px; z-index: 9999; }
.toast { background: var(--navy-blue); color: #fff; padding: 12px; border-radius: 12px; margin-bottom: 8px; font-size: 0.85rem; font-weight: 600; text-align: center; }
.toast.success { background: var(--emerald-green); }
.toast.error { background: #ef4444; }

header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-box { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.brand-title { font-size: 1.1rem; font-weight: 800; color: var(--navy-blue); line-height: 1; }
.brand-title span { color: var(--emerald-green); }
.header-actions { display: flex; align-items: center; gap: 10px; }
.icon-btn { background: #f1f5f9; border: none; width: 38px; height: 38px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1rem; color: var(--navy-blue); position: relative; cursor: pointer; }
.badge-count { position: absolute; top: -2px; right: -2px; background: #ef4444; color: #fff; font-size: 0.65rem; font-weight: 800; padding: 2px 6px; border-radius: 10px; }

/* SEARCH BAR */
.search-container { padding: 0.5rem 1rem; background: #fff; border-bottom: 1px solid var(--border-light); }
.search-input { width: 100%; padding: 10px 14px; border-radius: 20px; border: 1.5px solid var(--border-light); font-size: 0.85rem; outline: none; background: #f8fafc; }

/* MOBILE BOTTOM NAV BAR */
.mobile-bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1.5px solid var(--border-light); display: flex; justify-content: space-around; padding: 6px 0; z-index: 1000; height: 60px; }
.nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.7rem; font-weight: 700; flex: 1; cursor: pointer; text-decoration: none; position: relative; }
.nav-item i { font-size: 1.2rem; margin-bottom: 2px; }
.nav-item.active { color: var(--navy-blue); }

.app-container { max-width: 600px; margin: 0 auto; width: 100%; padding: 0.75rem 0.75rem 80px; flex: 1; }
.view-section { display: none; }
.view-section.active { display: block; }

.card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 1rem; margin-bottom: 0.85rem; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; background: #fff; }
.btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; }

.feed-post { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 0.88rem; margin-bottom: 0.85rem; }
.post-header { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 0.9rem; flex-shrink: 0; background-size: cover; background-position: center; }
.post-actions { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid var(--border-light); }
.post-action { flex: 1; background: none; border: none; padding: 6px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 4px; }

/* COMMENTS STYLING */
.comments-box { background: #f8fafc; border-radius: 12px; padding: 8px; margin-top: 8px; }
.comment-item { border-bottom: 1px solid #e2e8f0; padding: 6px 0; font-size: 0.82rem; }
.comment-item:last-child { border-bottom: none; }
.comment-user { font-weight: 800; color: var(--navy-blue); }

.chat-bubble { max-width:80%; padding:8px 12px; border-radius:14px; margin-bottom:6px; font-size:0.85rem; word-wrap:break-word; }
.chat-bubble.me { background:var(--emerald-green); color:#fff; margin-left:auto; border-bottom-right-radius:2px; }
.chat-bubble.them { background:#fff; border:1.5px solid var(--border-light); border-bottom-left-radius:2px; }
</style>
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

<div class="search-container">
  <input type="text" class="search-input" id="global-search-input" placeholder="🔍 Search people, market, jobs, groups..." onkeyup="handleSearch()">
</div>

<div class="app-container">

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
      <form onsubmit="handlePostSubmit(event, 'Social')">
        <textarea class="form-control" id="post-content" rows="2" placeholder="What's happening in Ijebu today?..."></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <input type="file" id="post-file-input" class="form-control" accept="image/*,video/*" style="padding:4px;">
        </div>
        <button type="submit" class="btn-submit">Publish Update</button>
      </form>
    </div>
    <div id="feed-posts-container"></div>
  </div>

  <!-- MARKETPLACE -->
  <div id="view-market" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Marketplace</h3>
      <button onclick="startSellItem('Market')" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ List Item</button>
    </div>
    <div id="products-container"></div>
  </div>

  <!-- GROUPS HUB -->
  <div id="view-groups" class="view-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);">Community Groups</h3>
      <button onclick="openGroupCreateModal()" style="background:var(--navy-blue);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;">+ Create Group</button>
    </div>
    <div id="groups-container"></div>
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

  <!-- CHAT -->
  <div id="view-chat" class="view-section">
    <div id="chat-list-wrap">
      <h3 style="font-size:1rem;font-weight:800;color:var(--navy-blue);margin-bottom:8px;">Messages</h3>
      <div id="chat-partners-container"></div>
    </div>
    <div id="chat-thread-wrap" style="display:none;">
      <button onclick="closeChatThread()" style="background:#fff;border:1px solid var(--border-light);padding:4px 10px;border-radius:8px;font-size:0.75rem;font-weight:700;margin-bottom:8px;">← Back</button>
      <div id="chat-thread-header" class="card" style="padding:0.5rem 0.88rem;display:flex;justify-content:space-between;align-items:center;"></div>
      <div id="chat-messages" style="min-height:220px;max-height:50vh;overflow-y:auto;"></div>
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

<!-- MOBILE BOTTOM NAVIGATION -->
<div class="mobile-bottom-nav">
  <div class="nav-item active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-house"></i> Home</div>
  <div class="nav-item" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-store"></i> Market</div>
  <div class="nav-item" data-nav="groups" onclick="switchNav('groups')"><i class="fa-solid fa-users"></i> Groups</div>
  <div class="nav-item" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
  <div class="nav-item" data-nav="chat" onclick="switchNav('chat')"><i class="fa-solid fa-comments"></i> Chat</div>
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

function switchNav(target) {
  document.querySelectorAll('.nav-item').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
  const pill = document.querySelector(`.nav-item[data-nav="${target}"]`);
  if(pill) pill.classList.add('active');
  const view = document.getElementById(`view-${target}`);
  if(view) view.classList.add('active');

  if(target === 'feed') loadPosts('Social', 'feed-posts-container');
  if(target === 'market') loadCategoryListings('Market', 'products-container');
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
      if(currentUser.unread_notifs > 0) {
        const badge = document.getElementById('notif-badge');
        badge.innerText = currentUser.unread_notifs;
        badge.style.display = 'block';
      }
      refreshUnread();
    } else {
      currentUser = null;
      renderHeaderAuth();
    }
  } catch(e){}
}

function renderHeaderAuth() {
  const box = document.getElementById('header-auth');
  if(currentUser) {
    box.innerHTML = `<button onclick="openProfile('${currentUser.username}')" style="background:#f1f5f9;border:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">@${currentUser.username}</button>`;
  } else {
    box.innerHTML = `<a href="/auth" style="background:var(--navy-blue);color:#fff;text-decoration:none;padding:6px 12px;border-radius:16px;font-weight:700;font-size:0.75rem;">Sign In</a>`;
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

async function loadPosts(postType, containerId) {
  const res = await fetch(`/api/posts?type=${postType}`);
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
  if(p.video_url) mediaHtml = `<video src="${p.video_url}" controls style="width:100%;border-radius:10px;margin-top:6px;"></video>`;
  else if(p.image_url) mediaHtml = `<img src="${p.image_url}" style="width:100%;border-radius:10px;margin-top:6px;">`;

  return `
  <div class="feed-post">
    <div class="post-header">
      <div class="avatar" style="background:var(--navy-blue);" onclick="openProfile('${p.username}')">${p.avatar_url ? `<img src="${p.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.full_name.charAt(0)}</div>
      <div>
        <div style="font-size:0.85rem;font-weight:800;" onclick="openProfile('${p.username}')">${p.full_name}</div>
        <div style="font-size:0.7rem;color:var(--text-muted);">@${p.username}</div>
      </div>
    </div>
    <div style="font-size:0.88rem;line-height:1.4;">${p.content}</div>
    ${mediaHtml}
    <div class="post-actions">
      <button class="post-action" onclick="toggleLike(${p.id})">❤️ ${p.likes_count}</button>
      <button class="post-action" onclick="toggleComments(${p.id})">💬 Comments</button>
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
      ${comments.map(c => `<div class="comment-item"><span class="comment-user">@${c.username}:</span> ${c.content}</div>`).join('') || '<small>No comments yet.</small>'}
    </div>
    <div style="display:flex;gap:4px;margin-top:6px;">
      <input type="text" id="comment-input-${pid}" class="form-control" placeholder="Write a comment..." style="padding:6px;font-size:0.78rem;">
      <button onclick="submitComment(${pid})" style="background:var(--emerald-green);color:#fff;border:none;padding:6px 10px;border-radius:8px;font-weight:700;font-size:0.75rem;">Post</button>
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
    body: JSON.stringify({content})
  });
  input.value = '';
  toggleComments(pid);
}

async function openProfile(username) {
  const res = await fetch(`/api/users/${encodeURIComponent(username)}`);
  const data = await res.json();
  if(!data.success) return showToast(data.message, 'error');
  const u = data.user;
  const isSelf = currentUser && currentUser.id === u.id;

  const c = document.getElementById('profile-wall-container');
  c.innerHTML = `
    <div class="card">
      <div style="display:flex;gap:10px;align-items:center;">
        <div class="avatar" style="width:50px;height:50px;background:var(--navy-blue);">${u.avatar_url ? `<img src="${u.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : u.full_name.charAt(0)}</div>
        <div>
          <h3 style="font-size:1rem;font-weight:800;">${u.full_name}</h3>
          <p style="font-size:0.75rem;color:var(--text-muted);">@${u.username} • ${u.user_type}</p>
        </div>
      </div>
      <p style="font-size:0.82rem;margin:8px 0;">${u.bio || 'Resident of Ijebu'}</p>
      ${!isSelf ? `<button onclick="toggleFollow('${u.username}')" style="background:var(--navy-blue);color:#fff;border:none;padding:6px 12px;border-radius:8px;font-weight:700;font-size:0.78rem;width:100%;">${u.is_following ? 'Unfollow' : 'Follow'}</button>` : ''}
    </div>
  `;
  switchNav('profile');
}

async function toggleFollow(username) {
  if(!currentUser) return window.location.href = '/auth';
  const res = await fetch(`/api/users/${encodeURIComponent(username)}/follow`, {method:'POST'});
  const data = await res.json();
  showToast(data.message);
  openProfile(username);
}

async function loadChatPartners() {
  const res = await fetch('/api/chat/partners');
  const data = await res.json();
  const c = document.getElementById('chat-partners-container');
  if(!data.success || !data.partners.length) {
    c.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No messages yet.</div>`;
    return;
  }
  c.innerHTML = data.partners.map(p => `
    <div onclick="openChatThread('${p.user.username}')" class="card" style="display:flex;gap:10px;align-items:center;cursor:pointer;">
      <div class="avatar" style="background:var(--navy-blue);">${p.user.avatar_url ? `<img src="${p.user.avatar_url}" style="width:100%;height:100%;border-radius:50%;">` : p.user.full_name.charAt(0)}</div>
      <div style="flex:1;">
        <div style="font-weight:800;font-size:0.88rem;">${p.user.full_name}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);">${p.last_message}</div>
      </div>
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

  document.getElementById('chat-thread-header').innerHTML = `<b>${data.other.full_name}</b> (@${data.other.username})`;
  const m = document.getElementById('chat-messages');
  m.innerHTML = data.messages.map(msg => `
    <div class="chat-bubble ${msg.sender_id === data.me_id ? 'me' : 'them'}">
      ${msg.content}
    </div>
  `).join('');
  m.scrollTop = m.scrollHeight;
}

function closeChatThread() {
  currentChatUser = null;
  document.getElementById('chat-list-wrap').style.display = 'block';
  document.getElementById('chat-thread-wrap').style.display = 'none';
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
:root { --navy-blue: #0b1e36; --emerald-green: #059669; --border-light: #cbd5e1; }
* { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; }
body { background: #f8fafc; color: #0f172a; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 1rem; }
.auth-card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 18px; padding: 1.5rem; max-width: 400px; width: 100%; text-align: center; }
.brand { font-size: 1.25rem; font-weight: 800; color: var(--navy-blue); margin-bottom: 1rem; }
.form-group { display: flex; flex-direction: column; gap: 4px; margin-bottom: 0.85rem; text-align: left; }
.form-control { padding: 10px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.88rem; outline: none; width: 100%; }
.btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.88rem; cursor: pointer; width: 100%; }
</style>
</head>
<body>
<div class="auth-card">
  <div class="brand">IJEBU <span>CONNECT</span></div>
  <form id="form-login" onsubmit="handleLogin(event)">
    <div class="form-group"><label style="font-size:0.82rem;font-weight:700;">Username or Phone</label><input type="text" id="login-uname" class="form-control" required></div>
    <div class="form-group"><label style="font-size:0.82rem;font-weight:700;">Password</label><input type="password" id="login-pword" class="form-control" required></div>
    <button type="submit" class="btn-submit" style="background:var(--navy-blue);">Sign In</button>
  </form>
</div>
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
<meta charset="UTF-8"><title>Admin Panel</title>
</head>
<body style="font-family:sans-serif;padding:1rem;">
  <h2>Admin Panel - Ijebu Connect</h2>
  <a href="/">← Back to Main App</a>
</body>
</html>
"""

# ======================================================================
# ROUTE HANDLERS
# ======================================================================
@app.route('/')
def index():
    return render_template_string(INDEX_TEMPLATE, contact_email=CONTACT_EMAIL)

@app.route('/auth')
def auth_page():
    return render_template_string(AUTH_TEMPLATE)

@app.route('/admin')
def admin_page():
    return render_template_string(ADMIN_TEMPLATE)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)