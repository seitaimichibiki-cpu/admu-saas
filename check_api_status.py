import os
from dotenv import load_dotenv
load_dotenv("backend/.env")
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

cfg = {
    "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
    "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
    "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
    "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
    "login_customer_id": os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
    "use_proto_plus": True,
}

def check_status():
    try:
        client = GoogleAdsClient.load_from_dict(cfg)
        customer_service = client.get_service("CustomerService")
        
        print("Checking API Status with a real query...")
        ga_service = client.get_service("GoogleAdsService")
        query = "SELECT customer.id, customer.descriptive_name FROM customer LIMIT 1"
        customer_id = os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
        
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            print(f"  アカウント名: {row.customer.descriptive_name}")
            
        print("✅ API 疎通OK！")
    except GoogleAdsException as ex:
        print(f"GoogleAdsException: {ex}")
        for error in ex.failure.errors:
            print(f"Error Code: {error.error_code}")
            print(f"Message: {error.message}")
            if "DEVELOPER_TOKEN_NOT_APPROVED" in str(error.error_code):
                print("❌ まだ審査中（または非承認）です: DEVELOPER_TOKEN_NOT_APPROVED")
    except Exception as e:
        print(f"Other Error: {e}")

if __name__ == "__main__":
    check_status()
