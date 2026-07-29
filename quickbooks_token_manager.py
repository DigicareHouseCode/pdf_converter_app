from quickbooks_oauth import QuickBooksOAuth
from quickbooks_service import QuickBooksService

def ensure_valid_token(session, app_config):
    """Ensure access token is valid. If expired → refresh automatically."""
    access_token = session.get("qb_access_token")
    refresh_token = session.get("qb_refresh_token")
    realm_id = session.get("qb_realm_id")

    if not access_token or not refresh_token or not realm_id:
        return False, "Missing QuickBooks credentials"

    qb_service = QuickBooksService()
    qb_service.set_credentials(access_token, realm_id)

    # 1) Try small API call (CompanyInfo) to check if token is valid
    is_ok, _ = qb_service.test_connection()

    if is_ok:
        return True, access_token

    # 2) Token expired → refresh it
    oauth = QuickBooksOAuth(
        client_id=app_config['QB_CLIENT_ID'],
        client_secret=app_config['QB_CLIENT_SECRET'],
        redirect_uri=app_config['QB_REDIRECT_URI'],
        environment=app_config['QB_ENVIRONMENT']
    )

    try:
        new_tokens = oauth.refresh_tokens(refresh_token)
        session['qb_access_token'] = new_tokens['access_token']
        session['qb_refresh_token'] = new_tokens['refresh_token']
        return True, new_tokens['access_token']

    except Exception as e:
        return False, f"Token refresh failed: {str(e)}"
