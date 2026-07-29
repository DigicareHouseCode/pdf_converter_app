import os
# ⚠️ Only for LOCAL testing - MUST be set before oauthlib imports
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

import json
import requests
from flask import Blueprint, redirect, request, url_for, current_app
from flask_login import login_user
from oauthlib.oauth2 import WebApplicationClient
from models import db, User
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

def get_google_provider_cfg():
    return requests.get(current_app.config['GOOGLE_DISCOVERY_URL']).json()


# =========================
# GOOGLE LOGIN
# =========================
@auth_bp.route("/google")
def google_login():
    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    client = WebApplicationClient(current_app.config['GOOGLE_CLIENT_ID'])

    redirect_uri = url_for("auth.google_callback", _external=True)

    # Force HTTPS only on production (non-local) environments
    if redirect_uri.startswith("http://") and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://", 1)
    
    print(f"\n[OAUTH DEBUG] Redirecting to Google with URI: {redirect_uri}")

    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=redirect_uri,
        scope=["openid", "email", "profile"],
    )

    return redirect(request_uri)


# =========================
# GOOGLE CALLBACK
# =========================
@auth_bp.route("/google/callback")
def google_callback():
    code = request.args.get("code")
    if not code:
        return "Missing authorization code", 400

    google_provider_cfg = get_google_provider_cfg()
    token_endpoint = google_provider_cfg["token_endpoint"]

    client = WebApplicationClient(current_app.config['GOOGLE_CLIENT_ID'])

    redirect_uri = url_for("auth.google_callback", _external=True)
    # Force HTTPS only on production (non-local) environments
    if redirect_uri.startswith("http://") and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://", 1)

    print(f"[DEBUG] Using Redirect URI: {redirect_uri}")

    # Prepare the token request
    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url,
        redirect_url=redirect_uri,
        code=code
    )

    # Send token request with Basic Auth
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(
            current_app.config['GOOGLE_CLIENT_ID'],
            current_app.config['GOOGLE_CLIENT_SECRET'],
        ),
    )

    # Debug: Check for errors in response
    if token_response.status_code != 200:
        print(f"[DEBUG] Google Token Error Status: {token_response.status_code}")
        print(f"[DEBUG] Google Token Error Body: {token_response.text}")
        try:
            error_data = token_response.json()
            error_msg = error_data.get('error_description', error_data.get('error', 'Unknown error'))
            return f"Error from Google (Status {token_response.status_code}): {error_msg}", 400
        except:
            return f"Failed to get token from Google. Status: {token_response.status_code}. Response: {token_response.text[:100]}", 400

    client.parse_request_body_response(json.dumps(token_response.json()))

    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo = requests.get(uri, headers=headers).json()

    if not userinfo.get("email_verified"):
        return "Email not verified by Google", 400

    google_id = userinfo["sub"]
    email = userinfo["email"]
    name = userinfo.get("given_name", "")
    picture = userinfo.get("picture", "")

    user = User.query.filter_by(google_id=google_id).first()

    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id
        else:
            user = User(
                google_id=google_id,
                email=email,
                username=email,
                name=name,
                picture_url=picture,
                last_login=datetime.utcnow(),
            )
            db.session.add(user)

    user.name = name
    user.picture_url = picture
    user.last_login = datetime.utcnow()
    db.session.commit()

    login_user(user, remember=True)

    return redirect(url_for("dashboard"))
