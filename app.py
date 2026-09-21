import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

import requests
import stripe
from dotenv import load_dotenv
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)
from flask_wtf.csrf import CSRFProtect
from requests.auth import HTTPBasicAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

load_dotenv()

from auth import auth_bp
from config import Config
from models import ConversionHistory, TaskProgress, User, db

# --------------------------------------------------------------- logging ---

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
)
log = logging.getLogger('pdf_converter')

# ------------------------------------------------------------------ app ----

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.from_object(Config)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1 GB

if not app.config.get('SECRET_KEY'):
    raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")

db.init_app(app)
stripe.api_key = app.config['STRIPE_SECRET_KEY']

csrf = CSRFProtect(app)

# Rate limiting. Falls back to in-memory storage when no Redis URL is set,
# which is per-worker rather than global but still blunts credential stuffing.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        get_remote_address,
        app=app,
        storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
        default_limits=[],
    )
except ImportError:  # pragma: no cover
    limiter = None
    log.warning("flask-limiter is not installed; auth routes are unthrottled.")


def rate_limit(rule):
    """Apply a limit only when the extension is available."""
    def decorator(fn):
        return limiter.limit(rule)(fn) if limiter else fn
    return decorator


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to continue.'
login_manager.login_message_category = 'error'
login_manager.session_protection = 'basic'

app.register_blueprint(auth_bp, url_prefix='/auth')


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user and user.is_pro and user.pro_expiry and datetime.utcnow() > user.pro_expiry:
        user.is_pro = False
        db.session.commit()
        log.info("Pro subscription expired for user %s", user.id)
    return user


@app.context_processor
def inject_globals():
    return {'now': datetime.utcnow()}


def file_extension(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''


def allowed_file(filename, conversion_type=None):
    """Every mode accepts PDFs and image files."""
    permitted = (app.config['CHECK_UPLOAD_EXTENSIONS'] if conversion_type == 'checks'
                 else app.config['DOCUMENT_UPLOAD_EXTENSIONS'])
    return file_extension(filename) in permitted


def count_pages(path):
    """
    Number of pages in an uploaded document, or None when it cannot be read.

    A PDF is counted with pdfplumber; an image counts its frames, so a
    multi-page TIFF is charged for what it actually contains.
    """
    extension = file_extension(path)
    try:
        if extension in app.config['ALLOWED_IMAGE_EXTENSIONS']:
            from PIL import Image
            with Image.open(path) as image:
                return getattr(image, 'n_frames', 1)
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def safe_relative_path(raw):
    """
    Turn a browser-supplied relative path into a safe one.

    Folder uploads report paths like "49 UNITED INC/JUNE 2026/1361.png". Each
    segment is sanitised individually so the company and month survive while
    traversal ("../") cannot.
    """
    if not raw:
        return None
    segments = []
    for segment in str(raw).replace('\\', '/').split('/'):
        segment = secure_filename(segment)
        if segment and segment not in ('.', '..'):
            segments.append(segment)
    return '/'.join(segments) if segments else None


def ensure_directories():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)


def update_task_progress(task_id, progress, message):
    with app.app_context():
        try:
            task = db.session.get(TaskProgress, task_id)
            if task:
                task.progress = int(progress)
                task.message = message[:255]
                db.session.commit()
        except Exception:
            log.exception("Could not update progress for task %s", task_id)
            db.session.rollback()


def owned_task_or_404(task_id):
    """
    Fetch a task, or abort, unless it belongs to the current user.

    These routes previously had no ownership check at all, so any user could
    read another user's conversion — and their bank statements — by guessing
    a task id or an output filename.
    """
    task = db.session.get(TaskProgress, task_id)
    if task is None:
        abort(404)
    if task.user_id is not None and task.user_id != current_user.id:
        log.warning("User %s attempted to access task %s owned by %s",
                    current_user.id, task_id, task.user_id)
        abort(404)
    return task


# ---------------------------------------------------------------- pages ----

@app.route('/')
@app.route('/landing')
def index():
    return render_template('landing.html', is_landing=True)


