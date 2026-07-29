import json
import logging
import os
import secrets

import requests
from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from flask_login import login_user
from oauthlib.oauth2 import WebApplicationClient

from datetime import datetime

from models import User, db

log = logging.getLogger(__name__)

# oauthlib refuses to run OAuth over plain HTTP. That is the correct default:
# relax it only for local development, never in production. This used to be
# set unconditionally at import time, in every environment.
if os.environ.get('FLASK_DEBUG', 'False').lower() == 'true':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

auth_bp = Blueprint('auth', __name__)

_STATE_KEY = 'oauth_state'


def get_google_provider_cfg():
    return requests.get(current_app.config['GOOGLE_DISCOVERY_URL'], timeout=15).json()


def _redirect_uri():
    uri = url_for('auth.google_callback', _external=True)
    if uri.startswith('http://') and 'localhost' not in uri and '127.0.0.1' not in uri:
        uri = uri.replace('http://', 'https://', 1)
    return uri


@auth_bp.route('/google')
def google_login():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        flash('Google sign-in is not configured.', 'error')
        return redirect(url_for('login'))

    try:
        authorization_endpoint = get_google_provider_cfg()['authorization_endpoint']
    except Exception:
        log.exception("Could not reach Google's discovery document")
        flash('Google sign-in is unavailable right now.', 'error')
        return redirect(url_for('login'))

    # Single-use CSRF token for the round trip. Without it, an attacker can
    # feed the callback their own authorization code and land the victim in
    # the attacker's account.
    state = secrets.token_urlsafe(32)
    session[_STATE_KEY] = state

    client = WebApplicationClient(current_app.config['GOOGLE_CLIENT_ID'])
    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=_redirect_uri(),
        scope=['openid', 'email', 'profile'],
        state=state,
    )
    return redirect(request_uri)


@auth_bp.route('/google/callback')
def google_callback():
    expected = session.pop(_STATE_KEY, None)
    received = request.args.get('state')
    if not expected or not received or not secrets.compare_digest(expected, received):
        log.warning("OAuth callback rejected: state mismatch")
        flash('That sign-in link has expired. Please try again.', 'error')
        return redirect(url_for('login'))

    code = request.args.get('code')
    if not code:
        flash('Google did not return an authorization code.', 'error')
        return redirect(url_for('login'))

    try:
        provider_cfg = get_google_provider_cfg()
        client = WebApplicationClient(current_app.config['GOOGLE_CLIENT_ID'])

        token_url, headers, body = client.prepare_token_request(
            provider_cfg['token_endpoint'],
            authorization_response=request.url,
            redirect_url=_redirect_uri(),
            code=code,
        )

        token_response = requests.post(
            token_url, headers=headers, data=body, timeout=20,
            auth=(current_app.config['GOOGLE_CLIENT_ID'],
                  current_app.config['GOOGLE_CLIENT_SECRET']),
        )

        if token_response.status_code != 200:
            # Never echo the provider's response body back to the browser.
            log.error("Google token exchange failed: %s", token_response.status_code)
            flash('Google sign-in failed. Please try again.', 'error')
            return redirect(url_for('login'))

        client.parse_request_body_response(json.dumps(token_response.json()))
        uri, headers, body = client.add_token(provider_cfg['userinfo_endpoint'])
        userinfo = requests.get(uri, headers=headers, timeout=20).json()
    except Exception:
        log.exception("Google sign-in failed")
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('login'))

    if not userinfo.get('email_verified'):
        flash('Your Google account does not have a verified email address.', 'error')
        return redirect(url_for('login'))

    google_id = userinfo['sub']
    email = (userinfo.get('email') or '').lower()
    name = userinfo.get('given_name', '')
    picture = userinfo.get('picture', '')

    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first() if email else None
        if user:
            user.google_id = google_id
        else:
            user = User(google_id=google_id, email=email, username=email or google_id,
                        name=name, picture_url=picture)
            db.session.add(user)

    user.name = name
    user.picture_url = picture
    user.last_login = datetime.utcnow()
    db.session.commit()

    login_user(user, remember=True)
    log.info("Google sign-in succeeded for user %s", user.id)
    return redirect(url_for('dashboard'))
