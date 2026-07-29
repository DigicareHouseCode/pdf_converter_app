import time

import requests
import json
from datetime import datetime, timedelta
from flask import session


class QuickBooksSearch:
    def __init__(self, access_token, realm_id):
        self.access_token = access_token
        self.realm_id = realm_id
        self.base_url = "https://sandbox-quickbooks.api.intuit.com/v3"

    def refresh_access_token_if_needed(self):
        """Refresh access token for search class"""
        try:
            refresh_token = session.get('qb_refresh_token')
            if not refresh_token:
                print("[ERROR] No refresh token available for search")
                return False

            # Check if token is expired
            from app import get_quickbooks_oauth, is_token_expired
            if not is_token_expired() and self.access_token:
                return True  # Token still valid

            print("[INFO] Search: Token expired or about to expire, refreshing...")

            oauth = get_quickbooks_oauth()
            new_tokens = oauth.refresh_tokens(refresh_token)

            session['qb_access_token'] = new_tokens['access_token']
            if 'refresh_token' in new_tokens:
                session['qb_refresh_token'] = new_tokens['refresh_token']

            session['qb_token_expires_at'] = time.time() + new_tokens.get('expires_in', 3600)
            self.access_token = new_tokens['access_token']

            print("[SUCCESS] Search access token refreshed successfully")
            return True
        except Exception as e:
            print(f"[ERROR] Search token refresh failed: {str(e)}")
            return False

    def get_connection_status(self):
        """Get detailed connection status"""
        status = {
            'authenticated': False,
            'company_name': 'Unknown',
            'error': None,
            'realm_id': self.realm_id
        }

        # Test authentication
        auth_ok, auth_msg = self.test_authentication()
        status['authenticated'] = auth_ok

        if auth_ok:
            # Get company info
            company_info, error = self.get_company_info()
            if company_info and not error:
                status['company_name'] = self.safe_get(company_info, 'CompanyName', 'Unknown')
            else:
                status['error'] = error
        else:
            status['error'] = auth_msg

        return status

    def test_authentication(self):
        """Test if the authentication is working"""
        print("[AUTH] Testing QuickBooks authentication...")
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        # Test with a simple company info request
        url = f"{self.base_url}/company/{self.realm_id}/companyinfo/{self.realm_id}"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"[API] Authentication test response: {response.status_code}")

            if response.status_code == 200:
                print("[SUCCESS] Authentication successful!")
                return True, "Authentication successful"
            else:
                print(f"[ERROR] Authentication failed: {response.status_code}")
                print(f"[API] Response: {response.text[:200]}")
                return False, f"Authentication failed: {response.status_code}"

        except Exception as e:
            print(f"[ERROR] Authentication test error: {str(e)}")
            return False, f"Authentication test error: {str(e)}"

    def make_authenticated_request(self, endpoint):
        """Make authenticated request with proper error handling"""
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        url = f"{self.base_url}/company/{self.realm_id}/{endpoint}"

        print(f"[SEARCH] Making request to: {endpoint}")

        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"[API] Response status: {response.status_code}")

            if response.status_code == 200:
                return response.json(), None
            elif response.status_code == 401:
                return None, "Authentication failed. Please reconnect to QuickBooks."
            elif response.status_code == 400:
                # Try to parse error message
                try:
                    error_data = response.json()
                    error_msg = error_data.get('Fault', {}).get('Error', [{}])[0].get('Message', 'Bad Request')
                    return None, f"API Error: {error_msg}"
                except:
                    return None, f"API Error 400: {response.text[:200]}"
            else:
                return None, f"HTTP Error {response.status_code}: {response.text[:200]}"

        except requests.exceptions.Timeout:
            return None, "Request timeout. Please try again."
        except Exception as e:
            return None, f"Request failed: {str(e)}"

    def make_authenticated_request_with_retry(self, endpoint):
        """Make request with token refresh retry"""
        result, error = self.make_authenticated_request(endpoint)

        if error and "401" in str(error):
            print("[INFO] Search: 401 detected, refreshing token...")
            if self.refresh_access_token_if_needed():
                result, error = self.make_authenticated_request(endpoint)

        return result, error

    def safe_get(self, obj, key, default=None):
        """Safely get a value from a dictionary with nested key support"""
        if obj is None:
            return default

        if isinstance(key, str) and '.' in key:
            keys = key.split('.')
            current = obj
            for k in keys:
                if isinstance(current, dict) and k in current:
                    current = current[k]
                else:
                    return default
            return current
        else:
            return obj.get(key, default)

    def _get_transactions_with_pagination(self, entity_type, start_date, max_results=1000):
        """Get transactions with pagination support"""
        all_transactions = []
        start_position = 1
        page_size = 100  # QuickBooks max per page

        print(f"[STATS] Getting {entity_type} with pagination...")

        while True:
            # Build query with pagination
            query = f"SELECT * FROM {entity_type} WHERE TxnDate >= '{start_date}' ORDER BY TxnDate DESC MAXRESULTS {page_size} STARTPOSITION {start_position}"
            print(f"[SEARCH] Page query: {query}")

            encoded_query = requests.utils.quote(query)
            endpoint = f"query?query={encoded_query}"

            data, error = self.make_authenticated_request_with_retry(endpoint)

            if error:
                print(f"[ERROR] Pagination error for {entity_type}: {error}")
                break

            # Extract transactions based on entity type
            transactions = self.safe_get(data, f'QueryResponse.{entity_type}', [])

            if not transactions:
                print(f"[SUCCESS] No more {entity_type} transactions found")
                break

            print(f"[FILE] Page result: {len(transactions)} {entity_type} transactions")

            # Add active transactions
            active_count = 0
            for transaction in transactions:
                if self._is_transaction_active(transaction, entity_type):
                    formatted = self._format_expense_transaction(transaction, entity_type)
                    if formatted:
                        all_transactions.append(formatted)
                        active_count += 1

            print(f"[SUCCESS] Page: Added {active_count} active {entity_type} transactions")

            # Check if we've reached the limit or end of results
            if len(transactions) < page_size:
                print(f"[SUCCESS] Reached end of {entity_type} results")
                break

            if len(all_transactions) >= max_results:
                print(f"[WARNING] Reached max results limit ({max_results}) for {entity_type}")
                break

            start_position += page_size
            print(f"[INFO] Fetching next page of {entity_type}, start position: {start_position}")

        return all_transactions

    def get_expense_transactions(self, days_back=90, max_results=1000):
        """Get expense transactions with pagination support"""
        try:
            print("[INFO] Getting expense transactions with pagination...")

            # Test connection first
            auth_ok, auth_msg = self.test_authentication()
            if not auth_ok:
                return {"error": auth_msg}

            # Get company info to verify connection
            company_info, error = self.get_company_info()
            if error:
                return {"error": f"Failed to get company info: {error}"}

            if company_info:
                company_name = self.safe_get(company_info, 'CompanyName', 'Unknown Company')
                print(f"[SUCCESS] Connected to: {company_name}")

            # Calculate start date
            start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
            print(f"[INFO] Date range: Last {days_back} days (since {start_date})")

            # Entity types to query
            entity_types = {
                'Purchase': 'Purchase',
                'Bill': 'Bill',
                'VendorCredit': 'Vendor Credit',
                'JournalEntry': 'Journal Entry'
            }

            all_expense_transactions = []
            transaction_counts = {}

            # Get transactions for each entity type with pagination
            for entity_type, display_type in entity_types.items():
                print(f"\n[INFO] Fetching {display_type} transactions...")
                transactions = self._get_transactions_with_pagination(
                    entity_type, start_date, max_results
                )
                all_expense_transactions.extend(transactions)
                transaction_counts[display_type] = len(transactions)
                print(f"[SUCCESS] Total {display_type}: {len(transactions)} transactions")

            # Sort all transactions by date (newest first)
            all_expense_transactions.sort(key=lambda x: x.get('sort_date', ''), reverse=True)

            # Limit to max_results if needed
            if len(all_expense_transactions) > max_results:
                print(f"[WARNING] Limiting results from {len(all_expense_transactions)} to {max_results}")
                all_expense_transactions = all_expense_transactions[:max_results]

            print("\n[STATS] FINAL RESULTS:")
            print(f"[SUCCESS] Total active transactions: {len(all_expense_transactions)}")
            for trans_type, count in transaction_counts.items():
                print(f"   - {trans_type}: {count}")

            return {
                'company': company_info,
                'transactions': all_expense_transactions,
                'total_count': len(all_expense_transactions),
                'categories': transaction_counts,
                'query_info': {
                    'date_range': f"Last {days_back} days",
                    'max_results': max_results,
                    'actual_results': len(all_expense_transactions)
                }
            }

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"[ERROR] {error_msg}")
            return {"error": error_msg}

    def get_simple_transactions_test(self):
        """Simple test method to verify basic API connectivity"""
        try:
            print("[TEST] Testing basic API connectivity with simple queries...")

            # Test with simplest possible queries
            test_queries = {
                'Company': "SELECT * FROM CompanyInfo",
                'Account': "SELECT * FROM Account MAXRESULTS 5",
                'Vendor': "SELECT * FROM Vendor MAXRESULTS 5"
            }

            results = {}

            for query_name, query in test_queries.items():
                print(f"[SEARCH] Testing {query_name} query...")
                encoded_query = requests.utils.quote(query)
                endpoint = f"query?query={encoded_query}"

                data, error = self.make_authenticated_request_with_retry(endpoint)

                if error:
                    results[query_name] = f"Error: {error}"
                    print(f"[ERROR] {query_name} query failed: {error}")
                else:
                    # Get the appropriate entity from response
                    entity_name = query_name
                    if query_name == 'Company':
                        entity_name = 'CompanyInfo'

                    entities = self.safe_get(data, f'QueryResponse.{entity_name}', [])
                    results[query_name] = f"Success: Found {len(entities)} records"
                    print(f"[SUCCESS] {query_name}: Found {len(entities)} records")

            return results

        except Exception as e:
            return {"error": f"Test failed: {str(e)}"}

    def _is_transaction_active(self, transaction, transaction_type):
        """Check if a transaction is active (not deleted)"""
        try:
            # Check Active flag (common for most transaction types)
            active_flag = self.safe_get(transaction, 'Active', True)

            # Check sync token (if sync token is present, it's usually active)
            sync_token = self.safe_get(transaction, 'SyncToken')

            # Check domain (deleted transactions might not have domain)
            domain = self.safe_get(transaction, 'domain')

            # Check for deletion indicators
            sparse = self.safe_get(transaction, 'sparse', False)

            # For Journal Entries, check different indicators
            if transaction_type == 'Journal Entry':
                adjustment = self.safe_get(transaction, 'Adjustment', False)

            # Transaction is considered active if:
            # 1. Active flag is True (or not present, default to True)
            # 2. Not a sparse object
            # 3. Has a domain (usually present in active objects)
            is_active = (active_flag is not False) and (not sparse) and (domain is not None)

            return is_active

        except Exception as e:
            print(f"[WARNING] Error checking transaction activity: {str(e)}")
            # If we can't determine, assume it's active to be safe
            return True

    def _format_expense_transaction(self, transaction, transaction_type):
        """Format a transaction for display in expenses view"""
        try:
            # First check if transaction is active
            if not self._is_transaction_active(transaction, transaction_type):
                return None

            # Common fields
            transaction_date = self.safe_get(transaction, 'TxnDate', 'Unknown Date')
            amount = float(self.safe_get(transaction, 'TotalAmt', 0))
            transaction_id = self.safe_get(transaction, 'Id', '')

            # Skip if amount is 0 (might be placeholder/deleted)
            if amount == 0:
                return None

            # Get vendor/payee based on transaction type
            vendor_name = 'Unknown'
            if transaction_type == 'Purchase':
                vendor_ref = self.safe_get(transaction, 'EntityRef') or self.safe_get(transaction, 'VendorRef') or {}
                vendor_name = self.safe_get(vendor_ref, 'name', 'No Vendor')
            elif transaction_type in ['Bill', 'Vendor Credit']:
                vendor_ref = self.safe_get(transaction, 'VendorRef') or {}
                vendor_name = self.safe_get(vendor_ref, 'name', 'No Vendor')
            elif transaction_type == 'Journal Entry':
                # Journal entries might not have a vendor
                vendor_name = 'Journal Entry'

            # Skip transactions with "Deleted" or similar in vendor name
            if vendor_name and any(deleted_indicator in str(vendor_name).lower() for deleted_indicator in
                                   ['deleted', 'void', 'inactive']):
                return None

            # Get description/memo
            description = self.safe_get(transaction, 'PrivateNote', '') or self.safe_get(transaction, 'DocNumber',
                                                                                         '') or f"{transaction_type} #{transaction_id[-6:]}"

            # Skip transactions with "Deleted" in description
            if description and any(deleted_indicator in str(description).lower() for deleted_indicator in
                                   ['deleted', 'void', 'inactive']):
                return None

            # Get account information if available
            account_name = 'Unknown Account'
            if transaction_type == 'Purchase':
                account_ref = self.safe_get(transaction, 'AccountRef') or {}
                account_name = self.safe_get(account_ref, 'name', 'Unknown Account')
            elif transaction_type == 'Bill':
                # Bills might have line items with accounts
                lines = self.safe_get(transaction, 'Line', [])
                if lines:
                    first_line = lines[0]
                    account_ref = self.safe_get(first_line, 'AccountBasedExpenseLineDetail.AccountRef') or {}
                    account_name = self.safe_get(account_ref, 'name', 'Unknown Account')

            # Create sortable date for ordering
            try:
                sort_date = datetime.strptime(transaction_date, '%Y-%m-%d')
            except:
                sort_date = datetime.now()

            formatted_transaction = {
                'id': transaction_id,
                'date': transaction_date,
                'sort_date': sort_date,
                'amount': amount,
                'vendor': vendor_name,
                'type': transaction_type,
                'description': description,
                'account': account_name,
                'status': self._get_transaction_status(transaction, transaction_type),
                'is_active': True
            }

            return formatted_transaction

        except Exception as e:
            print(f"[WARNING] Error in _format_expense_transaction: {str(e)}")
            return None

    def _get_transaction_status(self, transaction, transaction_type):
        """Get the status of a transaction"""
        try:
            if transaction_type == 'Bill':
                due_date = self.safe_get(transaction, 'DueDate')
                if due_date:
                    due_date_obj = datetime.strptime(due_date, '%Y-%m-%d')
                    if due_date_obj < datetime.now():
                        return 'Overdue'
                    else:
                        return 'Open'
                return 'Open'
            elif transaction_type == 'Purchase':
                return 'Paid'
            elif transaction_type == 'Vendor Credit':
                return 'Credit'
            elif transaction_type == 'Journal Entry':
                return 'Posted'
            else:
                return 'Unknown'
        except:
            return 'Unknown'

    def get_company_info(self):
        """Get basic company information"""
        print("[INFO] Getting company information...")
        data, error = self.make_authenticated_request_with_retry(f'companyinfo/{self.realm_id}')

        if error:
            return None, error

        company_info = self.safe_get(data, 'CompanyInfo', {})
        return company_info, None

    def get_expense_summary(self, days_back=90, max_results=1000, top_vendors_count=3, chart_vendors_count=10):
        """Get expense summary similar to QuickBooks dashboard"""
        try:
            result = self.get_expense_transactions(days_back, max_results)
            if 'error' in result:
                return result

            transactions = result.get('transactions', [])
            categories = result.get('categories', {})

            # Calculate summary statistics
            total_expenses = sum(t['amount'] for t in transactions)
            average_expense = total_expenses / len(transactions) if transactions else 0

            # Group by vendor
            vendor_totals = {}
            for transaction in transactions:
                vendor = transaction['vendor']
                if vendor not in vendor_totals:
                    vendor_totals[vendor] = 0
                vendor_totals[vendor] += transaction['amount']

            # Get top vendors for display (3)
            top_vendors = dict(sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:top_vendors_count])

            # Get vendors for pie chart (10)
            chart_vendors = dict(sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:chart_vendors_count])

            summary = {
                'total_expenses': total_expenses,
                'transaction_count': len(transactions),
                'average_expense': average_expense,
                'categories': categories,
                'top_vendors': top_vendors,  # For the top vendors list (3)
                'chart_vendors': chart_vendors,  # For the pie chart (10)
                'date_range': f"Last {days_back} days",
                'max_results': max_results
            }

            result['summary'] = summary
            return result

        except Exception as e:
            return {"error": f"Failed to generate summary: {str(e)}"}


# Utility function to create search instance from session
def create_search_from_session(session):
    """Create QuickBooksSearch instance from Flask session"""
    access_token = session.get('qb_access_token')
    realm_id = session.get('qb_realm_id')

    if not access_token or not realm_id:
        return None, "QuickBooks not connected"

    return QuickBooksSearch(access_token, realm_id), None