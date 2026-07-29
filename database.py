"""
Database initialization and utility functions
"""
import os

from models import db, User


def init_db(app):
    """Initialize the database and create tables"""
    with app.app_context():
        db.create_all()
        create_admin_from_env()
        print("[SUCCESS] Database initialized successfully")


def create_admin_from_env():
    """
    Create an admin user from ADMIN_USERNAME / ADMIN_PASSWORD if both are set.

    Deliberately does nothing when those are absent. This used to seed
    admin/password unconditionally, which left a known-credential account on
    every deployment.
    """
    username = os.environ.get('ADMIN_USERNAME')
    password = os.environ.get('ADMIN_PASSWORD')

    if not username or not password:
        print("[INFO] ADMIN_USERNAME/ADMIN_PASSWORD not set - no admin user created.")
        return

    if len(password) < 12:
        print("[ERROR] ADMIN_PASSWORD must be at least 12 characters. No admin user created.")
        return

    if User.query.filter_by(username=username).first():
        print(f"[INFO] Admin user '{username}' already exists.")
        return

    admin = User(
        username=username,
        email=os.environ.get('ADMIN_EMAIL') or f'{username}@localhost',
        is_active=True,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f"[SUCCESS] Admin user '{username}' created.")


def reset_database(app):
    """Drop all tables and recreate - USE WITH CAUTION"""
    with app.app_context():
        db.drop_all()
        db.create_all()
        create_admin_from_env()
        print("[WARNING] Database reset complete")
