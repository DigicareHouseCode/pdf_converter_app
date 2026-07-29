from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)  # Nullable for Google users
    email = db.Column(db.String(120), unique=True, nullable=True)
    
    # Google OAuth fields
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=True)
    picture_url = db.Column(db.String(500), nullable=True)
    
    is_pro = db.Column(db.Boolean, default=False)
    pro_expiry = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    conversions = db.relationship('ConversionHistory', backref='user', lazy='dynamic')
    # quickbooks_token = db.relationship('QuickBooksToken', backref='user', uselist=False) # Commented out QB integration
    
    def set_password(self, password):
        """Hash and set the password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches the hash"""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'


class ConversionHistory(db.Model):
    """Track PDF conversion history"""
    __tablename__ = 'conversion_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    output_filename = db.Column(db.String(255), nullable=True)
    conversion_type = db.Column(db.String(50), nullable=False)  # scanned, general, selectable, custom
    success = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    

    def __repr__(self):
        return f'<ConversionHistory {self.original_filename} - {self.conversion_type}>'


class TaskProgress(db.Model):
    """Store task progress for background jobs"""
    __tablename__ = 'task_progress'
    
    id = db.Column(db.String(36), primary_key=True)  # UUID
    status = db.Column(db.String(50), default='processing')
    progress = db.Column(db.Integer, default=0)
    message = db.Column(db.String(255), default='Initializing...')
    output_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'task_id': self.id,
            'status': self.status,
            'progress': self.progress,
            'message': self.message,
            'output_filename': self.output_filename
        }


"""
class QuickBooksToken(db.Model):
    # Store QuickBooks OAuth tokens persistently
    __tablename__ = 'quickbooks_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    realm_id = db.Column(db.String(100), nullable=False)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    company_info = db.Column(db.JSON, nullable=True)  # Store company info as JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def is_token_expired(self):
        # Check if access token is expired
        if not self.token_expires_at:
            return True
        # Add 5 minute buffer
        from datetime import timedelta
        return datetime.utcnow() > (self.token_expires_at - timedelta(minutes=5))
    
    def __repr__(self):
        return f'<QuickBooksToken for user_id={self.user_id}>'
"""