@app.route('/app')
@login_required
def dashboard():
    conversion_count = ConversionHistory.query.filter_by(
        user_id=current_user.id, success=True).count()
    return render_template(
        'index.html',
        conversion_count=conversion_count,
        conversion_limit=app.config.get('FREE_CONVERSION_LIMIT', 3),
        free_page_limit=app.config.get('FREE_PAGE_LIMIT', 5),
        checks_enabled=app.config.get('CHECKS_ENABLED', False),
        max_check_files=app.config.get('MAX_CHECK_FILES', 300),
        is_pro=current_user.is_pro,
        pro_expiry=current_user.pro_expiry,
    )


# ----------------------------------------------------------------- auth ----

@app.route('/login', methods=['GET', 'POST'])
@rate_limit("10 per minute; 40 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('That account has been deactivated.', 'error')
                return render_template('login.html')

            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user, remember=bool(request.form.get('remember')))
            log.info("Login succeeded for user %s", user.id)
            return redirect(url_for('dashboard'))

        # Identical message either way — never reveal which half was wrong.
        log.info("Login failed for username %r", username[:64])
        flash('That username and password do not match.', 'error')

    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
@rate_limit("5 per minute; 20 per hour")
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        if len(username) < 3:
            flash('Pick a username of at least 3 characters.', 'error')
            return render_template('signup.html')
        if len(password) < 8:
            flash('Use a password of at least 8 characters.', 'error')
            return render_template('signup.html')
        if User.query.filter_by(username=username).first():
            flash('That username is taken. Try another.', 'error')
            return render_template('signup.html')
        if email and User.query.filter_by(email=email).first():
            flash('That email is already registered. Log in instead.', 'error')
            return render_template('signup.html')

        user = User(username=username, email=email)
        user.set_password(password)
        try:
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            log.info("Account created: user %s", user.id)
            flash('Welcome aboard.', 'success')
            return redirect(url_for('dashboard'))
        except Exception:
            db.session.rollback()
            log.exception("Signup failed")
            flash('Could not create the account. Try again.', 'error')

    return render_template('signup.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


# -------------------------------------------------------------- payments ---

@app.route('/cart')
@login_required
def cart():
    if current_user.is_pro:
        flash('You are already on the Professional plan.', 'info')
        return redirect(url_for('dashboard'))
    return render_template('cart.html',
                           price=app.config['PRO_PLAN_PRICE'],
                           paypal_client_id=app.config['PAYPAL_CLIENT_ID'])


@app.route('/create-checkout-session')
@login_required
def create_checkout_session():
    try:
        checkout = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Professional Plan - PDF Converter',
                        'description': 'Unlimited conversions and advanced features',
                    },
                    'unit_amount': int(app.config['PRO_PLAN_PRICE'] * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', session_id='{CHECKOUT_SESSION_ID}', _external=True),
            cancel_url=url_for('cart', _external=True),
            client_reference_id=str(current_user.id),
            customer_email=current_user.email,
        )
        return redirect(checkout.url, code=303)
    except Exception:
        log.exception("Stripe checkout session failed")
        flash('Could not start checkout. Try again in a moment.', 'error')
        return redirect(url_for('cart'))


@app.route('/stripe-webhook', methods=['POST'])
@csrf.exempt  # signed by Stripe, verified below
def stripe_webhook():
    payload = request.get_data()
    signature = request.headers.get('Stripe-Signature')
    secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        log.warning("Stripe webhook with a bad signature was rejected")
        return 'Invalid signature', 400

    if event['type'] == 'checkout.session.completed':
        checkout = event['data']['object']
        if checkout.get('payment_status') == 'paid':
            user_id = checkout.get('client_reference_id')
            if user_id:
                upgrade_user_to_pro(user_id)

    return 'Success', 200


