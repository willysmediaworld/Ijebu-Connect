import os
import sqlite3
import random
import string
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask, render_template_string, request, jsonify,
    g, session, redirect
)
from werkzeug.security import generate_password_hash, check_password_hash

# =============================================================================
# CONFIGURATION
# =============================================================================

DATABASE_URL = os.environ.get('DATABASE_URL')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '').strip()
ALLOW_TEST_PAYMENTS = os.environ.get('ALLOW_TEST_PAYMENTS', 'True').lower() == 'true'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'ijebu_connect_secret_key_2026_secured'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024


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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS products (
                id {pk_type},
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT,
                image_url TEXT DEFAULT '',
                location TEXT DEFAULT 'Ijebu-Imusin',
                whatsapp_number TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS posts (
                id {pk_type},
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                post_type TEXT DEFAULT 'Community News',
                image_url TEXT DEFAULT '',
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
                parent_id INTEGER DEFAULT NULL,
                content TEXT NOT NULL,
                likes_count INTEGER DEFAULT 0,
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

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_uname ON users(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ref   ON users(referral_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_user  ON posts(user_id)")

        db.commit()

        # Admin Seed
        admin_username = os.environ.get('ADMIN_SEED_USERNAME', 'imusinconnect')
        admin_password = os.environ.get('ADMIN_SEED_PASSWORD', 'imusin1972williams')
        admin_phone    = os.environ.get('ADMIN_SEED_PHONE',    '09018363715')
        admin_name     = os.environ.get('ADMIN_SEED_NAME',     'Willys Media Admin')
        admin_ref      = os.environ.get('ADMIN_SEED_REF',      'CPN00001')

        cursor.execute(
            f"SELECT id FROM users WHERE username = {p} OR phone = {p} OR referral_code = {p}",
            (admin_username, admin_phone, admin_ref)
        )
        if not cursor.fetchone():
            admin_pass_hash = generate_password_hash(admin_password)
            try:
                cursor.execute(f'''
                    INSERT INTO users (full_name, phone, username, password_hash, user_type, referral_code)
                    VALUES ({p}, {p}, {p}, {p}, 'Admin', {p})
                ''', (admin_name, admin_phone, admin_username, admin_pass_hash, admin_ref))
                db.commit()
            except Exception:
                db.rollback()

with app.app_context():
    init_db()


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

    # Tier-1 Commission (10%)
    cursor.execute(f"SELECT id, full_name, referred_by FROM users WHERE referral_code = {p}", (buyer['referred_by'],))
    t1 = cursor.fetchone()
    if t1:
        bonus1 = upgrade_fee * 0.10
        cursor.execute(f"UPDATE users SET wallet_balance = wallet_balance + {p} WHERE id = {p}", (bonus1, t1['id']))
        cursor.execute(f'''INSERT INTO transactions (user_id, amount, tx_type, description)
                            VALUES ({p}, {p}, 'Tier-1 CPN Commission', {p})''',
                        (t1['id'], bonus1, f"10% CPN Reward from {buyer['full_name']}"))

        # Tier-2 Commission (5%)
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
            f'''SELECT id, full_name, username, user_type, referral_code, wallet_balance, is_verified_merchant
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

@app.route('/api/cpn/upgrade', methods=['POST'])
def upgrade_to_cpn():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    # PAYSTACK MODE
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

    # TEST / SIMULATED MODE
    if ALLOW_TEST_PAYMENTS:
        cursor.execute(f"UPDATE users SET user_type = 'CPN Partner', is_verified_merchant = 1 WHERE id = {p}", (uid,))
        db.commit()
        process_cpn_commission(uid, upgrade_fee=2000.0)
        session['user_type'] = 'CPN Partner'
        return jsonify({'success': True, 'paystack': False, 'message': 'Congratulations! You are now an official CPN Partner!'})

    return jsonify({'success': False, 'message': 'Payment gateway unavailable.'}), 400


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
# MARKETPLACE
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
                'message': 'You must be a CPN Partner to list items for sale. Please upgrade first.',
                'requires_upgrade': True
            }), 403

        data = request.json or {}
        title = data.get('title', '').strip()
        category = data.get('category', 'General')
        try:
            price = float(data.get('price', 0))
        except (ValueError, TypeError):
            price = 0.0
        description = data.get('description', '').strip()
        whatsapp = data.get('whatsapp_number', '').strip()

        if not title or price <= 0 or not whatsapp:
            return jsonify({'success': False, 'message': 'Title, price, and WhatsApp contact required.'}), 400

        cursor.execute(
            f'''INSERT INTO products (user_id, title, category, price, description, whatsapp_number)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})''',
            (session['user_id'], title, category, price, description, whatsapp)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Product published on Ijebu Market Hub!'})

    q = request.args.get('q', '').strip().lower()
    sql = '''
        SELECT p.*, u.full_name AS seller_name, u.username AS seller_username,
               u.is_verified_merchant, u.user_type
        FROM products p
        JOIN users u ON p.user_id = u.id
        WHERE p.status = 'active'
    '''
    params = []
    if q:
        sql += f" AND (LOWER(p.title) LIKE {p} OR LOWER(p.description) LIKE {p})"
        params.extend([f"%{q}%", f"%{q}%"])

    sql += ' ORDER BY p.id DESC'
    cursor.execute(sql, tuple(params))

    result = []
    for r in cursor.fetchall():
        d = dict(r)
        d['price'] = float(d.get('price') or 0)
        result.append(d)
    return jsonify(result)


# =============================================================================
# SOCIAL FEED
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

        if not content and not image_url:
            return jsonify({'success': False, 'message': 'Write something or attach an image.'}), 400

        cursor.execute(
            f"INSERT INTO posts (user_id, content, image_url) VALUES ({p}, {p}, {p})",
            (session['user_id'], content, image_url)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Published to community feed!'})

    current_uid = session.get('user_id') or 0
    cursor.execute(
        f'''
        SELECT p.id, p.user_id, p.content, p.post_type, p.image_url, p.created_at,
               u.full_name, u.username, u.user_type, u.is_verified_merchant,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
               CASE WHEN EXISTS (
                   SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = {p}
               ) THEN 1 ELSE 0 END AS liked_by_me
        FROM posts p
        JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC LIMIT 60
        ''',
        (current_uid,)
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


@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required.'}), 401

    db = get_db()
    cursor = db.cursor()
    p = query_param()
    uid = session['user_id']

    cursor.execute(f"SELECT user_id FROM posts WHERE id = {p}", (post_id,))
    post = cursor.fetchone()
    if not post:
        return jsonify({'success': False, 'message': 'Post not found.'}), 404

    cursor.execute(f"SELECT user_type FROM users WHERE id = {p}", (uid,))
    me = cursor.fetchone()
    if post['user_id'] != uid and me['user_type'] != 'Admin':
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 403

    cursor.execute(f"DELETE FROM comments WHERE post_id = {p}", (post_id,))
    cursor.execute(f"DELETE FROM post_likes WHERE post_id = {p}", (post_id,))
    cursor.execute(f"DELETE FROM posts WHERE id = {p}", (post_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Post deleted.'})


@app.route('/api/posts/<int:post_id>/comments', methods=['GET', 'POST'])
def post_comments(post_id):
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    if request.method == 'POST':
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Login required.'}), 401

        data = request.json or {}
        content = (data.get('content') or '').strip()
        parent_id = data.get('parent_id')

        if not content:
            return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400

        cursor.execute(
            f"INSERT INTO comments (post_id, user_id, parent_id, content) VALUES ({p}, {p}, {p}, {p})",
            (post_id, session['user_id'], parent_id, content)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Comment posted!'})

    current_uid = session.get('user_id') or 0
    cursor.execute(
        f'''
        SELECT c.id, c.post_id, c.user_id, c.parent_id, c.content, c.created_at,
               u.full_name, u.username, u.user_type,
               (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes_count,
               CASE WHEN EXISTS (
                   SELECT 1 FROM comment_likes cl WHERE cl.comment_id = c.id AND cl.user_id = {p}
               ) THEN 1 ELSE 0 END AS liked_by_me
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = {p} ORDER BY c.id ASC
        ''',
        (current_uid, post_id)
    )
    return jsonify([dict(r) for r in cursor.fetchall()])


# =============================================================================
# PUBLIC MEMBER PROFILE & WALL
# =============================================================================

@app.route('/api/users/<username>', methods=['GET'])
def get_user_profile(username):
    db = get_db()
    cursor = db.cursor()
    p = query_param()

    cursor.execute(
        f'''SELECT id, full_name, username, user_type, referral_code,
                   wallet_balance, is_verified_merchant, created_at
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
        SELECT p.id, p.user_id, p.content, p.post_type, p.image_url, p.created_at,
               u.full_name, u.username, u.user_type, u.is_verified_merchant,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count,
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
# ADMIN API
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
    cursor.execute("SELECT COUNT(*) FROM products WHERE status = 'active'")
    total_products = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(wallet_balance), 0) FROM users")
    total_wallets = float(cursor.fetchone()[0] or 0)
    cursor.execute("SELECT COUNT(*) FROM payout_requests WHERE status = 'pending'")
    pending = cursor.fetchone()[0]

    return jsonify({
        'success': True,
        'total_users': total_users,
        'total_partners': total_partners,
        'total_products': total_products,
        'total_partner_wallets': total_wallets,
        'pending_payouts': pending
    })

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
<title>Ijebu-Imusin Connect Network</title>
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

header { background: #fff; padding: 0.85rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1.5px solid var(--border-light); position: sticky; top:0; z-index: 100; }
.brand-box { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.brand-title { font-size: 1.15rem; font-weight: 800; color: var(--navy-blue); }
.brand-title span { color: var(--emerald-green); }

.header-auth { display: flex; align-items: center; gap: 8px; }
.btn-header-login { background: var(--navy-blue); color: #fff; text-decoration: none; padding: 7px 16px; border-radius: 20px; font-weight: 700; font-size: 0.8rem; }
.header-user-pill { background: #f1f5f9; color: var(--navy-blue); padding: 6px 12px; border-radius: 20px; font-weight: 700; font-size: 0.8rem; cursor: pointer; border: none; }
.header-logout-btn { background: #ef4444; color: #fff; border: none; padding: 6px 12px; border-radius: 20px; font-weight: 700; font-size: 0.78rem; cursor: pointer; }

.top-nav-pills { display: flex; gap: 8px; padding: 0.85rem 1rem 0.2rem; max-width: 600px; margin: 0 auto; width: 100%; }
.nav-pill { padding: 10px 16px; border-radius: 20px; font-size: 0.85rem; font-weight: 700; background: #fff; border: 1.5px solid var(--border-light); color: var(--text-muted); cursor: pointer; flex: 1; text-align: center; }
.nav-pill.active { background: var(--navy-blue); color: #fff; border-color: var(--navy-blue); }

.app-container { max-width: 600px; margin: 0 auto; width: 100%; padding: 0.5rem 1rem 2rem; flex: 1; }
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

/* Posts Feed */
.feed-post { background: #fff; border: 1.5px solid var(--border-light); border-radius: 16px; padding: 1rem; margin-bottom: 0.85rem; }
.post-header { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 0.9rem; flex-shrink: 0; cursor: pointer; }
.post-author { font-size: 0.92rem; }
.post-meta { font-size: 0.72rem; color: var(--text-muted); }
.post-content { font-size: 0.92rem; line-height: 1.5; white-space: pre-wrap; margin-bottom: 8px; }
.post-actions { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid var(--border-light); }
.post-action { flex: 1; background: none; border: none; padding: 8px; border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; }
.post-action.liked { color: #ef4444; }

/* Product Cards */
.product-card { display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--border-light); padding-bottom: 12px; margin-bottom: 12px; }
.product-card:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.product-img-box { width: 64px; height: 64px; border-radius: 12px; background: #f1f5f9; display: flex; align-items: center; justify-content: center; font-size: 1.4rem; color: #0f172a; flex-shrink: 0; }
.btn-whatsapp { background: #25d366; color: #fff; border: none; padding: 8px 14px; border-radius: 10px; font-weight: 700; font-size: 0.8rem; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }

/* Profile Wall & CPN Wallet Box */
.profile-hero { background: var(--navy-blue); color: #fff; border-radius: 18px; padding: 1.5rem 1.25rem; margin-bottom: 1rem; text-align: center; }
.profile-avatar { width: 72px; height: 72px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 1.6rem; margin: 0 auto 10px; border: 3px solid rgba(255,255,255,0.2); }
.profile-name { font-size: 1.2rem; font-weight: 800; }

.cpn-wallet-card { background: linear-gradient(135deg, #0b1e36, #1e3a8a); color: #fff; border-radius: 18px; padding: 1.25rem; margin-bottom: 1rem; }
.cpn-row { display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; margin-bottom: 10px; }
.val-gold { color: #f59e0b; font-weight: 800; font-size: 1.3rem; }

.profile-stats { display: flex; gap: 8px; margin-bottom: 1rem; }
.profile-stat { flex: 1; background: #fff; border: 1.5px solid var(--border-light); border-radius: 14px; padding: 12px; text-align: center; }
.profile-stat-val { font-size: 1.3rem; font-weight: 800; color: var(--navy-blue); }
.profile-stat-lbl { font-size: 0.7rem; color: var(--text-muted); font-weight: 700; text-transform: uppercase; }

.profile-tabs { display: flex; gap: 6px; margin-bottom: 1rem; }
.profile-tab { flex: 1; padding: 10px; border-radius: 10px; border: 1.5px solid var(--border-light); background: #fff; color: var(--text-muted); font-weight: 700; font-size: 0.82rem; cursor: pointer; }
.profile-tab.active { background: var(--navy-blue); color: #fff; border-color: var(--navy-blue); }

footer { background: #fff; text-align: center; padding: 1.5rem 1rem; font-size: 0.82rem; color: var(--text-muted); border-top: 1.5px solid var(--border-light); margin-top: auto; line-height: 1.6; }
</style>
</head>
<body>

<div id="toast-container"></div>

<header>
    <div class="brand-box" onclick="switchNav('feed')">
        <div class="brand-title">IJEBU-IMUSIN <span>CONNECT</span></div>
    </div>
    <div class="header-auth" id="header-auth"></div>
</header>

<div class="top-nav-pills">
    <div class="nav-pill active" data-nav="feed" onclick="switchNav('feed')">📰 Community Feed</div>
    <div class="nav-pill" data-nav="market" onclick="switchNav('market')">🛒 Market Hub</div>
    <div class="nav-pill" id="admin-pill" style="display:none;" onclick="window.location.href='/admin'">⚙️ Admin</div>
</div>

<div class="app-container">

    <!-- FEED VIEW -->
    <div id="view-feed" class="view-section active">
        <div class="card">
            <form onsubmit="handlePostSubmit(event)">
                <div class="form-group">
                    <textarea class="form-control" id="post-content" rows="2" placeholder="Share community news, updates, or announcements..."></textarea>
                </div>
                <button type="submit" class="btn-submit" style="background:var(--navy-blue);">Publish Update</button>
            </form>
        </div>
        <div id="feed-posts-container"></div>
    </div>

    <!-- MARKET VIEW -->
    <div id="view-market" class="view-section">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Market Hub</h3>
            <button onclick="startSellItem()" style="background:var(--emerald-green);color:#fff;border:none;padding:8px 14px;border-radius:10px;font-weight:700;font-size:0.82rem;cursor:pointer;">
                + Sell Item
            </button>
        </div>
        <div class="card" style="padding:0.75rem;margin-bottom:1rem;">
            <input type="text" class="form-control" id="market-search" placeholder="Search farm produce, land, electronics..." onkeyup="loadProducts()">
        </div>
        <div id="products-container" class="card"></div>
    </div>

    <!-- USER PROFILE & WALL VIEW -->
    <div id="view-profile" class="view-section">
        <button onclick="switchNav('feed')" style="background:#fff;border:1.5px solid var(--border-light);padding:6px 14px;border-radius:10px;font-weight:700;font-size:0.8rem;cursor:pointer;margin-bottom:1rem;">← Back to Feed</button>
        <div id="profile-wall-container"></div>
    </div>

</div>

<!-- CPN ONBOARDING / UPGRADE MODAL -->
<div id="cpn-upgrade-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:440px;width:100%;background:#fff;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">Become CPN Partner</h3>
            <button onclick="closeCPNModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <p style="font-size:0.85rem;color:var(--text-muted);margin-bottom:1rem;line-height:1.5;">
            To sell items on Ijebu Market Hub, you must register as an official <strong>CPN Partner (₦2,000)</strong>. Unlock unlimited listings and earn <strong>10% Tier-1 &amp; 5% Tier-2 referral rewards</strong> on traders you invite!
        </p>
        <button class="btn-submit" style="background:var(--amber-gold);" onclick="triggerCPNUpgrade()">Proceed to CPN Partner Upgrade (₦2,000)</button>
    </div>
</div>

<!-- SELL ITEM MODAL -->
<div id="sell-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999;align-items:center;justify-content:center;padding:1rem;">
    <div class="card" style="max-width:480px;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <h3 style="font-weight:800;color:var(--navy-blue);">List Market Item</h3>
            <button onclick="closeSellModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;">&times;</button>
        </div>
        <form onsubmit="handleProductSubmit(event)">
            <div class="form-group">
                <label>Title</label>
                <input type="text" class="form-control" id="prod-title" placeholder="e.g. Fresh Palm Oil (25 Liters)" required>
            </div>
            <div class="form-group">
                <label>Category</label>
                <select class="form-control" id="prod-category">
                    <option value="Agriculture">Agriculture &amp; Farming</option>
                    <option value="Real Estate">Land &amp; Property</option>
                    <option value="Electronics">Electronics &amp; Phones</option>
                    <option value="Fashion">Fashion &amp; Clothing</option>
                    <option value="Services">Local Services</option>
                    <option value="General">General Goods</option>
                </select>
            </div>
            <div class="form-group">
                <label>Price (₦)</label>
                <input type="number" class="form-control" id="prod-price" placeholder="25000" required>
            </div>
            <div class="form-group">
                <label>WhatsApp Number</label>
                <input type="text" class="form-control" id="prod-whatsapp" placeholder="09018363715" required>
            </div>
            <div class="form-group">
                <label>Description</label>
                <textarea class="form-control" id="prod-desc" rows="2" placeholder="Details about quantity, location..."></textarea>
            </div>
            <button type="submit" class="btn-submit">Publish Market Item</button>
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
    <i class="fa-solid fa-location-dot"></i> Okepo Quarters, Ijebu-Imusin — <i class="fa-solid fa-phone"></i> 09018363715<br>
    <span style="font-size:.75rem;">willysmediaworld@gmail.com</span><br><br>
    &copy; 2026 Ijebu-Imusin Connect. All Rights Reserved.
</footer>

<script>
let currentUser = null;

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

    if(target === 'feed') loadPosts();
    if(target === 'market') loadProducts();
}

async function checkSession() {
    try {
        const res = await fetch('/api/auth/me');
        const data = await res.json();
        if(data.logged_in) {
            currentUser = data.user;
            renderHeaderAuth();
            if(currentUser.user_type === 'Admin') {
                document.getElementById('admin-pill').style.display = 'block';
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

// POSTS
async function handlePostSubmit(e) {
    e.preventDefault();
    if(!currentUser) return window.location.href = '/auth';
    const content = document.getElementById('post-content').value.trim();
    if(!content) return showToast('Please enter post content', 'error');

    const res = await fetch('/api/posts', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({content: content})
    });
    const data = await res.json();
    if(data.success) {
        showToast(data.message);
        document.getElementById('post-content').value = '';
        loadPosts();
    } else showToast(data.message, 'error');
}

async function loadPosts() {
    const res = await fetch('/api/posts');
    const posts = await res.json();
    const container = document.getElementById('feed-posts-container');
    if(!posts.length) {
        container.innerHTML = `<div class="card" style="text-align:center;color:var(--text-muted);">No community posts yet. Be the first to share!</div>`;
        return;
    }
    container.innerHTML = posts.map(p => renderPostCard(p)).join('');
}

function renderPostCard(p) {
    const badge = p.user_type === 'CPN Partner' ? '<span class="badge badge-partner">CPN Partner</span>' : (p.user_type === 'Admin' ? '<span class="badge badge-admin">Admin</span>' : '');
    return `
        <div class="feed-post">
            <div class="post-header">
                <div class="avatar" style="background:var(--navy-blue);" onclick="openProfile('${p.username}')">${p.full_name.charAt(0).toUpperCase()}</div>
                <div>
                    <div class="post-author"><span class="clickable-user" onclick="openProfile('${p.username}')">${p.full_name}</span> ${badge}</div>
                    <div class="post-meta">@${p.username} • ${new Date(p.created_at).toLocaleDateString()}</div>
                </div>
            </div>
            <div class="post-content">${p.content}</div>
            <div class="post-actions">
                <button class="post-action ${p.liked_by_me ? 'liked':''}" onclick="toggleLike(${p.id})">❤️ ${p.likes_count}</button>
            </div>
        </div>
    `;
}

async function toggleLike(pid) {
    if(!currentUser) return window.location.href = '/auth';
    await fetch(`/api/posts/${pid}/like`, {method:'POST'});
    loadPosts();
}

// MARKETPLACE
async function loadProducts() {
    const q = document.getElementById('market-search').value.trim();
    const res = await fetch(`/api/products?q=${encodeURIComponent(q)}`);
    const products = await res.json();
    const container = document.getElementById('products-container');

    if(!products.length) {
        container.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:1rem;">No market items found.</div>`;
        return;
    }

    container.innerHTML = products.map(p => `
        <div class="product-card">
            <div class="product-img-box"><i class="fa-solid fa-store"></i></div>
            <div style="flex:1;">
                <h4 style="font-weight:800;color:var(--navy-blue);font-size:0.95rem;">${p.title}</h4>
                <div style="font-weight:800;color:var(--emerald-green);font-size:0.9rem;margin:2px 0;">${formatNaira(p.price)}</div>
                <div style="font-size:0.75rem;color:var(--text-muted);">Seller: <span class="clickable-user" onclick="openProfile('${p.seller_username}')">@${p.seller_username}</span></div>
            </div>
            <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" class="btn-whatsapp"><i class="fa-brands fa-whatsapp"></i> Chat</a>
        </div>
    `).join('');
}

function startSellItem() {
    if(!currentUser) return window.location.href = '/auth';
    if(currentUser.user_type === 'Resident') {
        document.getElementById('cpn-upgrade-modal').style.display = 'flex';
    } else {
        document.getElementById('sell-modal').style.display = 'flex';
    }
}
function closeCPNModal() { document.getElementById('cpn-upgrade-modal').style.display = 'none'; }
function closeSellModal() { document.getElementById('sell-modal').style.display = 'none'; }

async function triggerCPNUpgrade() {
    const res = await fetch('/api/cpn/upgrade', {method:'POST'});
    const data = await res.json();
    if(data.paystack) {
        window.location.href = data.redirect_url;
    } else if(data.success) {
        showToast(data.message);
        closeCPNModal();
        await checkSession();
        openProfile(currentUser.username);
    } else showToast(data.message, 'error');
}

async function handleProductSubmit(e) {
    e.preventDefault();
    const res = await fetch('/api/products', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
            title: document.getElementById('prod-title').value,
            category: document.getElementById('prod-category').value,
            price: document.getElementById('prod-price').value,
            whatsapp_number: document.getElementById('prod-whatsapp').value,
            description: document.getElementById('prod-desc').value
        })
    });
    const data = await res.json();
    if(data.success) {
        showToast(data.message);
        closeSellModal();
        loadProducts();
    } else showToast(data.message, 'error');
}

// PROFILE WALL & CPN DASHBOARD
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
                <div class="cpn-row">
                    <span>CPN Wallet Balance:</span>
                    <span class="val-gold">${formatNaira(u.wallet_balance)}</span>
                </div>
                <div class="cpn-row">
                    <span>CPN Referral Code:</span>
                    <strong style="background:rgba(255,255,255,0.15);padding:3px 8px;border-radius:6px;">${u.referral_code}</strong>
                </div>
                <div class="cpn-row">
                    <span>Direct Partners Recruited:</span>
                    <strong style="color:#4ade80;">${u.recruits_count} Partners</strong>
                </div>
                <div class="cpn-row" style="margin-bottom:12px;">
                    <span>Partner Link:</span>
                    <button onclick="copyRefLink('${u.referral_code}')" style="background:#d97706;color:#fff;border:none;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:700;cursor:pointer;">Copy Link</button>
                </div>
                <button onclick="openCashoutModal()" style="background:#059669;color:#fff;border:none;padding:10px;border-radius:8px;width:100%;font-weight:800;cursor:pointer;">Request Bank Cashout</button>
            </div>
        `;
    }

    const postsHtml = u.posts.length ? u.posts.map(p => renderPostCard(p)).join('') : `<div class="card" style="text-align:center;color:var(--text-muted);">No posts published yet.</div>`;
    const productsHtml = u.products.length ? u.products.map(p => `
        <div class="product-card">
            <div class="product-img-box"><i class="fa-solid fa-store"></i></div>
            <div style="flex:1;">
                <h4 style="font-weight:800;color:var(--navy-blue);">${p.title}</h4>
                <div style="font-weight:800;color:var(--emerald-green);">${formatNaira(p.price)}</div>
            </div>
            <a href="https://wa.me/234${p.whatsapp_number.replace(/^0/,'')}" target="_blank" class="btn-whatsapp">Chat</a>
        </div>
    `).join('') : `<div class="card" style="text-align:center;color:var(--text-muted);">No market items listed.</div>`;

    container.innerHTML = `
        <div class="profile-hero">
            <div class="profile-avatar" style="background:var(--emerald-green);">${u.full_name.charAt(0).toUpperCase()}</div>
            <div class="profile-name">${u.full_name}</div>
            <div style="font-size:0.85rem;opacity:0.8;">@${u.username} • ${u.user_type}</div>
        </div>

        ${cpnWalletBlock}

        <div class="profile-stats">
            <div class="profile-stat"><div class="profile-stat-val">${u.posts_count}</div><div class="profile-stat-lbl">Posts</div></div>
            <div class="profile-stat"><div class="profile-stat-val">${u.products_count}</div><div class="profile-stat-lbl">Market Items</div></div>
        </div>

        <div class="profile-tabs">
            <button class="profile-tab active" id="tab-btn-posts" onclick="toggleProfileTab('posts')">Posts (${u.posts_count})</button>
            <button class="profile-tab" id="tab-btn-prods" onclick="toggleProfileTab('products')">Market (${u.products_count})</button>
        </div>

        <div id="profile-tab-posts">${postsHtml}</div>
        <div id="profile-tab-products" style="display:none;" class="card">${productsHtml}</div>
    `;

    document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
    document.getElementById('view-profile').classList.add('active');
}

function toggleProfileTab(tab) {
    document.querySelectorAll('.profile-tab').forEach(t => t.classList.remove('active'));
    if(tab === 'posts') {
        document.getElementById('tab-btn-posts').classList.add('active');
        document.getElementById('profile-tab-posts').style.display = 'block';
        document.getElementById('profile-tab-products').style.display = 'none';
    } else {
        document.getElementById('tab-btn-prods').classList.add('active');
        document.getElementById('profile-tab-posts').style.display = 'none';
        document.getElementById('profile-tab-products').style.display = 'block';
    }
}

function copyRefLink(code) {
    const link = `${window.location.origin}/auth?ref=${code}`;
    navigator.clipboard.writeText(link);
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

window.onload = function() {
    checkSession();
    loadPosts();
};
</script>
</body>
</html>
"""

AUTH_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auth | Ijebu-Imusin Connect</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;800&display=swap" rel="stylesheet">
<style>
    body { font-family:'Plus Jakarta Sans',sans-serif; background:#f8fafc; display:flex; justify-content:center; align-items:center; min-height:100vh; padding:20px; margin:0; }
    .auth-card { background:#fff; border:1.5px solid #cbd5e1; padding:24px; border-radius:18px; max-width:400px; width:100%; box-shadow:0 8px 30px rgba(0,0,0,0.06); }
    .brand { font-size:1.3rem; font-weight:800; color:#0b1e36; text-align:center; margin-bottom:1rem; }
    .brand span { color:#059669; }
    .auth-tabs { display:flex; gap:6px; margin-bottom:1rem; }
    .auth-tab { flex:1; padding:10px; border-radius:10px; border:1.5px solid #cbd5e1; background:#f8fafc; font-weight:700; cursor:pointer; font-size:0.82rem; }
    .auth-tab.active { background:#0b1e36; color:#fff; border-color:#0b1e36; }
    .form-group { display:flex; flex-direction:column; gap:5px; margin-bottom:12px; }
    .form-group label { font-size:0.8rem; font-weight:700; color:#0f172a; }
    .form-control { padding:10px 12px; border-radius:10px; border:1.5px solid #cbd5e1; font-size:0.88rem; outline:none; font-family:inherit; }
    .btn-submit { background:#059669; color:#fff; border:none; padding:12px; border-radius:10px; font-weight:800; cursor:pointer; width:100%; margin-top:6px; }
</style>
</head>
<body>
<div class="auth-card">
    <div class="brand">IJEBU-IMUSIN <span>CONNECT</span></div>
    <div class="auth-tabs">
        <button class="auth-tab active" id="tab-btn-login" onclick="toggleAuth('login')">Sign In</button>
        <button class="auth-tab" id="tab-btn-register" onclick="toggleAuth('register')">Register Free</button>
    </div>

    <!-- LOGIN FORM -->
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

    <!-- REGISTER FORM -->
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
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Panel — Ijebu-Imusin Connect</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
    body { font-family:'Plus Jakarta Sans',sans-serif; background:#f8fafc; color:#0f172a; padding:20px; margin:0; }
    .container { max-width:1000px; margin:0 auto; }
    .card { background:#fff; border:1px solid #cbd5e1; border-radius:12px; padding:20px; margin-bottom:20px; }
    table { width:100%; border-collapse:collapse; font-size:0.85rem; margin-top:10px; }
    th, td { padding:10px; border-bottom:1px solid #cbd5e1; text-align:left; }
    th { background:#f1f5f9; }
    .btn { padding:6px 12px; border-radius:6px; border:none; font-weight:700; cursor:pointer; font-size:0.75rem; text-decoration:none; display:inline-block; }
    .btn-green { background:#059669; color:#fff; } .btn-red { background:#ef4444; color:#fff; }
</style>
</head>
<body>
<div class="container">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
        <h2>⚙️ Admin Control Panel</h2>
        <a href="/" class="btn" style="background:#0b1e36;color:#fff;">← Return to App</a>
    </div>

    <div class="card" id="overview-box">Loading Overview...</div>

    <div class="card">
        <h3>Pending Cashouts</h3>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th>User</th><th>Amount</th><th>Bank Details</th><th>Action</th></tr></thead>
                <tbody id="payouts-table"><tr><td colspan="4">Loading...</td></tr></tbody>
            </table>
        </div>
    </div>
</div>

<script>
async function loadDashboard() {
    const res = await fetch('/api/admin/overview');
    const data = await res.json();
    if(!data.success) { alert('Admin login required.'); window.location.href = '/auth'; return; }

    document.getElementById('overview-box').innerHTML = `
        <p>Total Registered Users: <strong>${data.total_users}</strong></p>
        <p>Official CPN Partners: <strong>${data.total_partners}</strong></p>
        <p>Active Market Items: <strong>${data.total_products}</strong></p>
        <p>Pending Cashouts: <strong>${data.pending_payouts}</strong></p>
    `;
    loadPayouts();
}

async function loadPayouts() {
    const res = await fetch('/api/admin/payouts');
    const payouts = await res.json();
    const tbody = document.getElementById('payouts-table');
    if(!payouts.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:#64748b;">No pending payouts.</td></tr>`;
        return;
    }
    tbody.innerHTML = payouts.map(p => `
        <tr>
            <td>${p.full_name} (@${p.username})</td>
            <td><strong>₦${p.amount.toLocaleString()}</strong></td>
            <td>${p.bank_name} - ${p.account_number} (${p.account_name})</td>
            <td>
                ${p.status === 'pending' ? `
                    <button class="btn btn-green" onclick="processPayout(${p.id}, 'approved')">Approve</button>
                    <button class="btn btn-red" onclick="processPayout(${p.id}, 'rejected')">Reject</button>
                ` : `<strong>${p.status}</strong>`}
            </td>
        </tr>
    `).join('');
}

async function processPayout(pid, status) {
    await fetch('/api/admin/payouts', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({payout_id: pid, status: status})
    });
    loadDashboard();
}

window.onload = loadDashboard;
</script>
</body>
</html>
"""


# =============================================================================
# MAIN APP ROUTES
# =============================================================================

@app.route('/')
def main_app():
    return render_template_string(INDEX_TEMPLATE)

@app.route('/auth')
def auth_app():
    return render_template_string(AUTH_TEMPLATE)

@app.route('/admin')
def admin_app():
    return render_template_string(ADMIN_TEMPLATE)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)