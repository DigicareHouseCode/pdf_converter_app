from flask import Flask, render_template, request, send_file, flash, redirect, url_for, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import os
import uuid
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
import stripe
import requests
from requests.auth import HTTPBasicAuth

# Load environment variables from .env file
load_dotenv()
from werkzeug.utils import secure_filename
from config import Config
from models import db, User, ConversionHistory, TaskProgress
from auth import auth_bp
from converters.scanned_pdf_converter import extract_data_with_gemini_full
from converters.general_pdf_converter import convert_pdf_to_excel
from converters.selectable_pdf_converter import extract_pdf_to_excel
from converters.custom_pdf_converter import extract_custom_pdf_to_excel
import threading
from concurrent.futures import ThreadPoolExecutor

# In-memory tasks dictionary removed in favor of Database-backed TaskProgress

def update_task_progress(task_id, progress, message):
    """Callback function to update task progress in DB"""
    with app.app_context():
        try:
            task = TaskProgress.query.get(task_id)
            if task:
                task.progress = progress
                task.message = message
                db.session.commit()
        except Exception as e:
            print(f"[ERROR] Failed to update task progress: {e}")
            db.session.rollback()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.from_object(Config)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB limit

# Initialize database
db.init_app(app)

# Stripe API Key
stripe.api_key = app.config['STRIPE_SECRET_KEY']

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page'
login_manager.login_message_category = 'error'

# Register auth blueprint
app.register_blueprint(auth_bp, url_prefix='/auth')

@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(int(user_id))
    if user and user.is_pro and user.pro_expiry:
        if datetime.utcnow() > user.pro_expiry:
            user.is_pro = False
            db.session.commit()
            print(f"[INFO] User {user.username} Pro status expired.")
    return user


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def ensure_directories():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)


# Note: login_required is now imported from flask_login
# The @login_required decorator will automatically redirect to login page


"""
def get_quickbooks_oauth():
    return QuickBooksOAuth(
        client_id=app.config['QB_CLIENT_ID'],
        client_secret=app.config['QB_CLIENT_SECRET'],
        redirect_uri=app.config['QB_REDIRECT_URI'],
        environment=app.config['QB_ENVIRONMENT']
    )


def is_token_expired():
    # Check if token is expired or about to expire (within 5 minutes)
    expires_at = session.get('qb_token_expires_at', 0)
    buffer_time = 300  # 5 minutes buffer
    return time.time() > (expires_at - buffer_time)
"""


@app.route('/landing')
def landing():
    return render_template('landing.html', is_landing=True)


@app.route('/')
def index():
    return render_template('landing.html', is_landing=True)


@app.route('/app')
@login_required
def dashboard():
    # Count successful conversions for the current user
    conversion_count = ConversionHistory.query.filter_by(user_id=current_user.id, success=True).count()
    limit = app.config.get('FREE_CONVERSION_LIMIT', 3)
    return render_template('index.html', 
                             conversion_count=conversion_count, 
                             conversion_limit=limit,
                             is_pro=current_user.is_pro,
                             pro_expiry=current_user.pro_expiry)


@app.route('/cart')
@login_required
def cart():
    if current_user.is_pro:
        flash("You are already a Pro user!", "info")
        return redirect(url_for('dashboard'))
    
    session['payment_notified'] = False
    return render_template('cart.html', 
                         price=app.config['PRO_PLAN_PRICE'],
                         paypal_client_id=app.config['PAYPAL_CLIENT_ID'])


@app.route('/create-checkout-session')
@login_required
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'Professional Plan - PDF Converter',
                            'description': 'Unlimited conversions and advanced features',
                        },
                        'unit_amount': int(app.config['PRO_PLAN_PRICE'] * 100),
                    },
                    'quantity': 1,
                },
            ],
            mode='payment',
            success_url=url_for('payment_success', success='true', _external=True),
            cancel_url=url_for('cart', _external=True),
            client_reference_id=str(current_user.id),
            customer_email=current_user.email,
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Error creating checkout session: {str(e)}", "error")
        return redirect(url_for('cart'))


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.environ.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        # Invalid payload
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return 'Invalid signature', 400

    # Handle the checkout.session.completed event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        # Fulfill the purchase...
        user_id = session.get('client_reference_id')
        if user_id:
            upgrade_user_to_pro(user_id)

    return 'Success', 200