@app.route('/paypal/create-order', methods=['POST'])
@login_required
def create_paypal_order():
    base = ("https://api-m.sandbox.paypal.com" if app.config['PAYPAL_MODE'] == 'sandbox'
            else "https://api-m.paypal.com")
    auth = HTTPBasicAuth(app.config['PAYPAL_CLIENT_ID'], app.config['PAYPAL_SECRET'])

    token_response = requests.post(f"{base}/v1/oauth2/token", auth=auth,
                                   data={'grant_type': 'client_credentials'}, timeout=30)
    if token_response.status_code != 200:
        log.error("PayPal token request failed: %s", token_response.status_code)
        return jsonify({'error': 'Could not reach PayPal'}), 502

    order = requests.post(
        f"{base}/v2/checkout/orders",
        headers={'Authorization': f"Bearer {token_response.json().get('access_token')}",
                 'Content-Type': 'application/json'},
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": "USD", "value": f"{app.config['PRO_PLAN_PRICE']:.2f}"},
                "description": "Professional Plan - PDF Converter",
                "custom_id": str(current_user.id),
            }],
        },
        timeout=30,
    )
    return jsonify(order.json())


@app.route('/paypal/capture-order', methods=['POST'])
@login_required
def capture_paypal_order():
    order_id = (request.json or {}).get('orderID')
    if not order_id:
        return jsonify({'error': 'Missing order id'}), 400

    base = ("https://api-m.sandbox.paypal.com" if app.config['PAYPAL_MODE'] == 'sandbox'
            else "https://api-m.paypal.com")
    auth = HTTPBasicAuth(app.config['PAYPAL_CLIENT_ID'], app.config['PAYPAL_SECRET'])

    token_response = requests.post(f"{base}/v1/oauth2/token", auth=auth,
                                   data={'grant_type': 'client_credentials'}, timeout=30)
    if token_response.status_code != 200:
        return jsonify({'error': 'Could not reach PayPal'}), 502

    capture = requests.post(
        f"{base}/v2/checkout/orders/{order_id}/capture",
        headers={'Authorization': f"Bearer {token_response.json().get('access_token')}",
                 'Content-Type': 'application/json'},
        timeout=30,
    )
    data = capture.json()

    # Trust PayPal's own report of the capture, not the browser's.
    if data.get('status') == 'COMPLETED':
        upgrade_user_to_pro(current_user.id)
        log.info("PayPal capture completed for user %s", current_user.id)

    return jsonify(data)


@app.route('/payment-success')
@login_required
def payment_success():
    """
    Confirmation page.

    The upgrade itself happens in the Stripe webhook or the PayPal capture.
    This route used to upgrade the account whenever it saw ?success=true in
    the query string, which meant any logged-in user could grant themselves a
    subscription by visiting the URL. It now verifies with Stripe directly.
    """
    user = db.session.get(User, current_user.id)
    db.session.refresh(user)

    if not user.is_pro:
        checkout_id = request.args.get('session_id')
        if checkout_id:
            try:
                checkout = stripe.checkout.Session.retrieve(checkout_id)
                paid = checkout.get('payment_status') == 'paid'
                belongs = str(checkout.get('client_reference_id')) == str(user.id)
                if paid and belongs:
                    upgrade_user_to_pro(user.id)
                    db.session.refresh(user)
                else:
                    log.warning("payment-success for unpaid or mismatched session %s", checkout_id)
            except Exception:
                log.exception("Could not verify Stripe session %s", checkout_id)

    if not user.is_pro:
        flash('We have not received confirmation of your payment yet. '
              'If you have been charged, it will appear here shortly.', 'info')
        return redirect(url_for('dashboard'))

    return render_template('payment_success.html')


def upgrade_user_to_pro(user_id):
    try:
        user = db.session.get(User, int(user_id))
        if not user:
            return False
        user.is_pro = True
        base = max(user.pro_expiry or datetime.utcnow(), datetime.utcnow())
        user.pro_expiry = base + timedelta(days=30)
        db.session.commit()
        log.info("User %s upgraded to Pro until %s", user_id, user.pro_expiry)
        return True
    except Exception:
        log.exception("Could not upgrade user %s", user_id)
        db.session.rollback()
        return False


# --------------------------------------------------------------- convert ---

