import os
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
    GOOGLE_CLIENT_ID = "92902851975-7la0egcou73k05fv756d68ali4oou99g.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = "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"
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    
    UPLOAD_FOLDER = 'uploads'
    OUTPUT_FOLDER = 'outputs'
    ALLOWED_EXTENSIONS = {'pdf'}
    MAX_CONTENT_LENGTH = None  # Removed file size limit as requested

    # Set GEMINI_API_KEY in your .env file (see .env.example)
    API_KEY = os.environ.get('GEMINI_API_KEY', "")
    
    # Model Configuration (User can override this to 'gemini-2.5-pro' if available)
    GEMINI_MODEL_NAME = os.environ.get('GEMINI_MODEL_NAME', "gemini-2.5-pro")

    # QuickBooks Configuration (Development Mode)
    QUICKBOOKS_ENABLED = True
    QUICKBOOKS_DEVELOPMENT_MODE = True
    QUICKBOOKS_SANDBOX = True

    # Development credentials (not needed for mock service)
    QUICKBOOKS_CONSUMER_KEY = "ABdjzWJDPUQaKr9DC8hDpl4RGewKDepQsgkimh77GgTh2U6nBD"
    QUICKBOOKS_CONSUMER_SECRET = "CaLnrd3fbplzBH4gMOsoJYzznQNvl5tY7QQ0eX8N"
    QUICKBOOKS_ACCESS_TOKEN = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMTI4Q0JDLUhTMjU2IiwieC5vcmciOiJIMCJ9..kWPfwXPs7ioWacIHx1moWg.6J48H69cR2dAcdefsbOJILPcT0tnqCiveq4vUpI1ZWOsUvRzcddeQM3kyQMq3iYRjDQgJXDxURC2eVGO_cVBDHT_xRr9hC8ZjDU6s4C74EWgfk9unnk_LgQ9gMtTX1acpqiWhxCC3NYi4dcbkEWdFllq-LsfNlJ3dAvwhtxheKb15XmAOk1hpiSdM-oXADZ3tsXSVjkUi_MpSGWSfHIy-HsoUowlPIzefuOgzy86rk-ipykjAUkgRXRapAt9VX06Zs-hVBDWQ-yMW6htcfvlQS6B0gHbN-yXcGzhxJoNu9LJkoTe_O-yDrO_CmgG9cFpYPTBWRZ0sutEhsTvnAX7qUprtVAFZvzJwPRF9GTMMrEw9DAB3nK5M92nyp1MQHRc9nkRV-8K6MLy_HSZ0Wq_P02q-21aw8LPWir-A5tRXyOTBIfOfc--lgrkHfyDodWTpclEI3PtlLGz0pJEDz0xHBRT0vn719Vkb5f-DGeO8o3x6NZFM0A2KgS3pMUcF3ILZpxC9edCryhrJY5AV7Op7v6eZWXw6cv8ag8oIKFDyYBsR79811lDDqodMHJoex57rlWiV-MDgIJ1fGhVMFbM5CwnSmVV5LvOhjK221Avl9BdcjG4667rKxLkaXVk8XKq.wDUQnASdCu4SVZlBjoHKKw"
    QUICKBOOKS_ACCESS_TOKEN_SECRET = "RT1-246-H0-1771175113ru1ax6zsyzzbx5d0gqp3"
    QUICKBOOKS_COMPANY_ID = "9341455649885282"

    # QuickBooks OAuth 2.0 Configuration
    QB_CLIENT_ID = "ABdjzWJDPUQaKr9DC8hDpl4RGewKDepQsgkimh77GgTh2U6nBD"
    QB_CLIENT_SECRET = "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"
    QB_REDIRECT_URI = "http://localhost:5000/quickbooks/callback"
    QB_ENVIRONMENT = "sandbox"  # or "production"

    # Payment Configuration
    STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', 'pk_test_placeholder')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_placeholder')
    STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', 'price_placeholder') # For $15 plan

    PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID', 'AV_placeholder')
    PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET', 'EB_placeholder')
    PAYPAL_MODE = os.environ.get('PAYPAL_MODE', 'sandbox') # sandbox or live

    PRO_PLAN_PRICE = 15.00