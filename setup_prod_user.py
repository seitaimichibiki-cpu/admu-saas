import urllib.request, json

base_url = "https://admu-backend-jxi0.onrender.com"

# 1. Get CSRF Token
req_csrf = urllib.request.Request(f"{base_url}/api/csrf-token")
resp_csrf = urllib.request.urlopen(req_csrf)
csrf_data = json.loads(resp_csrf.read().decode())
csrf_token = csrf_data.get("csrf_token")
cookie = resp_csrf.getheader("Set-Cookie")

print("CSRF Token:", csrf_token)

# 2. Register
req_reg = urllib.request.Request(
    f"{base_url}/api/auth/register",
    method="POST",
    headers={"Content-Type": "application/json", "x-csrf-token": csrf_token, "Cookie": cookie},
    data=json.dumps({"email": "seitaimichibiki@gmail.com", "password": "gai1124714", "clinic_name": "整体院導"}).encode("utf-8")
)
try:
    resp_reg = urllib.request.urlopen(req_reg)
    reg_data = json.loads(resp_reg.read().decode())
    print("Register Success:", reg_data)
except urllib.error.HTTPError as e:
    err_body = e.read().decode('utf-8')
    print("Register Failed:", err_body)
    # If already registered, it's fine. We just need to activate it.
    
# 3. Get Overview as Admin to find the clinic ID
req_admin = urllib.request.Request(
    f"{base_url}/api/admin/overview",
    headers={"Authorization": "admu2024", "Cookie": cookie}
)
try:
    resp_admin = urllib.request.urlopen(req_admin)
    admin_data = json.loads(resp_admin.read().decode())
    clinics = admin_data.get("clinics", [])
    target_clinic = None
    for c in clinics:
        if c.get("clinic_name") == "整体院導" or c.get("clinic_id") > 1:
            target_clinic = c
            break
            
    if target_clinic:
        cid = target_clinic["clinic_id"]
        print(f"Found Clinic ID: {cid}")
        
        # 4. Activate the clinic
        req_act = urllib.request.Request(
            f"{base_url}/api/admin/clinics",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "admu2024", "x-csrf-token": csrf_token, "Cookie": cookie},
            data=json.dumps({"id": cid, "plan_status": "active", "password": "admu2024"}).encode("utf-8")
        )
        resp_act = urllib.request.urlopen(req_act)
        print("Activate Success:", resp_act.read().decode())
    else:
        print("Clinic not found.")
except Exception as e:
    print("Admin Error:", e)