@app.route('/upload', methods=['GET', 'POST'])
@login_required
@rate_limit("20 per hour")
def upload_file():
    if request.method == 'GET':
        return redirect(url_for('dashboard'))

    def fail(message):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('dashboard'))

    conversion_count = ConversionHistory.query.filter_by(
        user_id=current_user.id, success=True).count()
    limit = app.config.get('FREE_CONVERSION_LIMIT', 3)

    if not current_user.is_pro and conversion_count >= limit:
        return fail(f'You have used all {limit} free conversions. Upgrade to continue.')

    conversion_type = request.form.get('conversion_type')
    valid_modes = {'scanned', 'selectable', 'general', 'custom'}
    if app.config.get('CHECKS_ENABLED'):
        valid_modes.add('checks')
    if conversion_type not in valid_modes:
        return fail('Pick a conversion mode.')

    pro_types = app.config.get('PRO_CONVERSION_TYPES', set())
    if conversion_type in pro_types and not current_user.is_pro:
        return fail(f'{conversion_type.title()} mode is part of the Professional plan.')

    custom_prompt = (request.form.get('custom_prompt') or '').strip()
    if conversion_type == 'custom' and not custom_prompt:
        return fail('Describe what you want extracted.')

    # Checks mode takes a batch; every other mode takes one PDF.
    uploaded = [f for f in request.files.getlist('file') if f and f.filename]
    if not uploaded:
        return fail('Choose a file to convert.')

    if conversion_type == 'checks':
        limit = app.config.get('MAX_CHECK_FILES', 300)
        if len(uploaded) > limit:
            return fail(f'That is {len(uploaded)} files. Please upload at most {limit} at a time.')
    else:
        uploaded = uploaded[:1]

    for item in uploaded:
        if not allowed_file(item.filename, conversion_type):
            return fail(f'{item.filename} is not supported. '
                        f'Only PDFs and image files can be converted.')

    ensure_directories()

    file_id = str(uuid.uuid4())
    max_pages = None if current_user.is_pro else app.config.get('FREE_PAGE_LIMIT', 5)

    if conversion_type == 'checks':
        # A batch keeps its own directory so the folder structure a user
        # uploaded (company / month / check) survives to the extractor.
        if not current_user.is_pro and len(uploaded) > max_pages:
            return fail(
                f'The free plan converts up to {max_pages} checks at a time. '
                f'You selected {len(uploaded)}. Upgrade for unlimited batches.'
            )

        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        os.makedirs(upload_path, exist_ok=True)

        raw_paths = request.form.getlist('relative_paths')
        saved = 0
        for index, item in enumerate(uploaded):
            relative = safe_relative_path(raw_paths[index]) if index < len(raw_paths) else None
            relative = relative or secure_filename(item.filename)
            if not relative:
                continue
            destination = os.path.join(upload_path, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            item.save(destination)
            saved += 1

        if not saved:
            return fail('None of those files could be read.')

        original_filename = (f"{saved} checks" if saved > 1
                             else secure_filename(uploaded[0].filename))
        output_filename = f"{file_id}_checks.xlsx"
        log.info("Checks batch %s: %d file(s) staged", file_id[:8], saved)

    else:
        file = uploaded[0]
        original_filename = secure_filename(file.filename) or 'document'
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_{original_filename}")
        output_filename = f"{file_id}_{os.path.splitext(original_filename)[0]}.xlsx"
        file.save(upload_path)

        if not current_user.is_pro:
            pages = count_pages(upload_path)
            if pages is None:
                log.warning("Could not count pages in %s; converter will apply the limit.",
                            original_filename)
            elif pages > max_pages:
                os.remove(upload_path)
                return fail(
                    f'The free plan reads up to {max_pages} pages. This file has {pages}. '
                    f'Upgrade for unlimited pages.'
                )

    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    task_id = str(uuid.uuid4())
    db.session.add(TaskProgress(
        id=task_id,
        user_id=current_user.id,
        original_filename=original_filename,
        status='processing',
        progress=0,
        message='Starting…',
    ))
    db.session.commit()

    thread = threading.Thread(
        target=run_conversion,
        args=(task_id, current_user.id, original_filename, upload_path,
              output_path, conversion_type, custom_prompt, max_pages),
        daemon=True,
    )
    thread.start()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'task_id': task_id, 'status': 'processing'})
    return render_template('processing.html', task_id=task_id)