@app.route('/paypal/create-order', methods=['POST'])
@login_required
def create_paypal_order():
    auth = HTTPBasicAuth(app.config['PAYPAL_CLIENT_ID'], app.config['PAYPAL_SECRET'])
    
    # Get access token
    base_url = "https://api-m.sandbox.paypal.com" if app.config['PAYPAL_MODE'] == 'sandbox' else "https://api-m.paypal.com"
    
    token_response = requests.post(
        f"{base_url}/v1/oauth2/token",
        auth=auth,
        data={'grant_type': 'client_credentials'}
    )
    
    if token_response.status_code != 200:
        return jsonify({'error': 'Failed to get PayPal token'}), 500
        
    access_token = token_response.json().get('access_token')
    
    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": "USD",
                "value": str(app.config['PRO_PLAN_PRICE'])
            },
            "description": "Professional Plan - PDF Converter"
        }],
        "application_context": {
            "return_url": url_for('payment_success', _external=True),
            "cancel_url": url_for('cart', _external=True)
        }
    }
    
    order_response = requests.post(
        f"{base_url}/v2/checkout/orders",
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        },
        json=order_data
    )
    
    return jsonify(order_response.json())


@app.route('/paypal/capture-order', methods=['POST'])
@login_required
def capture_paypal_order():
    order_id = request.json.get('orderID')
    
    auth = HTTPBasicAuth(app.config['PAYPAL_CLIENT_ID'], app.config['PAYPAL_SECRET'])
    base_url = "https://api-m.sandbox.paypal.com" if app.config['PAYPAL_MODE'] == 'sandbox' else "https://api-m.paypal.com"
    
    # Get access token
    token_response = requests.post(
        f"{base_url}/v1/oauth2/token",
        auth=auth,
        data={'grant_type': 'client_credentials'}
    )
    access_token = token_response.json().get('access_token')
    
    capture_response = requests.post(
        f"{base_url}/v2/checkout/orders/{order_id}/capture",
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    )
    
    data = capture_response.json()
    if data.get('status') == 'COMPLETED':
        upgrade_user_to_pro(current_user.id)
        
    return jsonify(data)


@app.route('/payment-success')
@login_required
def payment_success():
    # The actual upgrade happens securely via Stripe Webhooks or PayPal Capture API.
    # We refresh the current user state to see if the background process finished.
    user = db.session.get(User, current_user.id)
    db.session.refresh(user)
    
    # If the user is already pro, show the success page immediately
    if user.is_pro:
        if not session.get('payment_notified'):
            flash("Success! Your account has been upgraded to Pro.", "success")
            session['payment_notified'] = True
        return render_template('payment_success.html')
    
    # If they just came from a successful checkout but the webhook hasn't fired yet
    if request.args.get('success') == 'true':
        # Final safety check: If they are on this URL with the success flag, 
        # it means the frontend confirmed the payment. We can do a final manual upgrade
        # here as a fallback to the webhook/capture.
        upgrade_user_to_pro(current_user.id)
        flash("Success! Your account has been upgraded to Pro.", "success")
        return render_template('payment_success.html')

    # If someone tries to access this URL directly without paying
    flash("Your payment is being processed or was not found. Please check your dashboard.", "info")
    return redirect(url_for('dashboard'))


def upgrade_user_to_pro(user_id):
    """Helper function to upgrade a user to pro status"""
    with app.app_context():
        try:
            user = User.query.get(int(user_id))
            if user:
                user.is_pro = True
                user.pro_expiry = datetime.utcnow() + timedelta(days=30)
                db.session.commit()
                print(f"[INFO] User {user.username} (ID: {user_id}) upgraded to PRO. Expiring on {user.pro_expiry}")
                return True
        except Exception as e:
            print(f"[ERROR] Failed to upgrade user {user_id}: {e}")
            db.session.rollback()
    return False


