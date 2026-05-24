import os
import sys

print("Python version:", sys.version)

try:
    from google.ads.googleads.client import GoogleAdsClient
    print("GOOGLE ADS IMPORT SUCCESS")
except Exception as e:
    print("GOOGLE ADS IMPORT FAILED:", e)
