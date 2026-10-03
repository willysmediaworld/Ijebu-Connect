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
    g, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# =============================================================================
# CONFIGURATION
# =============================================================================

DATABASE_URL = os.environ.get('DATABASE_URL')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '').strip()
ALLOW_TEST_PAYMENTS = os.environ.get('ALLOW_TEST_PAYMENTS', 'True').lower() == 'true'
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', 'willysmediaworld@gmail.com')

# Your Official Bank Account Details for CPN Manual Transfers
BANK_INFO = {
    "bank_name": "OPay",
    "account_number": "09018363715",
    "account_name": "Rotimi Williams Oladele",
    "fee_naira": 2000
}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'ijebu_connect_secret_key_2026_secured'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max limit (Supports Videos)

# Static Upload Setup
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTS = {'mp4', 'webm', 'mov', 'm4v', 'avi'}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTS.union(ALLOWED_VIDEO_EXTS)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# =============================================================================
# DATABASE ENGINE
# =============================================================================

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

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        p = query_param()

        is_postgres = bool(DATABASE_URL)
        pk_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # USERS TABLE
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

        # PRODUCTS / MARKETPLACE / SERVICES / JOBS TABLE
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
        safe_add_column(cursor, 'products', 'image_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'products', 'video_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'products', 'listing_type', "TEXT DEFAULT 'Market'")

        # POSTS / EVENTS TABLE
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS posts (
                id {pk_type},
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                post_type TEXT DEFAULT 'Social',
                image_url TEXT DEFAULT '',
                video_url TEXT DEFAULT '',
                likes_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        safe_add_column(cursor, 'posts', 'image_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'posts', 'video_url', "TEXT DEFAULT ''")
        safe_add_column(cursor, 'posts', 'post_type', "TEXT DEFAULT 'Social'")

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
        admin_phone    = os.environ.get('ADMIN_SEED_PHONE',    '09018363715')
        admin_name     = os.environ.get('ADMIN_SEED_NAME',     "Sir Ola'Rotimi")
        admin_ref      = os.environ.get('ADMIN_SEED_REF',      'CPN00001')

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
                    UPDATE users 
                    SET password_hash = {p}, user_type = 'Admin' 
                    WHERE id = {p}
                ''', (admin_pass_hash, existing_admin['id']))
                db.commit()
            except Exception:
                db.rollback()

with app.app_context():
    init_db()


# =============================================================================
# FILE & MEDIA UPLOADER (PHOTO & VIDEO)
# =============================================================================

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


# =============================================================================
# CPN COMMISSION ENGINE
# =============================================================================

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

        if t1['referred_by']:
            cursor.execute(f"SELECT id FROM users WHERE referral_code = {p}", (t1['referred_by'],))
            t2 = cursor.fetchone()
            if t2:
                bonus2 = upgrade_fee * 0.05
                cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}", (bonus2, t2['id']))
                cursor.execute(f'''INSERT INTO transactions (user_id, amount, tx_type, description)
                                    VALUES ({p}, {p}, 'Tier-2 CPN Commission', {p})''',
                                (t2['id'], bonus2, f"5% CPN Reward from {buyer['full_name']}"))
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


# =============================================================================
# AUTH API ENDPOINTS
# =============================================================================

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
            return jsonify({'logged_in': True, 'user': d})
    return jsonify({'logged_in': False})


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out.'})


# =============================================================================
# CPN & PAYMENTS
# =============================================================================

@app.route('/api/cpn/claim-bank-transfer', methods=['POST'])
def claim_bank_transfer():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    data = request.json or {}
    note = data.get('reference_note', '').strip()
    if not note:
        return jsonify({'success': False, 'message': 'Please enter transfer sender name or reference note.'}), 400

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    cursor.execute(
        f"INSERT INTO partner_requests (user_id, amount, reference_note) VALUES ({p}, 2000.0, {p})",
        (session['user_id'], note)
    )
    db.commit()
    return jsonify({'success': True, 'message': 'Payment claim submitted! Admin will verify and activate your CPN Partner status.'})


@app.route('/api/cpn/upgrade', methods=['POST'])
def upgrade_to_cpn():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    if PAYSTACK_SECRET_KEY:
        cursor.execute(f"SELECT username FROM users WHERE id = {p}", (uid,))
        user = cursor.fetchone()
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "email": f"{user['username']}@ijebuconnect.com",
            "amount": 2000 * 100,
            "callback_url": f"{request.host_url}api/cpn/verify-payment",
            "metadata": {"user_id": uid}
        }
        try:
            res = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers=headers)
            res_data = res.json()
            if res_data.get('status'):
                return jsonify({'success': True, 'paystack': True, 'redirect_url': res_data['data']['authorization_url']})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    return jsonify({'success': False, 'message': 'Paystack key not configured. Please use Direct Bank Transfer option.'})


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


# =============================================================================
# MULTI-PILLAR API (MARKET, BEAUTY, JOBS, DATING, EVENTS)
# =============================================================================

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

    result = []
    for r in cursor.fetchall():
        d = dict(r)
        d['price'] = float(d.get('price') or 0)
        result.append(d)
    return jsonify(result)


# DATING API
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
        f'''UPDATE users 
            SET age={p}, gender={p}, relationship_intent={p}, bio={p}, occupation={p}, is_dating_active={p}
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


# UPDATE FB-STYLE PROFILE
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


# =============================================================================
# SOCIAL FEED & EVENTS
# =============================================================================

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

        if not content and not image_url and not video_url:
            return jsonify({'success': False, 'message': 'Write something or attach an image/video.'}), 400

        cursor.execute(
            f"INSERT INTO posts (user_id, content, image_url, video_url, post_type) VALUES ({p}, {p}, {p}, {p}, {p})",
            (session['user_id'], content, image_url, video_url, post_type)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Published successfully!'})

    current_uid = session.get('user_id') or 0
    post_type_filter = request.args.get('type', 'Social')

    cursor.execute(
        f'''
        SELECT p.id, p.user_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
               u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
               CASE WHEN EXISTS (
                   SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}
               ) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.post_type = {p}
        ORDER BY p.id DESC LIMIT 60
        ''',
        (current_uid, post_type_filter)
    )
    return jsonify([dict(r) for r in cursor.fetchall()])


@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
def toggle_post_like(post_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT id FROM post_likes WHERE post_id = {p} AND user_id = {p}", (post_id, uid))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(f"DELETE FROM post_likes WHERE id = {p}", (existing['id'],))
        liked = False
    else:
        cursor.execute(f"INSERT INTO post_likes (post_id, user_id) VALUES ({p}, {p})", (post_id, uid))
        liked = True
    db.commit()

    cursor.execute(f"SELECT COUNT(*) FROM post_likes WHERE post_id = {p}", (post_id,))
    return jsonify({'success': True, 'liked': liked, 'likes_count': cursor.fetchone()[0]})


# =============================================================================
# PUBLIC MEMBER PROFILE & WALL
# =============================================================================

@app.route('/api/users/<username>', methods=['GET'])
def get_user_profile(username):
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    cursor.execute(
        f'''SELECT id, full_name, username, user_type, referral_code, wallet_balance, 
                   is_verified_merchant, age, gender, relationship_intent, bio, occupation,
                   avatar_url, cover_url, created_at
            FROM users WHERE LOWER(username) = {p}''',
        (username.lower(),)
    )
    user = cursor.fetchone()
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    uid = user['id']
    current_uid = session.get('user_id') or 0

    cursor.execute(f"SELECT COUNT(*) FROM users WHERE referred_by = {p}", (user['referral_code'],))
    recruits_count = cursor.fetchone()[0]

    cursor.execute(
        f'''
        SELECT p.id, p.user_id, p.content, p.post_type, p.image_url, p.video_url, p.created_at,
               u.full_name, u.username, u.user_type, u.avatar_url, u.is_verified_merchant,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
               CASE WHEN EXISTS (
                   SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}
               ) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.user_id = {p} ORDER BY p.id DESC
        ''',
        (current_uid, uid)
    )
    posts = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        f"SELECT * FROM products WHERE user_id = {p} AND status = 'active' ORDER BY id DESC",
        (uid,)
    )
    products = []
    for r in cursor.fetchall():
        d = dict(r)
        d['price'] = float(d.get('price') or 0)
        products.append(d)

    res = dict(user)
    res['wallet_balance'] = float(res.get('wallet_balance') or 0)
    res['recruits_count'] = recruits_count
    res['posts'] = posts
    res['products'] = products
    res['posts_count'] = len(posts)
    res['products_count'] = len(products)
    return jsonify({'success': True, 'user': res})


# =============================================================================
# CHAT API
# =============================================================================

def _is_blocked(cursor, p, a, b):
    cursor.execute(
        f"SELECT 1 FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}",
        (a, b)
    )
    return cursor.fetchone() is not None


@app.route('/api/chat/unread', methods=['GET'])
def chat_unread():
    if 'user_id' not in session:
        return jsonify({'success': True, 'count': 0})
    db = get_db(); cursor = db.cursor(); p = query_param()
    cursor.execute(
        f"SELECT COUNT(*) FROM messages WHERE receiver_id = {p} AND is_read = 0",
        (session['user_id'],)
    )
    return jsonify({'success': True, 'count': cursor.fetchone()[0]})


@app.route('/api/chat/partners', methods=['GET'])
def chat_partners():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401
    db = get_db(); cursor = db.cursor(); p = query_param()
    uid = session['user_id']

    cursor.execute(f'''
        SELECT
            CASE WHEN sender_id = {p} THEN receiver_id ELSE sender_id END AS other_id,
            MAX(id) AS last_id
        FROM messages
        WHERE sender_id = {p} OR receiver_id = {p}
        GROUP BY other_id
        ORDER BY MAX(id) DESC
    ''', (uid, uid))

    result = []
    for row in cursor.fetchall():
        other_id = row['other_id']
        last_id  = row['last_id']

        cursor.execute(f"SELECT id, full_name, username, user_type, avatar_url FROM users WHERE id = {p}", (other_id,))
        u = cursor.fetchone()
        if not u:
            continue

        cursor.execute(f"SELECT content, sender_id, created_at FROM messages WHERE id = {p}", (last_id,))
        m = cursor.fetchone()

        cursor.execute(
            f"SELECT COUNT(*) FROM messages WHERE sender_id = {p} AND receiver_id = {p} AND is_read = 0",
            (other_id, uid)
        )
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

    if other_id == uid:
        return jsonify({'success': False, 'message': 'You cannot chat with yourself.'}), 400

    if request.method == 'POST':
        data = request.json or {}
        content = (data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400

        if _is_blocked(cursor, p, uid, other_id) or _is_blocked(cursor, p, other_id, uid):
            return jsonify({'success': False, 'message': 'Cannot send message.'}), 403

        cursor.execute(
            f"INSERT INTO messages (sender_id, receiver_id, content) VALUES ({p}, {p}, {p})",
            (uid, other_id, content)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Sent.'})

    cursor.execute(
        f"UPDATE messages SET is_read = 1 WHERE sender_id = {p} AND receiver_id = {p}",
        (other_id, uid)
    )
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

    cursor.execute(
        f"SELECT id FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}",
        (uid, other['id'])
    )
    if cursor.fetchone():
        cursor.execute(
            f"DELETE FROM blocked_users WHERE blocker_id = {p} AND blocked_id = {p}",
            (uid, other['id'])
        )
        db.commit()
        return jsonify({'success': True, 'blocked': False, 'message': 'User unblocked.'})

    cursor.execute(
        f"INSERT INTO blocked_users (blocker_id, blocked_id) VALUES ({p}, {p})",
        (uid, other['id'])
    )
    db.commit()
    return jsonify({'success': True, 'blocked': True, 'message': 'User blocked.'})


# =============================================================================
# ADMIN API (WITH MEMBER DELETION & PARTNER APPROVALS)
# =============================================================================

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
        action = data.get('action') # 'approve' or 'reject'

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


# =============================================================================
# FRONTEND TEMPLATES & VIEW ROUTES
# =============================================================================

INDEX_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ijebu Connect - Connect. Discover. Trade. Belong.</title>
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
	body { background: var(--bg-body); color: var(--text-dark); display: flex; flex-direction: column; min-height: 100vh; }
	
	#toast-container { position: fixed; top: 16px; right: 16px; z-index: 9999; }
	.toast { background: var(--navy-blue); color: #fff; padding: 12px 18px; border-radius: 10px; margin-bottom: 8px; box-shadow: 0 8px 20px rgba(0,0,0,.15); font-size: 0.88rem; font-weight: 600; }
	.toast.success { background: var(--emerald-green); } .toast.error { background: #ef4444; }
	
	header { background: #fff; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1.5px solid var(--border-light); position: sticky; top:0; z-index: 100; }
	.brand-box { display: flex; align-items: center; gap: 10px; cursor: pointer; }
    .brand-logo-img { height: 42px; width: auto; object-fit: contain; }
	.brand-title { font-size: 1.05rem; font-weight: 800; color: var(--navy-blue); line-height:1.1; }
	.brand-title span { color: var(--emerald-green); }
	
	.header-auth { display: flex; align-items: center; gap: 6px; }
	.btn-header-login { background: var(--navy-blue); color: #fff; text-decoration: none; padding: 7px 14px; border-radius: 20px; font-weight: 700; font-size: 0.78rem; }
	.header-user-pill { background: #f1f5f9; color: var(--navy-blue); padding: 6px 10px; border-radius: 20px; font-weight: 700; font-size: 0.75rem; cursor: pointer; border: none; max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
	.header-logout-btn { background: #ef4444; color: #fff; border: none; padding: 6px 10px; border-radius: 20px; font-weight: 700; font-size: 0.75rem; cursor: pointer; }
	
	.top-nav-pills { display: flex; gap: 4px; padding: 0.75rem 0.5rem 0.2rem; max-width: 680px; margin: 0 auto; width: 100%; overflow-x: auto; scrollbar-width: none; }
    .top-nav-pills::-webkit-scrollbar { display: none; }
	.nav-pill { padding: 8px 12px; border-radius: 20px; font-size: 0.76rem; font-weight: 700; background: #fff; border: 1.5px solid var(--border-light); color: var(--text-muted); cursor: pointer; flex-shrink: 0; text-align: center; position: relative; display: flex; align-items: center; gap: 5px; }
	.nav-pill.active { background: var(--navy-blue); color: #fff; border-color: var(--navy-blue); }
	
	.app-container { max-width: 620px; margin: 0 auto; width: 100%; padding: 0.5rem 1rem 2rem; flex: 1; }
	.view-section { display: none; } .view-section.active { display: block; }
	
	.card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 1.25rem; margin-bottom: 0.85rem; }
	.form-group { display: flex; flex-direction: column; gap: 5px; margin-bottom: 0.85rem; }
	.form-group label { font-size: 0.82rem; font-weight: 700; color: var(--text-dark); }
	.form-control { padding: 11px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.9rem; outline: none; width: 100%; background: #fff; font-family: inherit; }
	.btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.9rem; cursor: pointer; width: 100%; }
	
	.badge { padding: 3px 8px; border-radius: 6px; font-size: 0.7rem; font-weight: 800; display: inline-block; }
	.badge-partner { background: #fef3c7; color: #92400e; } .badge-admin { background: #fee2e2; color: #991b1b; }
	
	.clickable-user { cursor: pointer; font-weight: 800; color: var(--navy-blue); }
	.clickable-user:hover { color: var(--emerald-green); text-decoration: underline; }
	
	.feed-post { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 1rem; margin-bottom: 0.85rem; }
	.post-header { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 8px; }
	.avatar { width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 0.95rem; flex-shrink: 0; cursor: pointer; overflow:hidden; background-size:cover; background-position:center; }
	.avatar img { width:100%; height:100%; object-fit:cover; }
    .post-author { font-size: 0.92rem; }
	.post-meta { font-size: 0.72rem; color: var(--text-muted); }
	.post-content { font-size: 0.92rem; line-height: 1.5; white-space: pre-wrap; margin-bottom: 8px; }
    .post-media { width: 100%; max-height: 350px; object-fit: cover; border-radius: 12px; margin-bottom: 8px; }
	.post-actions { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid var(--border-light); }
	.post-action { flex: 1; background: none; border: none; padding: 8px; border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; }
	.post-action.liked { color: #ef4444; }

	.product-card { display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--border-light); padding-bottom: 12px; margin-bottom: 12px; }
	.product-card:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
	.product-img-box { width: 76px; height: 76px; border-radius: 12px; background: #f1f5f9; display: flex; align-items: center; justify-content: center; font-size: 1.4rem; color: #0f172a; flex-shrink: 0; overflow: hidden; }
    .product-img-box img, .product-img-box video { width: 100%; height: 100%; object-fit: cover; }
	.btn-whatsapp { background: #25d366; color: #fff; border: none; padding: 8px 14px; border-radius: 10px; font-weight: 700; font-size: 0.8rem; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }

	/* FACEBOOK-STYLE PROFILE */
	.fb-profile-card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 18px; overflow: hidden; margin-bottom: 1rem; position: relative; }
	.fb-cover-banner { height: 140px; background: linear-gradient(135deg, #0b1e36, #1e3a8a); background-size: cover; background-position: center; position: relative; }
	.fb-avatar-wrap { position: absolute; bottom: -35px; left: 20px; width: 80px; height: 80px; border-radius: 50%; border: 4px solid #fff; background: var(--emerald-green); overflow: hidden; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; }
	.fb-avatar-wrap img { width: 100%; height: 100%; object-fit: cover; }
	.fb-profile-body { padding: 45px 20px 20px; }
	.fb-profile-name { font-size: 1.25rem; font-weight: 800; color: var(--navy-blue); }
	.fb-profile-bio { font-size: 0.85rem; color: var(--text-muted); margin: 6px 0 12px; line-height: 1.4; }

	.cpn-wallet-card { background: linear-gradient(135deg, #0b1e36, #1e3a8a); color: #fff; border-radius: 18px; padding: 1.25rem; margin-bottom: 1rem; }
	.cpn-row { display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; margin-bottom: 10px; }
	.val-gold { color: #f59e0b; font-weight: 800; font-size: 1.3rem; }

	.chat-bubble { max-width:75%; padding:10px 14px; border-radius:16px; margin-bottom:8px; font-size:0.9rem; line-height:1.4; word-wrap:break-word; }
	.chat-bubble.me { background:var(--emerald-green); color:#fff; margin-left:auto; border-bottom-right-radius:4px; }
	.chat-bubble.them { background:#fff; border:1.5px solid var(--border-light); border-bottom-left-radius:4px; }
	.chat-time { font-size:0.65rem; opacity:0.7; margin-top:3px; }
	.chat-partner-row { display:flex; gap:12px; align-items:center; padding:12px; border-bottom:1px solid var(--border-light); cursor:pointer; }

    .bank-box { background:#f1f5f9; border:1.5px dashed var(--navy-blue); border-radius:12px; padding:1rem; margin:1rem 0; font-size:0.88rem; line-height:1.6; }

	footer { background: #fff; text-align: center; padding: 1.5rem 1rem; font-size: 0.82rem; color: var(--text-muted); border-top: 1.5px solid var(--border-light); margin-top: auto; line-height: 1.6; }
	footer a { color: var(--emerald-green); text-decoration: none; font-weight: 700; }
</style>
</head>
<body>

<div id="toast-container"></div>

<header>
    <div class="brand-box" onclick="switchNav('feed')">
        <img src="/static/logo.png" alt="Ijebu Connect Logo" class="brand-logo-img" onerror="this.style.display='none'">
        <div class="brand-title">IJEBU<br><span>CONNECT</span></div>
    </div>
    <div class="header-auth" id="header-auth"></div>
</header>

<div class="top-nav-pills">
    <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')"><i class="fa-solid fa-users"></i> Social</div>
    <div class="nav-pill" data-nav="market" onclick="switchNav('market')"><i class="fa-solid fa-cart-shopping"></i> Market</div>
    <div class="nav-pill" data-nav="dating" onclick="switchNav('dating')"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> Dating</div>
    <div class="nav-pill" data-nav="beauty" onclick="switchNav('beauty')"><i class="fa-solid fa-scissors" style="color:#d97706;"></i> Beauty</div>
    <div class="nav-pill" data-nav="jobs" onclick="switchNav('jobs')"><i class="fa-solid fa-briefcase" style="color:#2563eb;"></i> Jobs/Services</div>
    <div class="nav-pill" data-nav="events" onclick="switchNav('events')"><i class="fa-solid fa-calendar-days" style="color:#9333ea;"></i> Events</div>
    <div class="nav-pill" data-nav="chat" onclick="switchNav('chat')">
        <i class="fa-solid fa-comments"></i> Chat
        <span id="chat-badge" style="display:none;position:absolute;top:-4px;right:-4px;background:#ef4444;color:#fff;font-size:0.65rem;font-weight:800;padding:2px 6px;border-radius:10px;">0</span>
    </div>
    <div class="nav-pill" id="admin-pill" style="display:none;" onclick="window.location.href='/admin'"><i class="fa-solid fa-gear"></i> Admin</div>
</div>

<div class="app-container">

    <!-- SOCIAL FEED -->
    <div id="view-feed" class="view-section active">
        <div class="card">
            <form onsubmit="handlePostSubmit(event, 'Social')">
                <div class="form-group">
                    <textarea class="form-control" id="post-content" rows="2" placeholder="What's happening in Ijebu today?..."></textarea>
                </div>
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
                    <label style="font-size:0.75rem;font-weight:700;">Attach Photo/Video:</label>
                    <input type="file" id="post-file-input" class="form-control" accept="image/*,video/*" style="padding:6px;">
                </div>
                <button type="submit" class="btn-submit" style="background:var(--navy-blue);">Publish Update</button>
            </form>
        </div>
        <div id="feed-posts-container"></div>
    </div>

    <!-- MARKETPLACE HUB -->
    <div id="view-market" class="view-section">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Marketplace</h3>
            <button onclick="startSellItem('Market')" style="background:var(--emerald-green);color:#fff;border:none;padding:8px 14px;border-radius:10px;font-weight:700;font-size:0.82rem;cursor:pointer;">
                + List Item
            </button>
        </div>
        <div class="card" style="padding:0.75rem;margin-bottom:1rem;">
            <input type="text" class="form-control" id="market-search" placeholder="Search farm produce, land, electronics..." onkeyup="loadCategoryListings('Market', 'products-container')">
        </div>
        <div id="products-container" class="card"></div>
    </div>

    <!-- DATING & MATCHMAKING -->
    <div id="view-dating" class="view-section">
        <div class="card" style="background:linear-gradient(135deg, #4f46e5, #7c3aed);color:#fff;">
            <h3 style="font-weight:800;margin-bottom:4px;">❤️ Ijebu Singles &amp; Match</h3>
            <p style="font-size:0.8rem;opacity:0.9;margin-bottom:10px;">Connect with verified singles across Ijebu Ode, Sagamu, Remo &amp; environs.</p>
            <button onclick="openDatingSettingsModal()" style="background:#fff;color:#4f46e5;border:none;padding:8px 14px;border-radius:10px;font-weight:800;font-size:0.8rem;cursor:pointer;">Set Up My Dating Profile</button>
        </div>
        <div id="dating-matches-container"></div>
    </div>

    <!-- BEAUTY & LIFESTYLE -->
    <div id="view-beauty" class="view-section">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Beauty &amp; Fashion Directory</h3>
            <button onclick="startSellItem('Beauty')" style="background:var(--amber-gold);color:#fff;border:none;padding:8px 14px;border-radius:10px;font-weight:700;font-size:0.82rem;cursor:pointer;">
                + Add Service
            </button>
        </div>
        <div id="beauty-container" class="card"></div>
    </div>

    <!-- JOBS & SERVICES -->
    <div id="view-jobs" class="view-section">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Jobs &amp; Artisan Directory</h3>
            <button onclick="startSellItem('Jobs')" style="background:#2563eb;color:#fff;border:none;padding:8px 14px;border-radius:10px;font-weight:700;font-size:0.82rem;cursor:pointer;">
                + Post Job/Skill
            </button>
        </div>
        <div id="jobs-container" class="card"></div>
    </div>

    <!-- EVENTS -->
    <div id="view-events" class="view-section">
        <div class="card">
            <h3 style="font-weight:800;color:var(--navy-blue);margin-bottom:8px;">📅 Local Events &amp; Festivals</h3>
            <form onsubmit="handlePostSubmit(event, 'Event')">
                <div class="form-group">
                    <textarea class="form-control" id="event-content" rows="2" placeholder="Announce an upcoming party, Ojude Oba, festival, or event..."></textarea>
                </div>
                <button type="submit" class="btn-submit" style="background:#9333ea;">Publish Event</button>
            </form>
        </div>
        <div id="events-container"></div>
    </div>

    <!-- CHAT -->
    <div id="view-chat" class="view-section">
        <div id="chat-list-wrap">
            <h3 style="font-weight:800;color:var(--navy-blue);margin-bottom:12px;">Messages</h3>
            <div id="chat-partners-container"></div>
        </div>
        <div id="chat-thread-wrap" style="display:none;">
            <button onclick="closeChatThread()" style="background:#fff;border:1.5px solid var(--border-light);padding:6px 14px;border-radius:10px;font-weight:700;font-size:0.8rem;cursor:pointer;margin-bottom:1rem;">← Back</button>
            <div id="chat-thread-header" class="card" style="padding:0.75rem 1rem;display:flex;justify-content:space-between;align-items:center;"></div>
            <div id="chat-messages" style="min-height:200px;"></div>
            <form onsubmit="sendChatMessage(event)" style="position:sticky;bottom:0;background:var(--bg-body);padding:8px 0;">
                <div style="display:flex;gap:8px;">
                    <input type="text" id="chat-input" class="form-control" placeholder="Type a message..." style="flex:1;" autocomplete="off">
                    <button type="submit" class="btn-submit" style="width:auto;padding:11px 20px;">Send</button>
                </div>
            </form>
        </div>
    </div>

    <!-- PROFILE VIEW -->
    <div id="view-profile" class="view-section">
        <button onclick="switchNav('feed')" style="background:#fff;border:1.5px solid var(--border-light);padding:6px 14px;border-radius:10px;font-weight:700;font-size:0.8rem;cursor:pointer;margin-bottom:1rem;">← Back</button>
        <div id="profile-wall-container"></div>
    </div>

</div>

<!-- CPN UPGRADE MODAL WITH DIRECT BANK TRANSFER -->
<div id="cpn-upgrade-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:460px;width:100%;background:#fff;max-height:90vh;overflow-y:auto;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Become CPN Partner (₦2,000)</h3>
            <button onclick="closeCPNModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <p style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.75rem;line-height:1.5;">
            To list products/services on Market, Beauty, or Jobs hubs, you must register as an official <strong>CPN Partner</strong>. Unlock unlimited listings and earn <strong>10% Tier-1 &amp; 5% Tier-2 referral rewards</strong> on traders you invite!
        </p>

        <div class="bank-box">
            <strong style="color:var(--navy-blue);display:block;margin-bottom:4px;">🏦 Bank Transfer Details:</strong>
            Bank Name: <b>OPay</b><br>
            Account Number: <b style="color:var(--emerald-green);font-size:1.05rem;">09018363715</b><br>
            Account Name: <b>Rotimi Williams Oladele</b><br>
            Amount: <b style="color:var(--amber-gold);">₦2,000</b>
        </div>

        <form onsubmit="handleClaimBankTransfer(event)">
            <div class="form-group">
                <label>Transfer Reference / Sender Name</label>
                <input type="text" id="cpn-ref-note" class="form-control" placeholder="e.g. Paid from OPay / John Doe" required>
            </div>
            <button type="submit" class="btn-submit" style="background:var(--emerald-green);margin-bottom:8px;">Submit Payment for Admin Approval</button>
        </form>
    </div>
</div>

<!-- SELL / LISTING MODAL -->
<div id="sell-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:480px;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);" id="modal-sell-title">Publish Listing</h3>
            <button onclick="closeSellModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <form onsubmit="handleProductSubmit(event)">
            <input type="hidden" id="prod-type" value="Market">
            <div class="form-group">
                <label>Title</label>
                <input type="text" class="form-control" id="prod-title" placeholder="e.g. Fresh Palm Oil / Bridal Makeup / Electrician" required>
            </div>
            <div class="form-group">
                <label>Category</label>
                <input type="text" class="form-control" id="prod-category" placeholder="Agriculture, Salons, Plumbing, Phones" required>
            </div>
            <div class="form-group">
                <label>Price or Fee (₦)</label>
                <input type="number" class="form-control" id="prod-price" placeholder="0 if negotiable" required>
            </div>
            <div class="form-group">
                <label>WhatsApp Contact Number</label>
                <input type="text" class="form-control" id="prod-whatsapp" placeholder="09018363715" required>
            </div>
            <div class="form-group">
                <label>Upload Photo/Video</label>
                <input type="file" id="prod-img-file" class="form-control" accept="image/*,video/*">
            </div>
            <div class="form-group">
                <label>Description</label>
                <textarea class="form-control" id="prod-desc" rows="2" placeholder="Details..."></textarea>
            </div>
            <button type="submit" class="btn-submit">Publish Listing</button>
        </form>
    </div>
</div>

<!-- EDIT PROFILE MODAL -->
<div id="edit-profile-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:440px;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Edit Profile Photos &amp; Bio</h3>
            <button onclick="closeEditProfileModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <form onsubmit="handleProfileUpdateSubmit(event)">
            <div class="form-group">
                <label>Profile Avatar Picture</label>
                <input type="file" id="edit-avatar-file" class="form-control" accept="image/*">
            </div>
            <div class="form-group">
                <label>Cover Banner Image</label>
                <input type="file" id="edit-cover-file" class="form-control" accept="image/*">
            </div>
            <div class="form-group">
                <label>Bio</label>
                <textarea id="edit-bio-text" class="form-control" rows="2" placeholder="Short intro about yourself..."></textarea>
            </div>
            <button type="submit" class="btn-submit">Save Profile Changes</button>
        </form>
    </div>
</div>

<!-- DATING SETTINGS MODAL -->
<div id="dating-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:440px;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">❤️ Dating Profile Settings</h3>
            <button onclick="closeDatingModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <form onsubmit="handleDatingProfileSubmit(event)">
            <div class="form-group">
                <label>Age</label>
                <input type="number" id="dt-age" class="form-control" value="24" required>
            </div>
            <div class="form-group">
                <label>Gender</label>
                <select id="dt-gender" class="form-control">
                    <option value="Female">Female</option>
                    <option value="Male">Male</option>
                </select>
            </div>
            <div class="form-group">
                <label>Looking For</label>
                <select id="dt-intent" class="form-control">
                    <option value="Dating & Relationship">Dating &amp; Relationship</option>
                    <option value="Marriage">Marriage</option>
                    <option value="Networking & Friends">Networking &amp; Friends</option>
                </select>
            </div>
            <div class="form-group">
                <label>Occupation / Profession</label>
                <input type="text" id="dt-occupation" class="form-control" placeholder="Entrepreneur, Fashion Designer...">
            </div>
            <div class="form-group">
                <label>Short Bio</label>
                <textarea id="dt-bio" class="form-control" rows="2" placeholder="Tell singles in Ijebu a little about yourself..."></textarea>
            </div>
            <div class="form-group" style="flex-direction:row;align-items:center;gap:8px;">
                <input type="checkbox" id="dt-active" checked style="width:auto;">
                <label for="dt-active" style="margin:0;">Show profile on Match Feed</label>
            </div>
            <button type="submit" class="btn-submit" style="background:#4f46e5;">Save Profile</button>
        </form>
    </div>
</div>

<!-- CASHOUT MODAL -->
<div id="cashout-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:420px;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Bank Cashout</h3>
            <button onclick="closeCashoutModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <form onsubmit="handlePayoutRequest(event)">
            <div class="form-group">
                <label>Amount to Cashout (₦)</label>
                <input type="number" class="form-control" id="payout-amount" placeholder="Min 1000" required>
            </div>
            <div class="form-group">
                <label>Bank Name</label>
                <input type="text" class="form-control" id="payout-bank" placeholder="e.g. GTBank / OPay" required>
            </div>
            <div class="form-group">
                <label>Account Number</label>
                <input type="text" class="form-control" id="payout-acc-num" placeholder="10 Digits" required>
            </div>
            <div class="form-group">
                <label>Account Name</label>
                <input type="text" class="form-control" id="payout-acc-name" placeholder="Matching account name" required>
            </div>
            <button type="submit" class="btn-submit">Submit Cashout Request</button>
        </form>
    </div>
</div>

<footer>
    Designed &amp; Developed by <strong>Willys Media World</strong><br>
    <i class="fa-solid fa-envelope"></i> <a href="mailto:{{ contact_email }}">{{ contact_email }}</a><br><br>
    &copy; 2026 Ijebu Connect. All Rights Reserved.
</footer>

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
    document.querySelectorAll('.nav-pill').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
    const pill = document.querySelector(`.nav-pill[data-nav="${target}"]`);
    if(pill) pill.classList.add('active');
    const view = document.getElementById(`view-${target}`);
    if(view) view.classList.add('active');

    if(target === 'feed') loadPosts('Social', 'feed-posts-container');
    if(target === 'market') loadCategoryListings('Market', 'products-container');
    if(target === 'beauty') loadCategoryListings('Beauty', 'beauty-container');
    if(target === 'jobs') loadCategoryListings('Jobs', 'jobs-container');
    if(target === 'events') loadPosts('Event', 'events-container');
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
            if(currentUser.user_type === 'Admin') {
                document.getElementById('admin-pill').style.display = 'flex';
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
        box.innerHTML = `
            <button class="header-user-pill" onclick="openProfile('${currentUser.username}')">@${currentUser.username}</button>
            <button class="header-logout-btn" onclick="handleLogout()">Logout</button>
        `;
    } else {
        box.innerHTML = `<a href="/auth" class="btn-header-login">Sign In</a>`;
    }
}

async function handleLogout() {
    await fetch('/api/auth/logout', {method:'POST'});
    currentUser = null;
    document.getElementById('admin-pill').style.display = 'none';
    renderHeaderAuth();
    showToast('Logged out.');
    switchNav('feed');
}

// FILE UPLOAD HELPER
async function uploadSelectedFile(fileInput) {
    if(!fileInput || !fileInput.files[0]) return {url:'', is_video: false};
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    const res = await fetch('/api/upload', {method:'POST', body: formData});
    const data = await res.json();
    return data.success ? {url: data.url, is_video: data.is_video} : {url:'', is_video: false};
}

// POSTS / SOCIAL / EVENTS
async function handlePostSubmit(e, postType) {
    e.preventDefault();
    if(!currentUser) return window.location.href = '/auth';
    
    const contentEl = postType === 'Event' ? document.getElementById('event-content') : document.getElementById('post-content');
    const content = contentEl.value.trim();
    if(!content) return showToast('Please enter text content', 'error');

    let imageUrl = '';
    let videoUrl = '';
    const fileInput = document.getElementById('post-file-input');
    if(fileInput && fileInput.files[0]) {
        const upload = await uploadSelectedFile(fileInput);
        if(upload.is_video) videoUrl = upload.url;
        else imageUrl = upload.url;
    }

    const res = await fetch('/api/posts', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({content, image_url: imageUrl, video_url: videoUrl, post_type: postType})
    });
    const data = await res.json();
    if(data.success) {
        showToast(data.message);
        contentEl.value = '';
        if(fileInput) fileInput.value = '';
        loadPosts(postType, postType === 'Event' ? 'events-container' : 'feed-posts-container');
    } else showToast(data.message, 'error');
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
    const badge = p.user_type === 'CPN Partner' ? '<span class="badge badge-partner">CPN Partner</span>' : (p.user_type === 'Admin' ? '<span class="badge badge-admin">Admin</span>' : '');
    
    let mediaHtml = '';
    if(p.video_url) {
        mediaHtml = `<video src="${p.video_url}" controls class="post-media"></video>`;
    } else if(p.image_url) {
        mediaHtml = `<img src="${p.image_url}" class="post-media">`;
    }

    const avatarHtml = p.avatar_url ? `<img src="${p.avatar_url}">` : p.full_name.charAt(0).toUpperCase();

    return `
        <div class="feed-post">
            <div class="post-header">
                <div class="avatar" style="background:var(--navy-blue);" onclick="openProfile('${p.username}')">${avatarHtml}</div>
                <div>
                    <div class="post-author"><span class="clickable-user" onclick="openProfile('${p.username}')">${p.full_name}</span> ${badge}</div>
                    <div class="post-meta">@${p.username} • ${new Date(p.created_at).toLocaleDateString()}</div>
                </div>
            </div>
            <div class="post-content">${p.content}</div>
            ${mediaHtml}
            <div class="post-actions">
                <button class="post-action ${p.liked_by_me ? 'liked':''}" onclick="toggleLike(${p.id})">❤️ ${p.likes_count}</button>
            </div>
        </div>
    `;
}

async function toggleLike(pid) {
    if(!currentUser) return window.location.href = '/auth';
    await fetch(`/api/posts/${pid}/like`, {method:'POST'});
    loadPosts('Social', 'feed-posts-container');
}

// MULTI-PILLAR LISTINGS
async function loadCategoryListings(listingType, containerId) {
    const q = (listingType === 'Market') ? document.getElementById('market-search').value.trim() : '';
    const res = await fetch(`/api/products?type=${listingType}&q=${encodeURIComponent(q)}`);
    const items = await res.json();
    const container = document.getElementById(containerId);

    if(!items.length) {
        container.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:1rem;">No listings found.</div>`;
        return;
    }

    container.innerHTML = items.map(p => {
        let mediaBox = '<i class="fa-solid fa-store"></i>';
        if(p.video_url) mediaBox = `<video src="${p.video_url}" controls></video>`;
        else if(p.image_url) mediaBox = `<img src="${p.image_url}">`;

        return `
            <div class="product-card">
                <div class="product-img-box">${mediaBox}</div>
                <div style="flex:1;">
                    <h4 style="font-weight:800;color:var(--navy-blue);font-size:0.95rem;">${p.title}</h4>
                    <div style="font-weight:800;color:var(--emerald-green);font-size:0.9rem;margin:2px 0;">${p.price > 0 ? formatNaira(p.price) : 'Negotiable'}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted);">${p.category} • By <span class="clickable-user" onclick="openProfile('${p.seller_username}')">@${p.seller_username}</span></div>
                </div>
                <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" class="btn-whatsapp"><i class="fa-brands fa-whatsapp"></i> Chat</a>
            </div>
        `;
    }).join('');
}

function startSellItem(type = 'Market') {
    if(!currentUser) return window.location.href = '/auth';
    if(currentUser.user_type === 'Resident') {
        document.getElementById('cpn-upgrade-modal').style.display = 'flex';
    } else {
        document.getElementById('prod-type').value = type;
        document.getElementById('modal-sell-title').innerText = `List on Ijebu ${type}`;
        document.getElementById('sell-modal').style.display = 'flex';
    }
}
function closeCPNModal() { document.getElementById('cpn-upgrade-modal').style.display = 'none'; }
function closeSellModal() { document.getElementById('sell-modal').style.display = 'none'; }

async function handleClaimBankTransfer(e) {
    e.preventDefault();
    const note = document.getElementById('cpn-ref-note').value;
    const res = await fetch('/api/cpn/claim-bank-transfer', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reference_note: note})
    });
    const data = await res.json();
    if(data.success) {
        showToast(data.message);
        closeCPNModal();
    } else showToast(data.message, 'error');
}

async function handleProductSubmit(e) {
    e.preventDefault();
    const type = document.getElementById('prod-type').value;
    const fileInput = document.getElementById('prod-img-file');
    let uploadedImg = '';
    let uploadedVid = '';
    
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
    if(data.success) {
        showToast(data.message);
        closeSellModal();
        if(type === 'Market') loadCategoryListings('Market', 'products-container');
        if(type === 'Beauty') loadCategoryListings('Beauty', 'beauty-container');
        if(type === 'Jobs') loadCategoryListings('Jobs', 'jobs-container');
    } else showToast(data.message, 'error');
}

// DATING & MATCHING
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
    if(data.success) {
        showToast(data.message);
        closeDatingModal();
        loadDatingMatches();
    } else showToast(data.message, 'error');
}

async function loadDatingMatches() {
    const res = await fetch('/api/dating/matches');
    const matches = await res.json();
    const container = document.getElementById('dating-matches-container');
    if(!matches.length) {
        container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No active singles on match feed yet. Click "Set Up My Dating Profile" to be the first!</div>`;
        return;
    }
    container.innerHTML = matches.map(m => {
        const avatarHtml = m.avatar_url ? `<img src="${m.avatar_url}">` : m.full_name.charAt(0).toUpperCase();
        return `
            <div style="background:#fff;border:1.5px solid var(--border-light);border-radius:16px;padding:1rem;margin-bottom:0.85rem;display:flex;gap:12px;align-items:center;">
                <div class="avatar" style="background:#7c3aed;width:56px;height:56px;font-size:1.3rem;">${avatarHtml}</div>
                <div style="flex:1;">
                    <h4 style="font-weight:800;color:var(--navy-blue);">${m.full_name}, ${m.age}</h4>
                    <div style="font-size:0.78rem;color:var(--emerald-green);font-weight:700;">${m.relationship_intent} • ${m.gender}</div>
                    <div style="font-size:0.82rem;color:var(--text-muted);margin-top:2px;">"${m.bio || 'Living in Ijebu'}"</div>
                </div>
                <button onclick="messageUser('${m.username}')" style="background:#4f46e5;color:#fff;border:none;padding:8px 12px;border-radius:10px;font-weight:700;font-size:0.78rem;cursor:pointer;">Say Hi 👋</button>
            </div>
        `;
    }).join('');
}

// FACEBOOK-STYLE PROFILE
function openEditProfileModal() { document.getElementById('edit-profile-modal').style.display = 'flex'; }
function closeEditProfileModal() { document.getElementById('edit-profile-modal').style.display = 'none'; }

async function handleProfileUpdateSubmit(e) {
    e.preventDefault();
    const avatarInput = document.getElementById('edit-avatar-file');
    const coverInput = document.getElementById('edit-cover-file');
    
    let avatarUrl = '';
    let coverUrl = '';

    if(avatarInput && avatarInput.files[0]) {
        const up = await uploadSelectedFile(avatarInput);
        avatarUrl = up.url;
    }
    if(coverInput && coverInput.files[0]) {
        const up = await uploadSelectedFile(coverInput);
        coverUrl = up.url;
    }

    const res = await fetch('/api/users/profile/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            avatar_url: avatarUrl,
            cover_url: coverUrl,
            bio: document.getElementById('edit-bio-text').value
        })
    });
    const data = await res.json();
    if(data.success) {
        showToast(data.message);
        closeEditProfileModal();
        await checkSession();
        openProfile(currentUser.username);
    } else showToast(data.message, 'error');
}

async function openProfile(username) {
    const res = await fetch(`/api/users/${encodeURIComponent(username)}`);
    const data = await res.json();
    if(!data.success) return showToast(data.message, 'error');

    const u = data.user;
    const container = document.getElementById('profile-wall-container');
    const isSelf = currentUser && currentUser.id === u.id;
    const isPartner = u.user_type === 'CPN Partner' || u.user_type === 'Admin';

    let cpnWalletBlock = '';
    if(isSelf && isPartner) {
        cpnWalletBlock = `
            <div class="cpn-wallet-card">
                <div class="cpn-row"><span>CPN Wallet Balance:</span><span class="val-gold">${formatNaira(u.wallet_balance)}</span></div>
                <div class="cpn-row"><span>CPN Code:</span><strong style="background:rgba(255,255,255,0.15);padding:3px 8px;border-radius:6px;">${u.referral_code}</strong></div>
                <div class="cpn-row"><span>Direct Recruits:</span><strong style="color:#4ade80;">${u.recruits_count} Partners</strong></div>
                <div class="cpn-row" style="margin-bottom:12px;"><span>Partner Link:</span><button onclick="copyRefLink('${u.referral_code}')" style="background:#d97706;color:#fff;border:none;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:700;cursor:pointer;">Copy Link</button></div>
                <button onclick="openCashoutModal()" style="background:#059669;color:#fff;border:none;padding:10px;border-radius:8px;width:100%;font-weight:800;cursor:pointer;">Request Bank Cashout</button>
            </div>
        `;
    }

    const messageBtn = (!isSelf && currentUser) ? `
        <button onclick="messageUser('${u.username}')" style="background:var(--navy-blue);color:#fff;border:none;padding:10px;border-radius:10px;width:100%;font-weight:800;cursor:pointer;margin-bottom:1rem;">
            💬 Message ${u.full_name.split(' ')[0]}
        </button>
    ` : '';

    const editProfileBtn = isSelf ? `
        <button onclick="openEditProfileModal()" style="background:var(--navy-blue);color:#fff;border:none;padding:8px 14px;border-radius:10px;font-weight:700;font-size:0.8rem;cursor:pointer;margin-top:8px;">
            ✏️ Edit FB Cover &amp; Avatar
        </button>
    ` : '';

    const coverStyle = u.cover_url ? `style="background-image:url('${u.cover_url}');"` : '';
    const avatarInner = u.avatar_url ? `<img src="${u.avatar_url}">` : u.full_name.charAt(0).toUpperCase();

    container.innerHTML = `
        <div class="fb-profile-card">
            <div class="fb-cover-banner" ${coverStyle}>
                <div class="fb-avatar-wrap">${avatarInner}</div>
            </div>
            <div class="fb-profile-body">
                <div class="fb-profile-name">${u.full_name} <span class="badge ${isPartner ? 'badge-partner':'badge-admin'}">${u.user_type}</span></div>
                <div style="font-size:0.8rem;color:var(--text-muted);font-weight:700;">@${u.username}</div>
                <div class="fb-profile-bio">${u.bio || 'Resident of Ijebu'}</div>
                ${editProfileBtn}
            </div>
        </div>
        ${messageBtn}
        ${cpnWalletBlock}
    `;

    document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
    document.getElementById('view-profile').classList.add('active');
}

function copyRefLink(code) {
    navigator.clipboard.writeText(`${window.location.origin}/auth?ref=${code}`);
    showToast('Partner referral link copied!');
}

function openCashoutModal() { document.getElementById('cashout-modal').style.display = 'flex'; }
function closeCashoutModal() { document.getElementById('cashout-modal').style.display = 'none'; }

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
    if(data.success) {
        showToast(data.message);
        closeCashoutModal();
        await checkSession();
        openProfile(currentUser.username);
    } else showToast(data.message, 'error');
}

// CHAT
async function refreshUnread() {
    if(!currentUser) { document.getElementById('chat-badge').style.display='none'; return; }
    try {
        const res = await fetch('/api/chat/unread');
        const data = await res.json();
        const badge = document.getElementById('chat-badge');
        if(data.count > 0) { badge.innerText = data.count > 99 ? '99+' : data.count; badge.style.display='block'; }
        else badge.style.display='none';
    } catch(e){}
}

async function loadChatPartners() {
    const res = await fetch('/api/chat/partners');
    const data = await res.json();
    const c = document.getElementById('chat-partners-container');
    if(!data.success || !data.partners.length) {
        c.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No conversations yet.<br><small>Visit a member's profile and tap "Message" to start.</small></div>`;
        return;
    }
    c.innerHTML = data.partners.map(p => {
        const avatarHtml = p.user.avatar_url ? `<img src="${p.user.avatar_url}">` : p.user.full_name.charAt(0).toUpperCase();
        return `
            <div class="chat-partner-row" onclick="openChatThread('${p.user.username}')">
                <div class="avatar" style="background:var(--navy-blue);">${avatarHtml}</div>
                <div style="flex:1;min-width:0;">
                    <div style="font-weight:800;color:var(--navy-blue);">${p.user.full_name}</div>
                    <div style="font-size:0.78rem;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.last_from_me ? 'You: ' : ''}${p.last_message}</div>
                </div>
                ${p.unread ? `<span style="background:#ef4444;color:#fff;font-size:0.7rem;font-weight:800;padding:2px 6px;border-radius:10px;">${p.unread}</span>` : ''}
            </div>
        `;
    }).join('');
}

async function openChatThread(username) {
    if(!currentUser) return window.location.href = '/auth';
    currentChatUser = username;
    document.getElementById('chat-list-wrap').style.display = 'none';
    document.getElementById('chat-thread-wrap').style.display = 'block';

    const res = await fetch(`/api/chat/${encodeURIComponent(username)}`);
    const data = await res.json();
    if(!data.success) return showToast(data.message, 'error');

    const avatarHtml = data.other.avatar_url ? `<img src="${data.other.avatar_url}">` : data.other.full_name.charAt(0).toUpperCase();

    document.getElementById('chat-thread-header').innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;">
            <div class="avatar" style="background:var(--navy-blue);">${avatarHtml}</div>
            <div>
                <div style="font-weight:800;color:var(--navy-blue);">${data.other.full_name}</div>
                <div style="font-size:0.72rem;color:var(--text-muted);">@${data.other.username}</div>
            </div>
        </div>
        <button onclick="toggleBlock('${data.other.username}')" style="background:none;border:1.5px solid #ef4444;color:#ef4444;padding:6px 12px;border-radius:8px;font-size:0.72rem;font-weight:700;cursor:pointer;">Block</button>
    `;

    const m = document.getElementById('chat-messages');
    if(!data.messages.length) {
        m.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:2rem 0;">No messages yet. Say hi 👋</div>`;
    } else {
        m.innerHTML = data.messages.map(msg => `
            <div class="chat-bubble ${msg.sender_id === data.me_id ? 'me' : 'them'}">
                ${msg.content}
                <div class="chat-time">${new Date(msg.created_at).toLocaleString()}</div>
            </div>
        `).join('');
        m.scrollTop = m.scrollHeight;
    }
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
    const res = await fetch(`/api/chat/${encodeURIComponent(currentChatUser)}`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({content})
    });
    const data = await res.json();
    if(data.success) {
        openChatThread(currentChatUser);
    } else showToast(data.message, 'error');
}

async function toggleBlock(username) {
    if(!confirm('Block this user? You will not see their messages.')) return;
    const res = await fetch(`/api/chat/block/${encodeURIComponent(username)}`, {method:'POST'});
    const data = await res.json();
    showToast(data.message);
    if(data.blocked) closeChatThread();
}

async function messageUser(username) {
    if(!currentUser) return window.location.href = '/auth';
    switchNav('chat');
    openChatThread(username);
}

window.onload = function() {
    checkSession();
    loadPosts('Social', 'feed-posts-container');
    setInterval(refreshUnread, 15000);
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
    :root {
        --navy-blue: #0b1e36;
        --emerald-green: #059669;
        --border-light: #cbd5e1;
    }
    * { box-sizing: border-box; margin:0; padding:0; font-family:'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color:transparent; }
    body { background: #f8fafc; color: #0f172a; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 1rem; }
    .auth-card { background: #fff; border: 1.5px solid var(--border-light); border-radius: 18px; padding: 1.75rem; max-width: 420px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.05); text-align: center; }
    .auth-logo-img { height: 70px; width: auto; object-fit: contain; margin-bottom: 8px; }
    .brand { font-size: 1.25rem; font-weight: 800; color: var(--navy-blue); margin-bottom: 2px; }
    .brand span { color: var(--emerald-green); }
    .slogan { font-size: 0.78rem; font-weight: 700; color: #64748b; font-style: italic; margin-bottom: 1.25rem; }
    .auth-tabs { display: flex; gap: 6px; margin-bottom: 1.25rem; background: #f1f5f9; padding: 4px; border-radius: 12px; }
    .auth-tab { flex: 1; padding: 9px; border-radius: 8px; border: none; background: transparent; font-weight: 700; font-size: 0.85rem; color: #64748b; cursor: pointer; }
    .auth-tab.active { background: #fff; color: var(--navy-blue); box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
    .form-group { display: flex; flex-direction: column; gap: 5px; margin-bottom: 0.9rem; text-align: left; }
    .form-group label { font-size: 0.82rem; font-weight: 700; color: #0f172a; }
    .form-control { padding: 11px 12px; border-radius: 10px; border: 1.5px solid var(--border-light); font-size: 0.9rem; outline: none; width: 100%; background: #fff; font-family: inherit; }
    .btn-submit { background: var(--emerald-green); color: #fff; border: none; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 0.9rem; cursor: pointer; width: 100%; margin-top: 6px; }
</style>
</head>
<body>
<div class="auth-card">
    <img src="/static/logo.png" alt="Ijebu Connect Logo" class="auth-logo-img" onerror="this.style.display='none'">
    <div class="brand">IJEBU <span>CONNECT</span></div>
    <div class="slogan">Connect. Discover. Trade. Belong.</div>

    <div class="auth-tabs">
        <button class="auth-tab active" id="tab-btn-login" onclick="toggleAuth('login')">Sign In</button>
        <button class="auth-tab" id="tab-btn-register" onclick="toggleAuth('register')">Register Free</button>
    </div>

    <form id="form-login" onsubmit="handleLogin(event)">
        <div class="form-group">
            <label>Username or Phone</label>
            <input type="text" id="login-uname" class="form-control" required>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" id="login-pword" class="form-control" required>
        </div>
        <button type="submit" class="btn-submit" style="background:#0b1e36;">Sign In</button>
    </form>

    <form id="form-register" style="display:none;" onsubmit="handleRegister(event)">
        <div class="form-group">
            <label>Full Name</label>
            <input type="text" id="reg-name" class="form-control" placeholder="Afeez Adebayo" required>
        </div>
        <div class="form-group">
            <label>Phone Number</label>
            <input type="tel" id="reg-phone" class="form-control" placeholder="08012345678" required>
        </div>
        <div class="form-group">
            <label>Username</label>
            <input type="text" id="reg-uname" class="form-control" placeholder="afeez123" required>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" id="reg-pword" class="form-control" required>
        </div>
        <div class="form-group">
            <label>Sponsor Referral Code (Optional)</label>
            <input type="text" id="reg-ref" class="form-control" placeholder="CPN00001">
        </div>
        <button type="submit" class="btn-submit">Create Free Account</button>
    </form>
</div>

<script>
function toggleAuth(mode) {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    if(mode === 'login') {
        document.getElementById('tab-btn-login').classList.add('active');
        document.getElementById('form-login').style.display = 'block';
        document.getElementById('form-register').style.display = 'none';
    } else {
        document.getElementById('tab-btn-register').classList.add('active');
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
    if(data.success) {
        alert(data.message);
        toggleAuth('login');
    } else alert(data.message);
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
    body { font-family:'Plus Jakarta Sans', sans-serif; background:#f8fafc; color:#0f172a; padding:1.5rem; max-width:920px; margin:0 auto; }
    .admin-header { display:flex; align-items:center; gap:12px; margin-bottom:1.25rem; }
    .admin-logo { height:50px; width:auto; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:1rem; margin-bottom:1.5rem; }
    .card { background:#fff; border:1.5px solid #cbd5e1; border-radius:14px; padding:1.25rem; }
    .val { font-size:1.5rem; font-weight:800; color:#059669; }
    .lbl { font-size:0.75rem; color:#64748b; font-weight:700; text-transform:uppercase; }
    
    .admin-tabs { display:flex; gap:8px; margin-bottom:1rem; border-bottom:2px solid #cbd5e1; padding-bottom:8px; }
    .admin-tab { padding:8px 16px; border-radius:8px; border:none; background:#fff; font-weight:700; cursor:pointer; color:#64748b; }
    .admin-tab.active { background:#0b1e36; color:#fff; }

    .tab-sec { display:none; } .tab-sec.active { display:block; }
    table { width:100%; border-collapse:collapse; background:#fff; border-radius:14px; overflow:hidden; border:1.5px solid #cbd5e1; font-size:0.85rem; margin-top:0.5rem; }
    th, td { padding:10px 12px; text-align:left; border-bottom:1px solid #cbd5e1; }
    th { background:#0b1e36; color:#fff; }
    .btn-act { padding:5px 12px; border-radius:6px; border:none; color:#fff; font-weight:700; cursor:pointer; font-size:0.75rem; }
    .btn-app { background:#059669; } .btn-rej { background:#ef4444; } .btn-del { background:#dc2626; }
</style>
</head>
<body>
<div class="admin-header">
    <img src="/static/logo.png" alt="Logo" class="admin-logo" onerror="this.style.display='none'">
    <div>
        <h1 style="color:#0b1e36;font-size:1.4rem;line-height:1;">⚙️ Admin Control Panel</h1>
        <small style="color:#64748b;font-weight:700;">Ijebu Connect System Management</small>
    </div>
</div>
<a href="/" style="display:inline-block;margin-bottom:1rem;color:#0b1e36;font-weight:700;text-decoration:none;">← Back to Main Platform</a>

<div class="grid">
    <div class="card"><div class="val" id="st-users">0</div><div class="lbl">Total Members</div></div>
    <div class="card"><div class="val" id="st-partners">0</div><div class="lbl">CPN Partners</div></div>
    <div class="card"><div class="val" id="st-pending">0</div><div class="lbl">Pending Partner Upgrades</div></div>
    <div class="card"><div class="val" id="st-wallets">₦0.00</div><div class="lbl">Partner Balances</div></div>
</div>

<div class="admin-tabs">
    <button class="admin-tab active" onclick="switchAdminTab('partners')">Partner Payment Claims</button>
    <button class="admin-tab" onclick="switchAdminTab('members')">Manage Members</button>
    <button class="admin-tab" onclick="switchAdminTab('payouts')">Bank Cashouts</button>
</div>

<!-- TAB 1: PARTNER APPROVALS -->
<div id="adm-partners" class="tab-sec active">
    <h3>Pending CPN Partner Upgrade Claims (₦2,000)</h3>
    <table>
        <thead>
            <tr><th>Member</th><th>Amount</th><th>Transfer Reference Note</th><th>Action</th></tr>
        </thead>
        <tbody id="partner-reqs-body"></tbody>
    </table>
</div>

<!-- TAB 2: MEMBERS MANAGEMENT -->
<div id="adm-members" class="tab-sec">
    <h3>All Platform Members</h3>
    <table>
        <thead>
            <tr><th>Full Name</th><th>Username</th><th>Phone</th><th>User Type</th><th>Action</th></tr>
        </thead>
        <tbody id="members-body"></tbody>
    </table>
</div>

<!-- TAB 3: BANK CASHOUTS -->
<div id="adm-payouts" class="tab-sec">
    <h3>Member Cashout Requests</h3>
    <table>
        <thead>
            <tr><th>User</th><th>Amount</th><th>Bank Details</th><th>Action</th></tr>
        </thead>
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
    loadPayouts();
}

async function loadPartnerRequests() {
    const res = await fetch('/api/admin/partner-requests');
    const reqs = await res.json();
    const body = document.getElementById('partner-reqs-body');
    if(!reqs.length) { body.innerHTML = `<tr><td colspan="4" style="text-align:center;color:#64748b;">No partner claims.</td></tr>`; return; }
    body.innerHTML = reqs.map(r => `
        <tr>
            <td><b>${r.full_name}</b><br><small>@${r.username} (${r.phone})</small></td>
            <td><b>₦${r.amount.toLocaleString()}</b></td>
            <td>${r.reference_note}</td>
            <td>
                ${r.status === 'pending' ? `
                    <button class="btn-act btn-app" onclick="actPartnerReq(${r.id}, 'approve')">Approve CPN Partner</button>
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
            <td>
                ${u.user_type !== 'Admin' ? `<button class="btn-act btn-del" onclick="deleteMember(${u.id})">Delete Member</button>` : 'System Admin'}
            </td>
        </tr>
    `).join('');
}

async function deleteMember(uid) {
    if(!confirm('Are you sure you want to completely remove this member?')) return;
    const res = await fetch(`/api/admin/users?user_id=${uid}`, {method:'DELETE'});
    const data = await res.json();
    alert(data.message);
    loadAdmin();
}

async function loadPayouts() {
    const res = await fetch('/api/admin/payouts');
    const payouts = await res.json();
    const body = document.getElementById('payouts-body');
    if(!payouts.length) { body.innerHTML = `<tr><td colspan="4" style="text-align:center;color:#64748b;">No cashout requests.</td></tr>`; return; }
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

# =============================================================================
# ROUTE HANDLERS
# =============================================================================

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
    