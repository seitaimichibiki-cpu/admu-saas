import sys
# Make sure we use AdMu backend path
sys.path.append('backend')
import db

# Ensure DB is initialized
db.init_db()

# Check current name
conn = db.get_conn()
clinics = conn.execute("SELECT id, name FROM clinics").fetchall()
print("Before:", [dict(c) for c in clinics])

# Attempt to upsert
res = db.upsert_clinic({
    "id": 1,
    "name": "整体院導",
    "license_key": None
})

clinics = conn.execute("SELECT id, name FROM clinics").fetchall()
print("After:", [dict(c) for c in clinics])
