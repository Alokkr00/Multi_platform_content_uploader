import urllib.request
import urllib.error
import json
import sys

BASE_URL = "http://127.0.0.1:8001"
ENDPOINTS = [
    "/api/status",
    "/api/sources",
    "/api/accounts",
    "/api/settings",
    "/api/logs",
    "/api/approval-queue"
]

def check_endpoint(endpoint):
    url = f"{BASE_URL}{endpoint}"
    print(f"Checking {url}...")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            status_code = response.getcode()
            content = response.read().decode('utf-8')
            
            # Check status code
            if status_code != 200:
                print(f"  [FAIL] Non-200 status code: {status_code}")
                return False, status_code, None
            
            # Check if valid JSON
            try:
                data = json.loads(content)
                print(f"  [SUCCESS] HTTP {status_code} - Valid JSON response.")
                print(f"  Data keys/summary: {list(data.keys()) if isinstance(data, dict) else f'list of length {len(data)}'}")
                return True, status_code, data
            except json.JSONDecodeError as e:
                print(f"  [FAIL] Failed to parse JSON: {e}")
                print(f"  Raw Content: {content[:200]}")
                return False, status_code, None
                
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] HTTP Error: {e.code} - {e.reason}")
        try:
            body = e.read().decode('utf-8')
            print(f"  Error Body: {body}")
        except Exception:
            pass
        return False, e.code, None
    except urllib.error.URLError as e:
        print(f"  [FAIL] URL Error: {e.reason}")
        return False, None, None
    except Exception as e:
        print(f"  [FAIL] Unexpected error: {e}")
        return False, None, None

def main():
    print("--- Starting Live API Check ---")
    all_success = True
    results = {}
    for endpoint in ENDPOINTS:
        success, code, data = check_endpoint(endpoint)
        results[endpoint] = {"success": success, "status_code": code, "data": data}
        if not success:
            all_success = False
        print("-" * 40)
        
    if all_success:
        print("ALL ENDPOINTS VERIFIED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("SOME ENDPOINTS FAILED VERIFICATION.")
        sys.exit(1)

if __name__ == "__main__":
    main()
