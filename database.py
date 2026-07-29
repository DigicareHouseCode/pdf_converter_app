"""
Database initialization and utility functions
"""
from models import db, User


def init_db(app):
    """Initialize the database and create tables"""
    with app.app_context():
        # Create all tables
        db.create_all()
        
        # Create default admin user if not exists
        create_default_admin()
        
        print("[SUCCESS] Database initialized successfully")


def create_default_admin():
    """Create the default admin user if it doesn't exist"""
    admin = User.query.filter_by(username='admin').first()
    
    if not admin:
        admin = User(
            username='admin',
            email='admin@example.com',
            is_active=True
        )
        admin.set_password('password')  # Default password - should be changed
        db.session.add(admin)
        db.session.commit()
        print("[INFO] Default admin user created (username: admin, password: password)")
    else:
        print("[INFO] Admin user already exists")


def reset_database(app):
    """Drop all tables and recreate - USE WITH CAUTION"""
    with app.app_context():
        db.drop_all()
        db.create_all()
        create_default_admin()
        print("[WARNING] Database reset complete")
