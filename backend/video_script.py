"""
動画広告台本自動生成モジュール
Gemini API (google.genai) を使用して、整体院・サロン向けの5ステップ動画広告台本を生成する。
"""

import os
import json
import re
import google.genai as genai

# 動画の尺と推奨文字数マッピング（朗読スピード: 1秒あたり約5文字）
DURATION_SPECS = {
    "15s": {"name": "15秒", "min_char": 70, "max_char": 95, "desc": "超テンポ重視。要点を凝縮した短尺ショート動画"},
    "30s": {"name": "30秒", "min_char": 140, "max_char": 185, "desc": "【標準型】5ステップ全要素をバランス良く伝える定番構成"},
    "60s": {"name": "60秒", "min_char": 280, "max_char": 370, "desc": "【ストーリー型】悩みや施術理由を深掘りし共感度を高める動画"},
    "90s": {"name": "90秒", "min_char": 430, "max_char": 550, "desc": "【解説・教育型】丁寧な説明で信頼感と来院意欲を最大化する動画"},
}

def generate_video_scripts(
    target_concern: str,
    location_and_history: str,
    usp_feature: str,
    reason_mechanism: str,
    offer_detail: str,
    duration_key: str = "30s",
    tone_manner: str = "friendly"
) -> dict:
    """
    指定された5要素および動画の尺、トーンをもとに3パターンの動画広告台本を生成する。
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")

    spec = DURATION_SPECS.get(duration_key, DURATION_SPECS["30s"])
    duration_name = spec["name"]
    min_char = spec["min_char"]
    max_char = spec["max_char"]

    tone_instructions = {
        "friendly": "親しみやすく、共感性の高い口調（例: 『〜って人は絶対見て！』『〜なんです！』）",
        "professional": "頼れる専門家・プロとしての誠実で安心感のある口調（例: 『〜でお悩みの方へ』『当院では〜』）",
        "passionate": "インパクト重視で情熱的な引きの強い口調（例: 『まだ〜で消耗してるの？』『これ知らないと損！』）",
    }
    selected_tone = tone_instructions.get(tone_manner, tone_instructions["friendly"])

    prompt = f"""
あなたは店舗型集客（整体院・鍼灸院・サロンなど）に特化したYouTube/TikTok/Instagram動画広告のトップWebマーケター・コピーライターです。
以下の【顧客情報】をもとに、成果の出る動画広告台本を**必ず指定の5ステップ構成**で【3パターン】作成してください。

---

### 【顧客情報】
1. **① ターゲットの悩み・症状**: {target_concern}
2. **② 地域名・店舗名・実績の数字**: {location_and_history}
3. **③ 他店にない目新しさ・独自の強み (USP)**: {usp_feature}
4. **④ なぜ効くのかの理由・メカニズム (豆知識)**: {reason_mechanism}
5. **⑤ 初回限定オファー・価格条件**: {offer_detail}

### 【制約条件】
- **動画の尺**: {duration_name}（全体の合計文字数は **{min_char}文字〜{max_char}文字** の範囲に厳密に収めてください。1秒あたり約5文字ペースのナレーション想定です）
- **トーン＆マナー**: {selected_tone}
- **5ステップ構成ルール**（必ず以下の①〜⑤の順番・役割を守ってください）:
  - **① ターゲット指名（フック）**: 冒頭で「〜で悩んでいる人」を呼びかけ画面に引き付ける。
  - **② 地域・実績（信頼感）**: 店舗の地域名・店舗名・実績数字を伝え信頼を与える。
  - **③ 目新しさ（USP）**: 他店にはない独自の施術や特徴を伝え興味を惹く。
  - **④ 豆知識・メカニズム（納得感）**: なぜ効果が出るのかの理由・仕組みを説得力をもって説明する。
  - **⑤ オファー・CTA**: 初回限定価格や条件を提示し今すぐの予約・アクションを促す。

---

### 【出力フォーマット】
以下のJSONフォーマットのみを返してください。markdownコードブロック（```json ... ```）も含めて構いません。

{{
  "duration_key": "{duration_key}",
  "duration_name": "{duration_name}",
  "patterns": [
    {{
      "pattern_id": "pattern_a",
      "pattern_name": "共感・寄り添い型",
      "concept": "悩みへの深い共感から始まり自然と予約に繋げる王道パターン",
      "estimated_seconds": {duration_name.replace("秒", "")},
      "total_characters": 160,
      "steps": {{
        "step1_target": "① ターゲット指名パートのセリフ",
        "step2_location": "② 地域・実績パートのセリフ",
        "step3_usp": "③ 目新しさパートのセリフ",
        "step4_reason": "④ 豆知識・メカニズムパートのセリフ",
        "step5_offer": "⑤ オファーパートのセリフ"
      }},
      "full_script": "①〜⑤の全セリフをつなげた文章",
      "text_overlay_suggestions": [
        "テロップ提案1: 画面に大きく出す文字（例: 首肩ガチガチの方へ！）",
        "テロップ提案2: （例: 藤枝市で実績36年サロン）",
        "テロップ提案3: （例: 初回特別体験 9,900円）"
      ]
    }},
    {{
      "pattern_id": "pattern_b",
      "pattern_name": "衝撃・インパクト比較型",
      "concept": "『ただのマッサージとは違う』違いを強調して離脱を防ぐインパクト型",
      "estimated_seconds": {duration_name.replace("秒", "")},
      "total_characters": 165,
      "steps": {{
        "step1_target": "...",
        "step2_location": "...",
        "step3_usp": "...",
        "step4_reason": "...",
        "step5_offer": "..."
      }},
      "full_script": "...",
      "text_overlay_suggestions": [...]
    }},
    {{
      "pattern_id": "pattern_c",
      "pattern_name": "専門家・説得型",
      "concept": "効果の出る理由と実績を軸に説得力を極限まで高めた信頼型",
      "estimated_seconds": {duration_name.replace("秒", "")},
      "total_characters": 155,
      "steps": {{
        "step1_target": "...",
        "step2_location": "...",
        "step3_usp": "...",
        "step4_reason": "...",
        "step5_offer": "..."
      }},
      "full_script": "...",
      "text_overlay_suggestions": [...]
    }}
  ]
}}
"""

    gc = genai.Client(api_key=api_key)
    response = gc.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    raw_text = response.text or ""

    # JSON抽出処理
    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # JSON直接デコード試行
    try:
        return json.loads(raw_text)
    except Exception as e:
        raise ValueError(f"AIからのレスポンス解析に失敗しました: {e}\n生レスポンス: {raw_text[:200]}")
