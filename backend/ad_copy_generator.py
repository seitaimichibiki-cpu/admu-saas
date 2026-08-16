"""
ad_copy_generator.py - Gemini APIを使ったAI広告文生成 (google.genai Client API)
"""
import json
import os
import re

try:
    import google.genai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# 整体院業種向けデフォルト広告文テンプレート（Gemini API未設定時のフォールバック）
FALLBACK_HEADLINES = [
    "{region}の整体院｜初回特典あり",
    "腰痛・肩こりの専門院",
    "当日予約OK・夜間診療対応",
    "国家資格保有スタッフ在籍",
    "産後骨盤矯正に定評あり",
    "完全個室でプライバシー配慮",
    "口コミ4.8以上の高評価院",
    "{clinic_name}で根本改善",
    "体の歪み・姿勢矯正なら",
    "無料カウンセリング実施中",
    "慢性疲労・頭痛にも対応",
    "駅徒歩{minutes}分・好アクセス",
    "予約はLINEでも受付中",
    "施術後のアフターケアも充実",
    "土日祝日も診療しています",
]
FALLBACK_DESCRIPTIONS = [
    "{region}で慢性的な腰痛・肩こりでお悩みなら{clinic_name}へ。根本原因にアプローチする施術で、多くの患者様が改善を実感しています。",
    "国家資格を持つ経験豊富なスタッフが、あなたの体のお悩みに寄り添います。初回限定のお得なプランで、まずはお気軽にご来院ください。",
    "産後の骨盤矯正、姿勢改善、スポーツ障害まで幅広く対応。完全個室でプライバシーに配慮した安心の環境でお待ちしています。",
    "当日予約可・夜間診療対応で忙しい方にも通いやすい整体院です。LINEで24時間予約受付中。まずはお気軽にご相談ください。",
]

GENERATION_PROMPT = """
あなたはGoogle広告のプロコピーライターです。整体院・接骨院向けのレスポンシブ検索広告（RSA）用の広告文を生成してください。

## 入力情報
- クリニック名: {clinic_name}
- 地域: {region}
- 訴求ポイント: {appeal_points}
- ターゲット悩み: {target_issues}
- 追加指示: {extra_instructions}

{persona_section}

## 出力形式（JSON形式で出力）
```json
{{
  "headlines": [
    "見出し1（最大30文字）",
    "見出し2（最大30文字）",
    ...（必ず15個生成）
  ],
  "descriptions": [
    "説明文1（最大90文字）",
    "説明文2（最大90文字）",
    "説明文3（最大90文字）",
    "説明文4（最大90文字）"
  ]
}}
```

## ルール
- **見出し（headlines）は必ず15個、説明文（descriptions）は必ず4個フルで生成してください。数が不足するとGoogle広告の評価が下がります。**
- 見出しは各半角30文字（日本語全角で15文字）以内、説明文は各半角90文字（日本語全角で45文字）以内（句読点含む）。
- **「登録キーワード」に記載されている言葉を、見出しの最初の5つ以上にそれぞれ必ず含めてください。スペースが入っているキーワードは、見出しに入れる際はスペースを詰めても構いません（例：「腰痛 整体」→「腰痛整体」）。これにより、Google広告での広告有効性が劇的に高まります。**
- それぞれの見出しは独自の切り口（実績、価格・初回特典、アクセスの良さ、個室などの環境、悩みの解消、予約の手軽さ等）で記述し、重複や類似した見出し（単語の順番を入れ替えただけなど）を避け、バリエーションを豊かにしてください。
- 広告ポリシーに準拠（証明できない「最高」「No.1」「日本一」などは使用禁止）。
- 自然な日本語で、ユーザーの検索意図に沿った内容。
- 必ずJSON形式のみで返答すること。
"""


class AdCopyGenerator:

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[AdCopyGenerator] Gemini初期化失敗: {e}")

    def generate(self, context: dict) -> dict:
        """
        context: {
            clinic_name, region, appeal_points, target_issues, extra_instructions
        }
        returns: {headlines: [...], descriptions: [...]}
        """
        if self._client:
            return self._generate_with_gemini(context)
        return self._generate_fallback(context)

    def _generate_with_gemini(self, context: dict) -> dict:
        persona_section = ""
        if any([context.get("target_age_gender"), context.get("target_pain_point"), context.get("persona_details")]):
            persona_section = "## ターゲットペルソナ（この人物に深く刺さる言葉を選んでください）\n"
            if context.get("target_age_gender"): persona_section += f"- 年齢・性別: {context.get('target_age_gender')}\n"
            if context.get("target_job_lifestyle"): persona_section += f"- 職業・ライフスタイル: {context.get('target_job_lifestyle')}\n"
            if context.get("target_pain_point"): persona_section += f"- 深い悩み: {context.get('target_pain_point')}\n"
            if context.get("target_desired_outcome"): persona_section += f"- 求める理想（ゴール）: {context.get('target_desired_outcome')}\n"
            if context.get("persona_details"):
                persona_section += f"\n### 登録済みペルソナ詳細（広告文はこのペルソナに最適化してください）\n{context['persona_details']}\n"

        keywords_list = context.get("keywords", [])
        keywords_str = ", ".join(keywords_list) if keywords_list else "なし"

        prompt = GENERATION_PROMPT.format(
            clinic_name=context.get("clinic_name", "〇〇整体院"),
            region=context.get("region", ""),
            appeal_points=context.get("appeal_points", ""),
            target_issues=context.get("target_issues", "腰痛、肩こり"),
            extra_instructions=context.get("extra_instructions", "なし"),
            keywords=keywords_str,
            persona_section=persona_section
        )
        try:
            response = self._client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            text = response.text
            # JSONブロックを抽出
            match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
            if match:
                text = match.group(1)
            data = json.loads(text)
            return {
                "headlines": data.get("headlines", [])[:15],
                "descriptions": data.get("descriptions", [])[:4],
                "generated_by": "gemini",
            }
        except Exception as e:
            print(f"[AdCopyGenerator] Gemini生成失敗、フォールバック使用: {e}")
            return self._generate_fallback(context)

    def _generate_fallback(self, context: dict) -> dict:
        clinic_name = context.get("clinic_name", "〇〇整体院")
        region = context.get("region", "地域名")
        minutes = context.get("station_minutes", "5")

        headlines = [
            h.format(clinic_name=clinic_name, region=region, minutes=minutes)
            for h in FALLBACK_HEADLINES
        ]
        descriptions = [
            d.format(clinic_name=clinic_name, region=region)
            for d in FALLBACK_DESCRIPTIONS
        ]
        return {
            "headlines": headlines,
            "descriptions": descriptions,
            "generated_by": "fallback",
        }
