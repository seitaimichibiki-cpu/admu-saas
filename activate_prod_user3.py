import urllib.request, json

base_url = "https://admu-backend-jxi0.onrender.com"

# 1. Get CSRF Token
req_csrf = urllib.request.Request(f"{base_url}/api/csrf-token")
resp_csrf = urllib.request.urlopen(req_csrf)
csrf_data = json.loads(resp_csrf.read().decode())
csrf_token = csrf_data.get("csrf_token")
cookie = resp_csrf.getheader("Set-Cookie")

cid = 2 

# try password = "gai1124714"
for pwd in ["gai1124714", "admu2024", "gai1124714!"]:
    print(f"Trying password {pwd}...")
    req_act = urllib.request.Request(
        f"{base_url}/api/admin/clinics",
        method="POST",
        headers={"Content-Type": "application/json", "x-csrf-token": csrf_token, "Cookie": cookie},
        data=json.dumps({"id": cid, "plan_status": "active", "password": pwd}).encode("utf-8")
    )
    try:
        resp_act = urllib.request.urlopen(req_act)
        print("Activate Success!", resp_act.read().decode())
        break
    except urllib.error.HTTPError as e:
        print("Activate Error HTTP:", e.read().decode())
    except Exception as e:
        print("Activate Error:", e)

