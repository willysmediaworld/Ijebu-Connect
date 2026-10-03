import os
import psycopg2
from werkzeug.security import generate_password_hash

# ============================================================
#  ⚙️ EDIT THESE THREE LINES
# ============================================================

# Paste your Render "External Database URL" here
DATABASE_URL = "postgresql://..."

# Pick a NEW strong password (min 8 chars, letters + numbers + symbol)
NEW_PASSWORD = "YourStrongPassword123!"

# ============================================================

NEW_USERNAME = "ijebuconnect"
NEW_NAME     = "Sir Ola'Rotimi"

url = DATABASE_URL.replace("postgres://", "postgresql://")
conn = psycopg2.connect(url)
cur  = conn.cursor()

new_hash = generate_password_hash(NEW_PASSWORD)

cur.execute("""
    UPDATE users
    SET username = %s,
        full_name = %s,
        password_hash = %s
    WHERE username = 'imusinconnect' OR user_type = 'Admin'
""", (NEW_USERNAME, NEW_NAME, new_hash))

print(f"✅ Rows updated: {cur.rowcount}")

conn.commit()
cur.close()
conn.close()
print("✅ Done.")
print(f"   Username: {NEW_USERNAME}")
print(f"   Password: (the one you typed above)")
