import os
from dotenv import load_dotenv
load_dotenv("backend/.env")
from google.ads.googleads.client import GoogleAdsClient

cfg = {
    "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
    "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
    "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
    "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
    "login_customer_id": os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
    "use_proto_plus": True,
}
try:
    c = GoogleAdsClient.load_from_dict(cfg)
    print("SUCCESS LOAD")
except Exception as e:
    print(f"FAILED LOAD: {e}")