@app.route('/login', methods=['GET', 'POST'])
def login():
    # If already logged in, redirect to dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Query user from database
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Account is deactivated', 'error')
                return render_template('login.html')
            
            # Update last login time
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Capture 'Remember Me' preference
            remember = True if request.form.get('remember') else False
            
            # Login the user
            login_user(user, remember=remember)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # If already logged in, redirect to dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already taken. Please choose another one.', 'error')
            return render_template('signup.html')
            
        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please login instead.', 'error')
            return render_template('signup.html')
            
        # Create new user
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            
            # Log the user in automatically
            login_user(new_user, remember=True)
            flash('Account created successfully! Welcome aboard.', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {str(e)}', 'error')
            
    return render_template('signup.html')


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_file():
    if request.method == 'GET':
        return redirect(url_for('dashboard'))
        
    # Check conversion limit before processing
    conversion_count = ConversionHistory.query.filter_by(user_id=current_user.id, success=True).count()
    limit = app.config.get('FREE_CONVERSION_LIMIT', 3)
    
    if not current_user.is_pro and conversion_count >= limit:
        flash(f'You have reached your limit of {limit} free conversions. Please contact support to upgrade your account.', 'error')
        return redirect(url_for('dashboard'))

    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    conversion_type = request.form.get('conversion_type')
    custom_prompt = request.form.get('custom_prompt', '')
    
    # Generate unique task ID
    task_id = str(uuid.uuid4())
    
    # Create TaskProgress entry in DB
    new_task = TaskProgress(
        id=task_id,
        status='processing',
        progress=0,
        message='Initializing...'
    )
    db.session.add(new_task)
    db.session.commit()

    # Feature Gating: Check if conversion type is PRO only
    pro_types = app.config.get('PRO_CONVERSION_TYPES', set())
    if conversion_type in pro_types and not current_user.is_pro:
        flash(f'The {conversion_type.title()} conversion is a PRO feature. Please upgrade your account.', 'error')
        return redirect(url_for('dashboard'))

    # Page Limit: Determine max pages based on user tier
    max_pages = None if current_user.is_pro else app.config.get('FREE_PAGE_LIMIT', 5)

    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('dashboard'))

    if file and allowed_file(file.filename):
        ensure_directories()

        # Generate unique filenames
        file_id = str(uuid.uuid4())
        original_filename = secure_filename(file.filename)

        # Create consistent filenames
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_{original_filename}")
        output_filename = f"{file_id}_{original_filename}.xlsx"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

        print(f"[UPLOAD] Upload path: {upload_path}")
        print(f"[OUTPUT] Output path: {output_path}")

        # Save uploaded file
        file.save(upload_path)

        # Check page count for free users
        if not current_user.is_pro:
            try:
                import pdfplumber
                with pdfplumber.open(upload_path) as pdf:
                    page_count = len(pdf.pages)
                    if page_count > app.config.get('FREE_PAGE_LIMIT', 5):
                        os.remove(upload_path)
                        flash(f'Free plan users are limited to PDFs with up to {app.config.get("FREE_PAGE_LIMIT", 5)} pages. Your file has {page_count} pages. Please upgrade to Pro for unlimited pages.', 'error')
                        return redirect(url_for('dashboard'))
            except Exception as e:
                print(f"[ERROR] Failed to check page count: {e}")
                # If we can't check, we let it pass but the converter will still respect the limit

        # Function to run conversion in background
        def run_conversion(task_id, user_id, original_filename, upload_path, output_path, conversion_type, custom_prompt, max_pages):
            with app.app_context():
                try:
                    # Import here to avoid circular dependencies if any
                    from converters.scanned_pdf_converter import extract_data_with_gemini_full
                    from converters.general_pdf_converter import convert_pdf_to_excel
                    from converters.selectable_pdf_converter import extract_pdf_to_excel
                    from converters.custom_pdf_converter import extract_custom_pdf_to_excel

                    success = False
                    message = "Unknown error"
                    
                    # Define progress callback
                    def progress_callback(percent, msg):
                        update_task_progress(task_id, percent, msg)

                    if conversion_type == 'scanned':
                        success, message = extract_data_with_gemini_full(upload_path, output_path, app.config['API_KEY'], max_pages=max_pages, progress_callback=progress_callback)
                    elif conversion_type == 'general':
                        success, message = convert_pdf_to_excel(upload_path, output_path, app.config['API_KEY'], max_pages=max_pages, progress_callback=progress_callback)
                    elif conversion_type == 'selectable':
                        success, message = extract_pdf_to_excel(upload_path, output_path, app.config['API_KEY'], max_pages=max_pages, progress_callback=progress_callback)
                    elif conversion_type == 'custom':
                        success, message = extract_custom_pdf_to_excel(upload_path, output_path, app.config['API_KEY'], custom_prompt, max_pages=max_pages, progress_callback=progress_callback)

                    # Update task status in DB
                    task = TaskProgress.query.get(task_id)
                    if not task:
                        return # Should not happen

                    if success and os.path.exists(output_path):
                        task.status = 'completed'
                        task.progress = 100
                        task.message = message or 'Conversion complete!'
                        task.output_filename = os.path.basename(output_path)
                    else:
                        task.status = 'failed'
                        task.progress = 100 # Reset progress
                        task.message = message or 'Conversion failed.'
                    
                    # Record in conversion history
                    history = ConversionHistory(
                        user_id=user_id,
                        original_filename=original_filename,
                        output_filename=os.path.basename(output_path) if success else None,
                        conversion_type=conversion_type,
                        success=success,
                        error_message=None if success else message
                    )
                    db.session.add(history)
                    db.session.commit()

                except Exception as e:
                    print(f"[ERROR] Background task failed: {e}")
                    # Update task failure in DB
                    try:
                        task = TaskProgress.query.get(task_id)
                        if task:
                            task.status = 'failed'
                            task.message = str(e)
                            db.session.commit()
                    except:
                        pass
                finally:
                    # Cleanup upload
                    if os.path.exists(upload_path):
                        try:
                            os.remove(upload_path)
                        except:
                            pass

        # Start background thread
        thread = threading.Thread(target=run_conversion, args=(task_id, current_user.id, original_filename, upload_path, output_path, conversion_type, custom_prompt, max_pages))
        thread.start()

        # Check if request is AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'task_id': task_id, 'status': 'processing'})

        # Return processing page for standard form submission
        return render_template('processing.html', task_id=task_id)

    return redirect(url_for('dashboard'))


