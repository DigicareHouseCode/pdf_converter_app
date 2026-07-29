import requests
import json
from urllib.parse import urlencode
import webbrowser
from flask import request, redirect, session, url_for


class QuickBooksOAuth:
    def __init__(self, client_id, client_secret, redirect_uri, environment='sandbox'):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.environment = environment

        if environment == 'sandbox':
            self.base_url = "https://sandbox-quickbooks.api.intuit.com"
            self.auth_url = "https://appcenter.intuit.com/connect/oauth2"
            self.token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
        else:
            self.base_url = "https://quickbooks.api.intuit.com"
            self.auth_url = "https://appcenter.intuit.com/connect/oauth2"
            self.token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

    def get_authorization_url(self):
        """Generate the authorization URL for OAuth 2.0"""
        params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'scope': 'com.intuit.quickbooks.accounting',
            'redirect_uri': self.redirect_uri,
            'state': 'quickbooks_oauth_state'
        }

        return f"{self.auth_url}?{urlencode(params)}"

    def get_tokens(self, authorization_code):
        """Exchange authorization code for access tokens"""
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f"Basic {self._get_basic_auth()}"
        }

        data = {
            'grant_type': 'authorization_code',
            'code': authorization_code,
            'redirect_uri': self.redirect_uri
        }

        response = requests.post(self.token_url, headers=headers, data=data)

        if response.status_code == 200:
            tokens = response.json()
            return {
                'access_token': tokens.get('access_token'),
                'refresh_token': tokens.get('refresh_token'),
                'expires_in': tokens.get('expires_in')
            }
        else:
            raise Exception(f"Token exchange failed: {response.text}")

    def refresh_tokens(self, refresh_token):
        """Refresh access tokens using refresh token"""
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f"Basic {self._get_basic_auth()}"
        }

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }

        response = requests.post(self.token_url, headers=headers, data=data)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Token refresh failed: {response.text}")

    def _get_basic_auth(self):
        """Generate Basic Auth header for token requests"""
        import base64
        credentials = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(credentials.encode()).decode()