def run_conversion(task_id, user_id, original_filename, upload_path,
                   output_path, conversion_type, custom_prompt, max_pages):
    with app.app_context():
        from converters.pipeline import convert, convert_custom

        success, message, summary = False, 'Conversion failed.', None
        started = time.time()

        def progress(percent, note):
            update_task_progress(task_id, percent, note)

        try:
            log.info("[%s] %s mode, file=%s", task_id[:8], conversion_type, original_filename)
            if conversion_type == 'checks':
                from converters.checks_converter import convert_checks
                success, message, summary = convert_checks(
                    upload_path, output_path, app.config['API_KEY'],
                    progress_callback=progress)
            elif conversion_type == 'custom':
                success, message, summary = convert_custom(
                    upload_path, output_path, app.config['API_KEY'], custom_prompt,
                    max_pages=max_pages, progress_callback=progress)
            else:
                success, message, summary = convert(
                    upload_path, output_path, app.config['API_KEY'],
                    mode=conversion_type, max_pages=max_pages, progress_callback=progress)
        except Exception as exc:
            log.exception("[%s] conversion crashed", task_id[:8])
            message = f'The conversion failed: {exc}'

        try:
            task = db.session.get(TaskProgress, task_id)
            if task:
                done = success and os.path.exists(output_path)
                task.status = 'completed' if done else 'failed'
                task.progress = 100
                task.message = (message or '')[:255]
                task.output_filename = os.path.basename(output_path) if done else None
                if summary:
                    task.row_count = summary.get('rows')
                    task.flagged_count = summary.get('flagged')

            db.session.add(ConversionHistory(
                user_id=user_id,
                original_filename=original_filename,
                output_filename=os.path.basename(output_path) if success else None,
                conversion_type=conversion_type,
                success=bool(success),
                error_message=None if success else message,
            ))
            db.session.commit()
            log.info("[%s] %s in %.1fs — %s", task_id[:8],
                     'completed' if success else 'failed', time.time() - started, message)
        except Exception:
            log.exception("[%s] could not record result", task_id[:8])
            db.session.rollback()
        finally:
            # A checks batch is a directory; every other mode is one file.
            try:
                if os.path.isdir(upload_path):
                    import shutil
                    shutil.rmtree(upload_path, ignore_errors=True)
                elif os.path.exists(upload_path):
                    os.remove(upload_path)
            except OSError:
                log.warning("Could not delete upload %s", upload_path)


@app.route('/progress/<task_id>')
@login_required
def get_progress(task_id):
    task = owned_task_or_404(task_id)
    return jsonify(task.to_dict())


@app.route('/result/<task_id>')
@login_required
def show_result(task_id):
    task = owned_task_or_404(task_id)
    if task.status != 'completed' or not task.output_filename:
        flash('That conversion is not ready yet.', 'error')
        return redirect(url_for('dashboard'))

    return render_template(
        'result.html',
        task_id=task.id,
        download_url=url_for('download_file', task_id=task.id),
        original_name=task.original_filename or 'your document',
        excel_filename=task.output_filename,
        row_count=task.row_count,
        flagged_count=task.flagged_count,
    )


@app.route('/download/<task_id>')
@login_required
def download_file(task_id):
    """Serve the workbook by task id, so ownership is always checked."""
    task = owned_task_or_404(task_id)
    if not task.output_filename:
        abort(404)

    path = os.path.join(app.config['OUTPUT_FOLDER'], task.output_filename)
    if not os.path.exists(path):
        flash('That file has expired and is no longer available.', 'error')
        return redirect(url_for('dashboard'))

    base = os.path.splitext(task.original_filename or 'converted')[0]
    return send_file(path, as_attachment=True, download_name=f"{base}.xlsx")


# ------------------------------------------------------ review & correct ---

