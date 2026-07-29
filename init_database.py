"""
Database Initialization Script
Run this script to set up the database tables and create the default admin user.

Usage: python init_database.py
"""
import sys
import os

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, User
from database import create_admin_from_env


def main():
    print("=" * 50)
    print("PDF Converter - Database Initialization")
    print("=" * 50)
    
    try:
        with app.app_context():
            # Create all tables
            print("\n[DB] Creating database tables...")
            db.create_all()
            print("[SUCCESS] Database tables created successfully!")
            
            # Create an admin user only if ADMIN_USERNAME/ADMIN_PASSWORD are set
            print("\n[ADMIN] Checking for admin credentials in the environment...")
            create_admin_from_env()
            
            # Verify tables
            print("\n[VERIFY] Verifying tables...")
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            print(f"   Tables created: {', '.join(tables)}")
            
            # Count users
            user_count = User.query.count()
            print(f"   Total users: {user_count}")
            
            print("\n" + "=" * 50)
            print("[SUCCESS] Database initialization complete!")
            print("=" * 50)
            print("\nYou can now run the application with: python app.py")
            print("Set ADMIN_USERNAME and ADMIN_PASSWORD in .env to create an admin user.")
            
    except Exception as e:
        print(f"\n[ERROR] Error during initialization: {str(e)}")
        print("\nPlease check your MySQL connection settings in config.py:")
        print("  - MYSQL_HOST")
        print("  - MYSQL_PORT")
        print("  - MYSQL_USER")
        print("  - MYSQL_PASSWORD")
        print("  - MYSQL_DATABASE")
        sys.exit(1)


if __name__ == '__main__':
    main()
