import urllib.request, json
# get CSRF
req_csrf = urllib.request.Request("https://admu-backend-jxi0.onrender.com/api/csrf-token")
resp_csrf = urllib.request.urlopen(req_csrf)
csrf_data = json.loads(resp_csrf.read().decode())
csrf_token = csrf_data.get("csrf_token")

# ensure the JWT
req = urllib.request.Request(
    "https://admu-backend-jxi0.onrender.com/api/settings",
    method="POST",
    headers={"Content-Type": "application/json", "x-csrf-token": csrf_token},
    data=json.dumps({"clinic_id": 1, "mock_mode": 0, "developer_token": "TEST"}).encode('utf-8')
)
try:
    resp = urllib.request.urlopen(req)
    print("SUCCESS", resp.read().decode())
except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"ERROR: {e}")