@app.route('/api/rows/<task_id>', methods=['GET'])
@login_required
def get_rows(task_id):
    """Return the extracted rows so they can be reviewed before download."""
    task = owned_task_or_404(task_id)
    if task.status != 'completed' or not task.output_filename:
        abort(404)

    path = os.path.join(app.config['OUTPUT_FOLDER'], task.output_filename)
    if not os.path.exists(path):
        abort(404)

    try:
        from openpyxl import load_workbook
        workbook = load_workbook(path, data_only=True)

        # Pick the data sheet: Checks first, then Transactions, then first.
        sheet_name = None
        for candidate in ('Checks', 'Transactions'):
            if candidate in workbook.sheetnames:
                sheet_name = candidate
                break
        if sheet_name is None:
            return jsonify({'editable': False, 'rows': [], 'columns': []})

        sheet = workbook[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return jsonify({'editable': True, 'rows': [], 'columns': []})

        headers = [str(h) if h is not None else '' for h in rows[0]]

        # Separate data columns from the Review column
        data_columns = [h for h in headers if h.lower() != 'review']

        payload = []
        for values in rows[1:]:
            record = {}
            for i in range(min(len(headers), len(values))):
                record[headers[i]] = values[i]
            # Build the response row with all data columns
            row_data = {}
            for col in data_columns:
                row_data[col] = record.get(col) if record.get(col) is not None else ''
            row_data['Review'] = record.get('Review') or ''
            payload.append(row_data)

        return jsonify({
            'editable': True,
            'columns': data_columns,
            'rows': payload,
            'flagged': task.flagged_count or 0,
            'sheet_type': 'checks' if sheet_name == 'Checks' else 'transactions',
        })
    except Exception:
        log.exception("Could not read workbook for task %s", task_id)
        abort(500)


@app.route('/api/rows/<task_id>', methods=['POST'])
@login_required
def save_rows(task_id):
    """Rewrite the workbook from user-corrected rows and re-run validation."""
    task = owned_task_or_404(task_id)
    if task.status != 'completed' or not task.output_filename:
        abort(404)

    submitted = (request.json or {}).get('rows')
    sheet_type = (request.json or {}).get('sheet_type', 'transactions')
    if not isinstance(submitted, list):
        return jsonify({'error': 'Expected a list of rows.'}), 400
    if len(submitted) > 20000:
        return jsonify({'error': 'That is more rows than we can save.'}), 400

    path = os.path.join(app.config['OUTPUT_FOLDER'], task.output_filename)

    if sheet_type == 'checks':
        from converters.checks_converter import (
            CHECK_COLUMNS, validate_checks, write_checks_workbook,
        )
        from converters import common

        rows = []
        for index, item in enumerate(submitted):
            if not isinstance(item, dict):
                continue
            row = {}
            for col in CHECK_COLUMNS:
                val = item.get(col, '')
                if col == 'courtesy_amount':
                    val = common.as_amount(val)
                else:
                    val = common.as_text(val) if not isinstance(val, (int, float)) else val
                row[col] = val
            row['_source_file'] = ''
            row['_flags'] = []
            rows.append(row)

        checked, flagged = validate_checks(rows)
        total_amount = sum(
            r['courtesy_amount'] for r in rows
            if isinstance(r.get('courtesy_amount'), (int, float))
        )
        summary = {
            'rows': len(rows),
            'total_amount': total_amount,
            'flagged': flagged,
            'failed_files': [],
            'checked': checked,
        }

        try:
            write_checks_workbook(rows, path, summary)
        except Exception:
            log.exception("Could not rewrite checks workbook for task %s", task_id)
            return jsonify({'error': 'Could not save your changes.'}), 500

    else:
        from converters import common

        rows = []
        for index, item in enumerate(submitted):
            if not isinstance(item, dict):
                continue
            rows.append({
                'Date': common.as_text(item.get('Date'))[:64],
                'Description': common.as_text(item.get('Description'))[:500],
                'Debit': common.as_amount(item.get('Debit')),
                'Credit': common.as_amount(item.get('Credit')),
                'Balance': common.as_amount(item.get('Balance')),
                '_page': 0,
                '_row': index,
                '_flags': [],
            })

        checked, flagged = common.reconcile(rows)
        summary = common.summarise(rows, checked, flagged, 0)

        try:
            common.write_workbook(rows, path, summary)
        except Exception:
            log.exception("Could not rewrite workbook for task %s", task_id)
            return jsonify({'error': 'Could not save your changes.'}), 500

    task.row_count = summary['rows']
    task.flagged_count = summary['flagged']
    db.session.commit()
    log.info("[%s] user saved %d corrected rows, %d still flagged",
             task_id[:8], summary['rows'], summary['flagged'])

    return jsonify({'saved': True, 'rows': summary['rows'], 'flagged': summary['flagged']})


# ----------------------------------------------------------- maintenance ---

def purge_expired_outputs():
    """Delete workbooks and task rows past the retention window."""
    hours = app.config.get('OUTPUT_RETENTION_HOURS', 48)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    removed_files = 0

    with app.app_context():
        try:
            for task in TaskProgress.query.filter(TaskProgress.created_at < cutoff).all():
                if task.output_filename:
                    path = os.path.join(app.config['OUTPUT_FOLDER'], task.output_filename)
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                            removed_files += 1
                        except OSError:
                            pass
                db.session.delete(task)
            db.session.commit()
        except Exception:
            log.exception("Retention purge failed")
            db.session.rollback()
            return

        # Uploads are removed after each conversion; anything left is an orphan.
        # Checks batches are directories, so sweep both shapes.
        upload_dir = app.config['UPLOAD_FOLDER']
        if os.path.isdir(upload_dir):
            import shutil
            for name in os.listdir(upload_dir):
                path = os.path.join(upload_dir, name)
                try:
                    if datetime.utcfromtimestamp(os.path.getmtime(path)) >= cutoff:
                        continue
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                        removed_files += 1
                    elif os.path.isfile(path):
                        os.remove(path)
                        removed_files += 1
                except OSError:
                    pass

    if removed_files:
        log.info("Retention purge removed %d file(s) older than %dh", removed_files, hours)


def fail_orphaned_tasks():
    """
    Mark tasks abandoned by a restart as failed.

    Conversions run in a plain thread, so a deploy kills anything in flight and
    the browser would otherwise poll a 'processing' row forever.
    """
    cutoff = datetime.utcnow() - timedelta(hours=2)
    with app.app_context():
        try:
            stale = TaskProgress.query.filter(
                TaskProgress.status == 'processing',
                TaskProgress.updated_at < cutoff,
            ).all()
            for task in stale:
                task.status = 'failed'
                task.message = 'The conversion was interrupted. Please try again.'
            if stale:
                db.session.commit()
                log.info("Marked %d interrupted task(s) as failed", len(stale))
        except Exception:
            log.exception("Could not reconcile interrupted tasks")
            db.session.rollback()


def start_maintenance_thread():
    def loop():
        # Stagger workers so three Gunicorn processes don't purge simultaneously.
        time.sleep(30 + (os.getpid() % 60))
        while True:
            fail_orphaned_tasks()
            purge_expired_outputs()
            time.sleep(3600)

    threading.Thread(target=loop, daemon=True).start()


if os.environ.get('ENABLE_MAINTENANCE', 'true').lower() == 'true':
    start_maintenance_thread()


# --------------------------------------------------------------- errors ----

@app.errorhandler(404)
def not_found(_):
    return render_template('error.html',
                           code=404,
                           title='Page not found',
                           detail='That page does not exist, or it is not yours to view.'), 404


@app.errorhandler(413)
def too_large(_):
    return render_template('error.html',
                           code=413,
                           title='That file is too large',
                           detail='The upload exceeded the size limit.'), 413


@app.errorhandler(500)
def server_error(_):
    db.session.rollback()
    return render_template('error.html',
                           code=500,
                           title='Something went wrong',
                           detail='The error has been logged. Please try again.'), 500


if __name__ == '__main__':
    ensure_directories()
    with app.app_context():
        db.create_all()
        from database import create_admin_from_env
        create_admin_from_env()

    app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true')
