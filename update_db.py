from app import app
from models import db
from database import create_default_admin

def update_schema():
    with app.app_context():
        print("[INFO] Updating database schema...")
        # Drop and recreate tables to ensure new columns are added
        # (Since this is a new setup, this is safe)
        db.drop_all()
        db.create_all()
        print("[SUCCESS] Tables recreated successfully.")
        
        # Re-create the default admin user
        create_default_admin()
        print("[SUCCESS] Ready to go!")

if __name__ == "__main__":
    update_schema()
