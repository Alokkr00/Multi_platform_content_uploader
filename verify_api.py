import urllib.request
import urllib.error
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://127.0.0.1:8000"
TOKEN = os.getenv("DASHBOARD_SECRET", "")

ENDPOINTS = [
    "/api/status",
    "/api/sources",
    "/api/accounts",
    "/api/settings",
    "/api/logs",
    "/api/approval-queue"
]

def check_endpoint(endpoint, use_auth=True):
    url = f"{BASE_URL}{endpoint}"
    headers = {}
    if use_auth and TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    mode = "AUTHENTICATED" if use_auth else "UNAUTHENTICATED"
    print(f"Checking {url} [{mode}]...")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            status_code = response.getcode()
            content = response.read().decode('utf-8')
            
            if not use_auth:
                print(f"  [FAIL] Expected 401 for unauthenticated request, got {status_code}")
                return False, status_code, None

            if status_code != 200:
                print(f"  [FAIL] Non-200 status code: {status_code}")
                return False, status_code, None
            
            try:
                data = json.loads(content)
                print(f"  [SUCCESS] HTTP {status_code} - Valid JSON response.")
                return True, status_code, data
            except json.JSONDecodeError as e:
                print(f"  [FAIL] Failed to parse JSON: {e}")
                return False, status_code, None
                
    except urllib.error.HTTPError as e:
        if not use_auth and e.code == 401:
            print(f"  [SUCCESS] Unauthenticated request correctly rejected with HTTP 401 Unauthorized.")
            return True, 401, None

        print(f"  [FAIL] HTTP Error: {e.code} - {e.reason}")
        return False, e.code, None
    except Exception as e:
        print(f"  [FAIL] Unexpected error: {e}")
        return False, None, None

def main():
    print("--- Starting Live Security & API Verification ---")
    all_success = True
    
    # 1. Test unauthenticated request rejection (P0 requirement)
    print("\n1. Testing Unauthenticated Gate (Security Check)...")
    unauth_pass, _, _ = check_endpoint("/api/status", use_auth=False)
    if not unauth_pass:
        all_success = False

    # 2. Test authenticated endpoints
    print("\n2. Testing Authenticated Endpoints...")
    for endpoint in ENDPOINTS:
        success, code, data = check_endpoint(endpoint, use_auth=True)
        if not success:
            all_success = False
        print("-" * 40)
        
    if all_success:
        print("\nALL API SECURITY & ENDPOINT CHECKS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\nSOME API VERIFICATION CHECKS FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    main()
