import os
from datetime import timedelta

import pytesseract

# Set Tesseract path
try:
    # Check for Namecheap shared hosting path (typical structure: /home/username/domain/bin/tesseract)
    # You can also set this via environment variable in cPanel
    home_dir = os.path.expanduser('~')
    possible_tesseract_path = os.path.join(home_dir, 'automation.digicarehouse.com', 'bin', 'tesseract')
    
    if os.path.exists(possible_tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = possible_tesseract_path
    elif os.name == 'nt': # Windows local development
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except:
    pass  # Will use system PATH if nothing else works


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    FREE_CONVERSION_LIMIT = 3  # Maximum free conversions per user
    FREE_PAGE_LIMIT = 5        # Maximum pages per file for free users
    PRO_CONVERSION_TYPES = {'scanned', 'custom'} # Features locked to Pro tier
    
    # MySQL Database Configuration
    MYSQL_HOST = os.environ.get('DB_HOST', os.environ.get('MYSQL_HOST',''))
    MYSQL_PORT = int(os.environ.get('DB_PORT', os.environ.get('MYSQL_PORT','')))
    MYSQL_USER = os.environ.get('DB_USER', os.environ.get('MYSQL_USER',''))
    MYSQL_PASSWORD = os.environ.get('DB_PASSWORD', os.environ.get('MYSQL_PASSWORD',''))
    MYSQL_DATABASE = os.environ.get('DB_NAME', os.environ.get('MYSQL_DATABASE', ''))
    
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}'
    
    # Fallback to SQLite for local development if MySQL isn't available
    if 'localhost' in SQLALCHEMY_DATABASE_URI or '127.0.0.1' in SQLALCHEMY_DATABASE_URI:
        import socket
        try:
            # Short timeout to check if MySQL port is open
            with socket.create_connection((MYSQL_HOST, MYSQL_PORT), timeout=1):
                pass
        except:
            print("[INFO] MySQL connection failed, falling back to SQLite for local testing.")
            SQLALCHEMY_DATABASE_URI = 'sqlite:///pdf_converter.db'
    
    # Optional: Log the database being used
    # print(f"Using database: {SQLALCHEMY_DATABASE_URI}")
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False  # Set to True for SQL debugging

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    
    UPLOAD_FOLDER = 'uploads'
    OUTPUT_FOLDER = 'outputs'
    ALLOWED_EXTENSIONS = {'pdf'}
    MAX_CONTENT_LENGTH = None  # Removed file size limit as requested

    # Set GEMINI_API_KEY in your .env file (see .env.example)
    API_KEY = os.environ.get('GEMINI_API_KEY', "")
    
    GEMINI_MODEL_NAME = os.environ.get('GEMINI_MODEL_NAME', "gemini-2.5-pro")

    # --- Extraction tuning -------------------------------------------------
    # Rasterisation resolution for vision extraction. 300 DPI is the standard
    # floor for OCR of financial documents; below ~200 the error rate on small
    # digits climbs sharply.
    EXTRACTION_DPI = int(os.environ.get('EXTRACTION_DPI', 300))

    # Deterministic output. Extraction is a transcription task, not a creative
    # one, so there is no reason to sample.
    GEMINI_TEMPERATURE = float(os.environ.get('GEMINI_TEMPERATURE', 0.0))
    GEMINI_MAX_OUTPUT_TOKENS = int(os.environ.get('GEMINI_MAX_OUTPUT_TOKENS', 8192))

    # Pages processed concurrently. Raise only if your Gemini quota allows.
    EXTRACTION_WORKERS = int(os.environ.get('EXTRACTION_WORKERS', 3))

    # Cost ceiling: every page is one vision call, so an unbounded document can
    # cost more than the subscription. Applies to Pro users too.
    MAX_PAGES_PER_CONVERSION = int(os.environ.get('MAX_PAGES_PER_CONVERSION', 200))

    # Converted workbooks are deleted after this many hours.
    OUTPUT_RETENTION_HOURS = int(os.environ.get('OUTPUT_RETENTION_HOURS', 48))

    # --- Session hardening -------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_DEBUG', 'False').lower() != 'true'
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    # Payment Configuration
    STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', 'pk_test_placeholder')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_placeholder')
    STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', 'price_placeholder') # For $15 plan

    PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID', 'AV_placeholder')
    PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET', 'EB_placeholder')
    PAYPAL_MODE = os.environ.get('PAYPAL_MODE', 'sandbox') # sandbox or live

    PRO_PLAN_PRICE = 15.00