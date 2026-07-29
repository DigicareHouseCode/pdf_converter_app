import requests
import pandas as pd
import os
import json
import base64
import time
from datetime import datetime
from urllib.parse import urlencode
from flask import session


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
            error_msg = f"Token exchange failed: {response.status_code} - {response.text}"
            raise Exception(error_msg)

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
        credentials = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(credentials.encode()).decode()


class QuickBooksService:
    def __init__(self):
        self.base_url = "https://sandbox-quickbooks.api.intuit.com/v3"
        self.connected = False
        self.access_token = None
        self.realm_id = None

    def set_credentials(self, access_token, realm_id):
        """Set QuickBooks OAuth credentials"""
        self.access_token = access_token
        self.realm_id = realm_id
        self.connected = True
        return True

    def refresh_access_token_if_needed(self):
        """Refresh access token if it's expired or about to expire"""
        try:
            # Check if we have refresh token in session
            refresh_token = session.get('qb_refresh_token')
            if not refresh_token:
                print("[ERROR] No refresh token available")
                return False

            # Check if token is expired
            from app import get_quickbooks_oauth, is_token_expired
            if not is_token_expired() and self.access_token:
                return True  # Token still valid

            print("[INFO] Token expired or about to expire, refreshing...")

            # Use the OAuth class to refresh tokens
            oauth = get_quickbooks_oauth()
            new_tokens = oauth.refresh_tokens(refresh_token)

            # Update session with new tokens
            session['qb_access_token'] = new_tokens['access_token']
            # Keep the refresh token if a new one is provided, otherwise keep the old one
            if 'refresh_token' in new_tokens:
                session['qb_refresh_token'] = new_tokens['refresh_token']

            # Update token expiration
            session['qb_token_expires_at'] = time.time() + new_tokens.get('expires_in', 3600)

            # Update service instance
            self.access_token = new_tokens['access_token']

            print("[SUCCESS] Access token refreshed successfully")
            return True

        except Exception as e:
            print(f"[ERROR] Token refresh failed: {str(e)}")
            return False

    def make_request(self, endpoint, method='GET', data=None):
        """Make API request to QuickBooks with better error handling"""
        if not self.connected:
            return None, "Not connected to QuickBooks"

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        # Build URL - handle both full endpoints and query parameters
        if endpoint.startswith('http'):
            url = endpoint
        elif '?' in endpoint:
            url = f"{self.base_url}/company/{self.realm_id}/{endpoint}"
        else:
            url = f"{self.base_url}/company/{self.realm_id}/{endpoint}"

        print(f"[SEARCH] Making QuickBooks request to: {url}")

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=30)
            else:
                return None, "Unsupported HTTP method"

            print(f"[API] QuickBooks API Response: {response.status_code}")
            print(f"[API] Content-Type: {response.headers.get('content-type')}")

            # Check if response is JSON
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type:
                if response.status_code == 200:
                    return response.json(), None
                else:
                    error_data = response.json()
                    error_msg = error_data.get('Fault', {}).get('Error', [{}])[0].get('Message', 'Unknown error')
                    return None, f"API Error {response.status_code}: {error_msg}"
            else:
                # Handle non-JSON responses (like XML errors)
                if response.status_code == 200:
                    return None, "Unexpected response format (expected JSON)"
                else:
                    return None, f"HTTP Error {response.status_code}: {response.text[:200]}"

        except requests.exceptions.Timeout:
            return None, "Request timeout. Please try again."
        except Exception as e:
            return None, f"Request failed: {str(e)}"

    def make_request_with_retry(self, endpoint, method='GET', data=None):
        """Make API request with automatic token refresh on 401"""
        # First attempt
        result, error = self.make_request(endpoint, method, data)

        # If 401 error, try refreshing token and retry
        if error and "401" in error:
            print("[ERROR] 401 Error detected, attempting token refresh...")
            if self.refresh_access_token_if_needed():
                print("[INFO] Retrying request with fresh token...")
                result, error = self.make_request(endpoint, method, data)

        return result, error

    def test_connection(self):
        """Test the QuickBooks connection with better error handling"""
        print("[TEST] Testing QuickBooks connection...")

        # Test with companyinfo endpoint
        result, error = self.make_request_with_retry(f'companyinfo/{self.realm_id}')
        if error:
            print(f"[ERROR] Connection test failed: {error}")
            return False, error

        if result and 'CompanyInfo' in result:
            company_name = result['CompanyInfo'].get('CompanyName', 'Unknown')
            print(f"[SUCCESS] Connection successful! Company: {company_name}")
            return True, f"Connected to {company_name}"
        else:
            return False, "Unexpected response format"

    def get_company_info(self):
        """Get company information"""
        return self.make_request_with_retry(f'companyinfo/{self.realm_id}')

    def get_accounts(self):
        """Get list of accounts from QuickBooks"""
        print("[INFO] Fetching accounts...")
        result, error = self.make_request_with_retry('query?query=select * from Account maxresults 50')
        if error:
            print(f"[ERROR] Failed to fetch accounts: {error}")
            return [], error

        accounts = result.get('QueryResponse', {}).get('Account', [])
        print(f"[SUCCESS] Found {len(accounts)} accounts")
        return accounts, None

    def get_vendors(self):
        """Get list of vendors from QuickBooks"""
        print("[INFO] Fetching vendors...")
        result, error = self.make_request_with_retry('query?query=select * from Vendor maxresults 50')
        if error:
            return [], error
        return result.get('QueryResponse', {}).get('Vendor', []), None

    def find_or_create_vendor(self, vendor_name):
        """Find existing vendor or create new one"""
        if not vendor_name or vendor_name.strip() == '':
            return None, "Vendor name is required"

        vendors, error = self.get_vendors()
        if error:
            return None, error

        # Look for existing vendor
        vendor = next((v for v in vendors if v.get('DisplayName', '').lower() == vendor_name.lower()), None)

        if vendor:
            print(f"[SUCCESS] Found existing vendor: {vendor_name}")
            return vendor, None

        # Create new vendor
        print(f"[INFO] Creating new vendor: {vendor_name}")
        vendor_data = {
            "DisplayName": vendor_name,
            "PrintOnCheckName": vendor_name
        }

        result, error = self.make_request_with_retry('vendor', method='POST', data=vendor_data)
        if error:
            return None, f"Failed to create vendor: {error}"

        return result.get('Vendor'), None

    def create_expense_transaction(self, transaction_data):
        """Create an expense transaction in QuickBooks"""
        try:
            print(f"[INFO] Creating expense transaction: ${transaction_data['debit']}")

            # Get accounts
            accounts, error = self.get_accounts()
            if error:
                return None, error

            # Find appropriate accounts
            bank_account = next((acc for acc in accounts if acc.get('AccountType') == 'Bank'), None)
            expense_account = next((acc for acc in accounts if acc.get('AccountType') == 'Expense'),
                                   accounts[0] if accounts else None)

            if not bank_account or not expense_account:
                return None, "Required accounts not found in QuickBooks"

            # Handle vendor/payee
            vendor_ref = None
            if transaction_data.get('payee') and transaction_data['payee'].strip():
                vendor, error = self.find_or_create_vendor(transaction_data['payee'])
                if vendor and not error:
                    vendor_ref = {
                        "value": vendor['Id'],
                        "name": vendor['DisplayName']
                    }

            # Create purchase transaction
            purchase_data = {
                "PaymentType": "Cash",
                "AccountRef": {
                    "value": bank_account['Id'],
                    "name": bank_account['Name']
                },
                "TotalAmt": transaction_data['debit'],
                "TxnDate": transaction_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                "Line": [
                    {
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "Amount": transaction_data['debit'],
                        "Description": transaction_data.get('description', '')[:100],
                        "AccountBasedExpenseLineDetail": {
                            "AccountRef": {
                                "value": expense_account['Id'],
                                "name": expense_account['Name']
                            }
                        }
                    }
                ]
            }

            # Add vendor reference if available
            if vendor_ref:
                purchase_data["EntityRef"] = vendor_ref

            print("[API] Sending purchase data to QuickBooks...")
            result, error = self.make_request_with_retry('purchase', method='POST', data=purchase_data)
            if error:
                return None, error

            print("[SUCCESS] Purchase transaction created successfully")
            return result.get('Purchase'), None

        except Exception as e:
            return None, f"Error creating expense: {str(e)}"

    def create_income_transaction(self, transaction_data):
        """Create an income/deposit transaction in QuickBooks"""
        try:
            print(f"[INFO] Creating income transaction: ${transaction_data['credit']}")

            # Get accounts
            accounts, error = self.get_accounts()
            if error:
                return None, error

            # Find appropriate accounts
            bank_account = next((acc for acc in accounts if acc.get('AccountType') == 'Bank'), None)
            income_account = next((acc for acc in accounts if acc.get('AccountType') == 'Income'),
                                  accounts[0] if accounts else None)

            if not bank_account or not income_account:
                return None, "Required accounts not found in QuickBooks"

            # Create deposit transaction
            deposit_data = {
                "DepositToAccountRef": {
                    "value": bank_account['Id'],
                    "name": bank_account['Name']
                },
                "TxnDate": transaction_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                "Line": [
                    {
                        "DetailType": "DepositLineDetail",
                        "Amount": transaction_data['credit'],
                        "Description": transaction_data.get('description', '')[:100],
                        "DepositLineDetail": {
                            "AccountRef": {
                                "value": income_account['Id'],
                                "name": income_account['Name']
                            }
                        }
                    }
                ]
            }

            print("[API] Sending deposit data to QuickBooks...")
            result, error = self.make_request_with_retry('deposit', method='POST', data=deposit_data)
            if error:
                return None, error

            print("[SUCCESS] Income transaction created successfully")
            return result.get('Deposit'), None

        except Exception as e:
            return None, f"Error creating income transaction: {str(e)}"

    def send_transactions_to_quickbooks(self, excel_file_path):
        """Main method to send transactions from Excel to QuickBooks"""
        try:
            if not self.connected:
                return False, "Please connect to QuickBooks first"

            print("[INFO] Starting QuickBooks transaction import...")

            # Test connection first with retry
            connection_ok, error = self.test_connection()
            if not connection_ok:
                return False, f"QuickBooks connection failed: {error}"

            # Read Excel file
            df = pd.read_excel(excel_file_path)
            print(f"[FILE] Read {len(df)} transactions from Excel file")

            successful_transactions = 0
            failed_transactions = 0
            results = []

            # Process in smaller batches to avoid timeout and rate limiting
            batch_size = 10  # Reduced from 20 to process faster
            total_batches = (len(df) + batch_size - 1) // batch_size

            for batch_num in range(total_batches):
                # Check token before each batch
                if not self.refresh_access_token_if_needed():
                    return False, "Token refresh failed during batch processing"

                start_idx = batch_num * batch_size
                end_idx = min((batch_num + 1) * batch_size, len(df))
                batch_df = df.iloc[start_idx:end_idx]

                print(f"[INFO] Processing batch {batch_num + 1}/{total_batches} ({len(batch_df)} transactions)")

                for index, row in batch_df.iterrows():
                    try:
                        # Skip empty rows
                        if pd.isna(row.get('Date')) and pd.isna(row.get('Description')):
                            continue

                        # Convert date if needed
                        transaction_date = row.get('Date')
                        if hasattr(transaction_date, 'strftime'):
                            transaction_date = transaction_date.strftime('%Y-%m-%d')
                        elif isinstance(transaction_date, str):
                            # Keep as is, QuickBooks will handle validation
                            pass
                        else:
                            transaction_date = datetime.now().strftime('%Y-%m-%d')

                        transaction_data = {
                            'date': transaction_date,
                            'debit': float(row.get('Debit', 0) or 0),
                            'credit': float(row.get('Credit', 0) or 0),
                            'payee': str(row.get('Payee', '')) if pd.notna(row.get('Payee')) else '',
                            'description': str(row.get('Description', 'Transaction')) if pd.notna(
                                row.get('Description')) else 'Transaction',
                            'cheque_number': str(row.get('Cheque #', '')) if pd.notna(row.get('Cheque #')) else ''
                        }

                        # Skip transactions with zero amount
                        if transaction_data['debit'] == 0 and transaction_data['credit'] == 0:
                            results.append(f"[WARNING] Skipped transaction {index + 1}: Zero amount")
                            continue

                        # Process both debit and credit transactions
                        if transaction_data['debit'] > 0:
                            print(f"[INFO] Processing debit transaction {index + 1}: ${transaction_data['debit']}")
                            result, error = self.create_expense_transaction(transaction_data)
                            if result:
                                successful_transactions += 1
                                results.append(
                                    f"[SUCCESS] Transaction {index + 1}: ${transaction_data['debit']} expense created")
                                print(f"[SUCCESS] Transaction {index + 1} sent to QuickBooks")
                            else:
                                failed_transactions += 1
                                results.append(f"[ERROR] Transaction {index + 1} failed: {error}")
                                print(f"[ERROR] Failed to send transaction {index + 1}: {error}")

                        elif transaction_data['credit'] > 0:
                            print(f"[INFO] Processing credit transaction {index + 1}: ${transaction_data['credit']}")
                            result, error = self.create_income_transaction(transaction_data)
                            if result:
                                successful_transactions += 1
                                results.append(
                                    f"[SUCCESS] Transaction {index + 1}: ${transaction_data['credit']} income created")
                                print(f"[SUCCESS] Transaction {index + 1} sent to QuickBooks")
                            else:
                                failed_transactions += 1
                                results.append(f"[ERROR] Transaction {index + 1} failed: {error}")
                                print(f"[ERROR] Failed to send transaction {index + 1}: {error}")
                        else:
                            results.append(f"[WARNING] Skipped transaction {index + 1}: Zero amount")

                    except Exception as e:
                        failed_transactions += 1
                        error_msg = f"[ERROR] Error processing transaction {index + 1}: {str(e)}"
                        results.append(error_msg)
                        print(error_msg)
                        continue

                # Rate limiting between batches (reduced time)
                if batch_num < total_batches - 1:  # Don't wait after the last batch
                    print("[WAIT] Rate limiting: waiting 1 second before next batch...")
                    time.sleep(1)

            summary = f"QuickBooks Import Complete: {successful_transactions} successful, {failed_transactions} failed"
            print(f"[STATS] {summary}")
            return True, {
                'summary': summary,
                'details': results,
                'successful': successful_transactions,
                'failed': failed_transactions,
                'total_processed': len(df)
            }

        except Exception as e:
            error_msg = f"QuickBooks integration error: {str(e)}"
            print(f"[ERROR] {error_msg}")
            return False, error_msg