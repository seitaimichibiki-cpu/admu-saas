import urllib.request, json
req = urllib.request.Request(
    "https://admu-backend-jxi0.onrender.com/api/admin/overview",
    headers={"Cookie": "access_token=INVALID"}
)
try:
    urllib.request.urlopen(req)
except Exception as e:
    print(e)
