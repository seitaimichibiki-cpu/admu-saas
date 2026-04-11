import os
import stripe
from dotenv import load_dotenv

# .env をロードしてStripeキーを取得
load_dotenv()
stripe.api_key = os.environ.get("STRIPE_API_KEY")

if not stripe.api_key:
    print("🚨 エラー: Stripe API Keyが見つかりません。")
    exit(1)

print("🛍️  AdMuのStripe商品を本番環境に作成します...\n")

def create_product_and_price(name, amount):
    try:
        # 商品の作成
        product = stripe.Product.create(
            name=name,
            description="AdMu システム利用料"
        )
        # 価格の作成（月額）
        price = stripe.Price.create(
            product=product.id,
            unit_amount=amount,
            currency="jpy",
            recurring={"interval": "month"}
        )
        print(f"✅ 作成成功: {name}")
        print(f"   価格: ¥{amount:,}/月")
        print(f"   Price ID: {price.id}\n")
        return price.id
    except Exception as e:
        print(f"❌ エラー発生 ({name}): {str(e)}")
        return None

# 1. 単体プラン (39,800円)
standalone_price_id = create_product_and_price("AdMu システム利用料（単体プラン）", 39800)

# 2. ロジクションセットプラン (11,000円)
set_price_id = create_product_and_price("AdMu システム利用料（LOGICTIONセット特別プラン）", 11000)

if standalone_price_id and set_price_id:
    # .env ファイルに自動追記
    with open(".env", "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    with open(".env", "w", encoding="utf-8") as f:
        for line in lines:
            if line.startswith("STRIPE_PRICE_STANDALONE="):
                f.write(f"STRIPE_PRICE_STANDALONE={standalone_price_id}\n")
            elif line.startswith("STRIPE_PRICE_LOGICTION_SET="):
                f.write(f"STRIPE_PRICE_LOGICTION_SET={set_price_id}\n")
            else:
                f.write(line)
                
    print("🎉 商品の作成が完了し、.env ファイルへの自動保存が完了しました！")