@app.route('/progress/<task_id>')
def get_progress(task_id):
    """Endpoint to get task progress"""
    task = TaskProgress.query.get(task_id)
    if task:
        return jsonify(task.to_dict())
    return jsonify({'status': 'unknown'}), 404


@app.route('/result/<task_id>')
def show_result(task_id):
    """Show result page for a completed task"""
    task = TaskProgress.query.get(task_id)
    
    if not task or task.status != 'completed':
        flash('Result not ready or invalid task', 'error')
        return redirect(url_for('dashboard'))
    
    output_filename = task.output_filename
    return render_template('result.html',
                           download_url=url_for('download_file', filename=output_filename),
                           original_name="Converted File", # Simplified for now
                           excel_filename=output_filename)



@app.route('/download/<filename>')
@login_required
def download_file(filename):
    try:
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        if os.path.exists(file_path):
            download_name = f"converted_{filename.split('_', 1)[1] if '_' in filename else filename}"
            return send_file(file_path, as_attachment=True, download_name=download_name)
        else:
            flash('File not found', 'error')
            return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f'Download error: {str(e)}', 'error')
        return redirect(url_for('dashboard'))


"""
# QuickBooks Routes
@app.route('/quickbooks/setup')
@login_required
def quickbooks_setup():
    # QuickBooks setup page
    qb_connected = session.get('qb_connected', False)
    company_info = session.get('qb_company_info', {})

    return render_template('quickbooks_setup.html',
                           qb_connected=qb_connected,
                           company_info=company_info)


@app.route('/quickbooks/connect')
@login_required
def quickbooks_connect():
    # Start QuickBooks OAuth flow
    try:
        oauth = get_quickbooks_oauth()
        auth_url = oauth.get_authorization_url()
        return redirect(auth_url)
    except Exception as e:
        flash(f'OAuth initialization failed: {str(e)}', 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/callback')
@login_required
def quickbooks_callback():
    # OAuth callback handler
    try:
        authorization_code = request.args.get('code')
        realm_id = request.args.get('realmId')

        if not authorization_code or not realm_id:
            flash('Authorization failed: Missing code or realm ID', 'error')
            return redirect(url_for('quickbooks_setup'))

        # Exchange code for tokens
        oauth = get_quickbooks_oauth()
        tokens = oauth.get_tokens(authorization_code)

        # Store in session
        session['qb_access_token'] = tokens['access_token']
        session['qb_refresh_token'] = tokens['refresh_token']
        session['qb_realm_id'] = realm_id
        session['qb_connected'] = True
        session['qb_token_expires_at'] = time.time() + tokens['expires_in']

        # Test connection and get company info
        qb_service = QuickBooksService()
        qb_service.set_credentials(tokens['access_token'], realm_id)

        # Get company info
        company_info, error = qb_service.get_company_info()
        if company_info and not error:
            session['qb_company_info'] = company_info.get('CompanyInfo', {})
            company_name = company_info.get('CompanyInfo', {}).get('CompanyName', 'Unknown')
            flash(f'✅ Successfully connected to QuickBooks: {company_name}', 'success')
        else:
            flash('✅ Connected to QuickBooks (but could not fetch company info)', 'success')

        return redirect(url_for('quickbooks_setup'))

    except Exception as e:
        flash(f'Connection failed: {str(e)}', 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/disconnect')
@login_required
def quickbooks_disconnect():
    # Disconnect from QuickBooks
    session.pop('qb_access_token', None)
    session.pop('qb_refresh_token', None)
    session.pop('qb_realm_id', None)
    session.pop('qb_connected', None)
    session.pop('qb_company_info', None)
    session.pop('qb_token_expires_at', None)
    flash('Disconnected from QuickBooks', 'success')
    return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/test_connection')
@login_required
def quickbooks_test_connection():
    # Test QuickBooks connection
    try:
        if not session.get('qb_connected'):
            flash('Not connected to QuickBooks', 'error')
            return redirect(url_for('quickbooks_setup'))

        qb_service = QuickBooksService()
        qb_service.set_credentials(
            session.get('qb_access_token'),
            session.get('qb_realm_id')
        )

        success, message = qb_service.test_connection()
        if success:
            flash('✅ Connection test successful!', 'success')
        else:
            flash(f'❌ Connection test failed: {message}', 'error')

        return redirect(url_for('quickbooks_setup'))

    except Exception as e:
        flash(f'Connection test error: {str(e)}', 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/refresh_token')
@login_required
def quickbooks_refresh_token():
    # Manually refresh QuickBooks access token
    try:
        qb_service = QuickBooksService()
        qb_service.set_credentials(
            session.get('qb_access_token'),
            session.get('qb_realm_id')
        )
        success = qb_service.refresh_access_token_if_needed()

        if success:
            flash('✅ Access token refreshed successfully', 'success')
        else:
            flash('❌ Failed to refresh access token', 'error')

        return redirect(url_for('quickbooks_setup'))

    except Exception as e:
        flash(f'Token refresh error: {str(e)}', 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/send_transactions', methods=['POST'])
@login_required
def send_to_quickbooks():
    # Send converted transactions to QuickBooks
    try:
        excel_filename = request.form.get('excel_filename')

        if not session.get('qb_connected'):
            flash('Please connect to QuickBooks first', 'error')
            return redirect(url_for('index'))

        print(f"🔍 Received filename from form: {excel_filename}")

        # Build the full path
        excel_path = os.path.join(app.config['OUTPUT_FOLDER'], excel_filename)
        print(f"🔍 Looking for file at: {excel_path}")

        # Check if file exists
        if not os.path.exists(excel_path):
            print(f"❌ File not found at: {excel_path}")

            # Debug: List all files in outputs directory
            output_dir = app.config['OUTPUT_FOLDER']
            if os.path.exists(output_dir):
                all_files = os.listdir(output_dir)
                print(f"📁 All files in outputs directory: {all_files}")

                # Try to find the file with different patterns
                # Remove .xlsx extension and search
                base_name = excel_filename.replace('.xlsx', '')
                matching_files = [f for f in all_files if base_name in f]
                print(f"🔍 Files matching '{base_name}': {matching_files}")

                if matching_files:
                    # Use the first matching file
                    actual_filename = matching_files[0]
                    excel_path = os.path.join(output_dir, actual_filename)
                    print(f"🔄 Using matched file: {actual_filename}")
                else:
                    flash(f'Excel file not found. Looking for: {excel_filename}', 'error')
                    return redirect(url_for('index'))
            else:
                flash('Outputs directory does not exist', 'error')
                return redirect(url_for('index'))

        print(f"✅ File found: {excel_path}")
        print(f"✅ File exists: {os.path.exists(excel_path)}")
        print(f"✅ File size: {os.path.getsize(excel_path)} bytes")

        # Initialize QuickBooks service
        qb_service = QuickBooksService()
        qb_service.set_credentials(
            session.get('qb_access_token'),
            session.get('qb_realm_id')
        )

        print("🚀 Starting QuickBooks transaction import...")

        # Send transactions
        success, result = qb_service.send_transactions_to_quickbooks(excel_path)

        if success:
            flash(f'✅ {result["summary"]}', 'success')
            # Store detailed results in session to display on results page
            session['qb_results'] = result['details']
            session['qb_success_count'] = result['successful']
            session['qb_failed_count'] = result['failed']
            session['qb_total_processed'] = result['total_processed']
        else:
            flash(f'❌ {result}', 'error')

        return redirect(url_for('quickbooks_results'))

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ QuickBooks integration error: {str(e)}")
        print(f"📄 Error details: {error_details}")
        flash(f'QuickBooks integration error: {str(e)}', 'error')
        return redirect(url_for('index'))


@app.route('/quickbooks/results')
@login_required
def quickbooks_results():
    # Show QuickBooks transaction results
    results = session.pop('qb_results', [])
    success_count = session.pop('qb_success_count', 0)
    failed_count = session.pop('qb_failed_count', 0)
    total_processed = session.pop('qb_total_processed', 0)

    return render_template('quickbooks_results.html',
                           results=results,
                           success_count=success_count,
                           failed_count=failed_count,
                           total_processed=total_processed)


@app.route('/quickbooks/search')
@login_required
def quickbooks_search():
    # Search for transactions in QuickBooks
    try:
        if not session.get('qb_connected'):
            flash('Please connect to QuickBooks first', 'error')
            return redirect(url_for('quickbooks_setup'))

        from quickbooks_search import QuickBooksSearch

        qb_search = QuickBooksSearch(
            session.get('qb_access_token'),
            session.get('qb_realm_id')
        )

        # Get transactions from last 7 days
        transactions = qb_search.search_transactions(days_back=7)

        return render_template('quickbooks_search.html',
                               transactions=transactions,
                               company_info=session.get('qb_company_info', {}))

    except Exception as e:
        flash(f'Search failed: {str(e)}', 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/expenses')
@login_required
def quickbooks_expenses():
    # View expense transactions similar to QuickBooks Expenses page
    try:
        if not session.get('qb_connected'):
            flash('Please connect to QuickBooks first', 'error')
            return redirect(url_for('quickbooks_setup'))

        # Create search instance
        from quickbooks_search import create_search_from_session
        qb_search, error = create_search_from_session(session)

        if error:
            flash(f'QuickBooks error: {error}', 'error')
            return redirect(url_for('quickbooks_setup'))

        # Get expense transactions with summary and pagination
        result = qb_search.get_expense_summary(
            days_back=90,
            max_results=1000,
            top_vendors_count=3,  # Show 3 in top vendors list
            chart_vendors_count=10  # Show 10 in pie chart
        )

        if 'error' in result:
            flash(f'Error loading expenses: {result["error"]}', 'error')
            return redirect(url_for('quickbooks_setup'))

        # Get query info for debugging
        query_info = result.get('query_info', {})
        actual_results = query_info.get('actual_results', 0)

        print(f"📊 Expense Results: {actual_results} transactions loaded")

        # Show info message if we hit the limit
        if actual_results >= 1000:
            flash(f'Showing {actual_results} most recent transactions. Some older transactions may not be displayed.',
                  'info')

        return render_template('quickbooks_expenses.html',
                               transactions=result.get('transactions', []),
                               company_info=result.get('company', {}),
                               summary=result.get('summary', {}),
                               total_count=result.get('total_count', 0),
                               query_info=query_info,
                               error=None)

    except Exception as e:
        error_msg = f'Failed to load expenses: {str(e)}'
        print(f"❌ {error_msg}")
        flash(error_msg, 'error')
        return redirect(url_for('quickbooks_setup'))


@app.route('/quickbooks/debug-deleted')
@login_required
def quickbooks_debug_deleted():
    # Debug route to see deleted transactions
    try:
        if not session.get('qb_connected'):
            return jsonify({'error': 'Not connected to QuickBooks'})

        from quickbooks_search import create_search_from_session
        qb_search, error = create_search_from_session(session)

        if error:
            return jsonify({'error': error})

        # Get deleted transactions
        deleted_transactions = qb_search.get_deleted_transactions(days_back=90)

        return jsonify({
            'deleted_count': len(deleted_transactions),
            'deleted_transactions': deleted_transactions
        })

    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/quickbooks/transactions')
@login_required
def quickbooks_transactions():
    # Legacy route - redirect to expenses
    return redirect(url_for('quickbooks_expenses'))


@app.route('/quickbooks/debug-response')
@login_required
def quickbooks_debug_response():
    # Debug route to see actual API response structure
    try:
        if not session.get('qb_connected'):
            return jsonify({'error': 'Not connected to QuickBooks'})

        from quickbooks_search import create_search_from_session
        qb_search, error = create_search_from_session(session)

        if error:
            return jsonify({'error': error})

        # Get debug response
        data, error = qb_search.debug_api_response()

        if error:
            return jsonify({'error': error})

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/quickbooks/reconnect')
@login_required
def quickbooks_reconnect():
    # Force reconnection to QuickBooks
    session.pop('qb_access_token', None)
    session.pop('qb_refresh_token', None)
    session.pop('qb_realm_id', None)
    session.pop('qb_connected', None)
    session.pop('qb_company_info', None)
    session.pop('qb_token_expires_at', None)
    flash('Please reconnect to QuickBooks with fresh credentials', 'info')
    return redirect(url_for('quickbooks_connect'))
"""
# Note: QuickBooks routes have been commented out by request.
# To restore, remove the triple quotes above and below this section.



if __name__ == '__main__':
    ensure_directories()
    
    # Initialize database tables
    with app.app_context():
        db.create_all()
        
        # Create default admin user if not exists
        from database import create_default_admin
        create_default_admin()
    
    debug_mode = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    app.run(debug=debug_mode)