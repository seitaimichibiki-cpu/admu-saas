import os
os.environ["DATABASE_URL"] = "postgres://fake:fake@fake.com:5432/fake"
import db
print("Testing pool creation...")
try:
    db.init_db()
except Exception as e:
    print(f"Error: {e}")
