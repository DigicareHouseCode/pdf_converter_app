import requests
import json
from datetime import datetime


class QuickBooksDebug:
    def __init__(self, access_token, realm_id):
        self.access_token = access_token
        self.realm_id = realm_id
        self.base_url = "https://sandbox-quickbooks.api.intuit.com/v3"

    def test_authentication(self):
        """Test authentication and connection to QuickBooks"""
        print("[SEARCH] Testing QuickBooks Authentication...")
        print(f"Realm ID: {self.realm_id}")
        print(f"Access Token: {self.access_token[:20]}...")

        # Test 1: Basic company info
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        url = f"{self.base_url}/company/{self.realm_id}/companyinfo/{self.realm_id}"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"[API] API Response Status: {response.status_code}")
            print(f"[API] Response Headers: {dict(response.headers)}")

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'application/json' in content_type:
                    data = response.json()
                    print("[SUCCESS] Authentication SUCCESSFUL!")
                    print(f"[INFO] Company: {data.get('CompanyInfo', {}).get('CompanyName', 'Unknown')}")
                    return True, "Authentication successful"
                else:
                    # Handle XML response (error)
                    print(f"[ERROR] Unexpected response type: {content_type}")
                    print(f"[API] Response content: {response.text[:500]}")
                    return False, f"Unexpected response type: {content_type}"
            else:
                print(f"[ERROR] API Error: {response.status_code}")
                print(f"[API] Error response: {response.text[:500]}")
                return False, f"API Error: {response.status_code}"

        except Exception as e:
            print(f"[ERROR] Request failed: {str(e)}")
            return False, f"Request failed: {str(e)}"

    def check_token_validity(self):
        """Check if the access token is valid"""
        print("\n[AUTH] Checking token validity...")

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        # Simple query to test token
        url = f"{self.base_url}/company/{self.realm_id}/query?query=SELECT COUNT(*) FROM Customer"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"[API] Query test status: {response.status_code}")

            if response.status_code == 200:
                print("[SUCCESS] Token is VALID")
                return True
            else:
                print(f"[ERROR] Token validation failed: {response.status_code}")
                print(f"[API] Response: {response.text[:200]}")
                return False

        except Exception as e:
            print(f"[ERROR] Token check failed: {str(e)}")
            return False