"""
動画広告台本自動生成モジュール (プロ演出強化版)
Gemini API (google.genai) を使用して、整体院・サロン向けの5ステップ動画広告台本および撮影・ナレーション演出指示を生成する。
"""

import os
import json
import re
import google.genai as genai

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
    指定された5要素および動画の尺、トーンをもとに、
    ①冒頭3秒の撮影指示(視覚フック)、②ナレーション抑揚指示、③オファー限定理由、④画面タップ誘導(CTA)
    を含めた3パターンのプロ仕様動画広告台本を一括生成する。
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
あなたは店舗型集客（整体院・鍼灸院・サロンなど）に特化したYouTube/TikTok/Instagram動画広告のトップマーケター兼映像ディレクターです。
以下の【顧客情報】をもとに、成果の出る動画広告台本を**5ステップ構成 ＋ 4大プロ演出機能（撮影ガイド・声の抑揚・限定理由・画面タップ誘導）付き**で【3パターン】作成してください。

---

### 【顧客情報】
1. **① ターゲットの悩み・症状**: {target_concern}
2. **② 地域名・店舗名・実績の数字**: {location_and_history}
3. **③ 他店にない目新しさ・独自の強み (USP)**: {usp_feature}
4. **④ なぜ効くのかの理由・メカニズム (豆知識)**: {reason_mechanism}
5. **⑤ 初回限定オファー・価格条件**: {offer_detail}

### 【制約条件 ＆ 4大プロ演出ルール】
1. **動画の尺**: {duration_name}（ナレーション全体の合計文字数は **{min_char}文字〜{max_char}文字** の範囲に納めてください）
2. **トーン＆マナー**: {selected_tone}
3. **4大プロ演出機能の適用**:
   - **① 冒頭3秒の撮影指示（視覚フック）**: 視聴者のスクロールを指で止めるための具体的なカメラ撮影指示・演技指示を冒頭に明記する。（例: 『【撮影】首を痛そうに叩いて顔をしかめるシーンを画面一杯にアップ』）
   - **② ナレーション抑揚・演出指導**: 話すスピード、トーンの上げ下げ、間の取り方のアドバイスを記載する。（例: 『①フックは早口で焦燥感を出す。③のUSPはトーンを落として秘密を明かすように囁く』）
   - **③ 限定理由 ＆ 予約ハードル低下**: ⑤のオファー部分に「なぜ安くするのかの納得理由（例: 技術に自信があるから体験してほしい）」と「予約の簡単さ（例: たった30秒でLINE予約完了）」を必ず盛り込む。
   - **④ 画面ラストのタップ誘導（CTA演出）**: 動画ラストで「画面下部を指さすアニメーション/演出」と「今すぐ下の緑のボタンをタップ！」という強いアクション換起を入れる。
4. **5ステップ構成（①〜⑤）**:
   - **① ターゲット指名（フック）**
   - **② 地域・実績（信頼感）**
   - **③ 目新しさ（USP）**
   - **④ 豆知識・メカニズム（説得力）**
   - **⑤ オファー・限定理由・CTA（行動換起）**

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
      "visual_hook_instruction": "🎬 【冒頭3秒の撮影指示】首を痛そうに叩きながら眉間にシワを寄せる表情のアップ映像",
      "voice_pacing_instruction": "🎵 【ナレーション指導】①はやや早口で焦りや悩みを強調。③の目新しさは声を落ち着かせて秘密を明かすトーンで語る。",
      "screen_cta_instruction": "👇 【画面ラストの演出指示】画面下部を指さす矢印演出を配置し『今すぐ下の緑のボタンから空き枠をチェック！』と強く呼びかける",
      "steps": {{
        "step1_target": {{
          "instruction": "🎬 悩んでいる表情のアップ",
          "script": "首肩がガチガチ、体が重くて限界...って人は絶対見て！"
        }},
        "step2_location": {{
          "instruction": "🎬 店舗外観または看板の映像",
          "script": "藤枝市で開院15年、のべ1万人以上が通った整体院 導があるんです。"
        }},
        "step3_usp": {{
          "instruction": "🎬 酵素風呂や施術のアップ映像",
          "script": "10年熟成の酵素風呂で体を深部から温めてから施術するから違いを即実感！"
        }},
        "step4_reason": {{
          "instruction": "🎬 筋肉がほぐれていくイメージ映像",
          "script": "体が温まった状態で受ける施術だからただのマッサージと全然違う！"
        }},
        "step5_offer": {{
          "instruction": "🎬 オファーテキストテロップ＋画面下の指さし",
          "script": "当院の技術を知ってほしいから通常13,000円が初回たったの9,900円！たった30秒でLINE予約できます。今すぐ下のボタンをタップ！"
        }}
      }},
      "full_script_with_instructions": "【演出付き台本全文...】",
      "narration_only_script": "【ナレーションセリフのみ全文...】",
      "text_overlay_suggestions": [
        "テロップ1: 首肩ガチガチで限界な人へ！",
        "テロップ2: 藤枝市で実績15年の整体院 導",
        "テロップ3: 初回体験 9,900円 (LINE予約30秒)"
      ]
    }},
    {{
      "pattern_id": "pattern_b",
      "pattern_name": "衝撃・インパクト比較型",
      "concept": "『ただのマッサージとは違う』違いを強調して離脱を防ぐインパクト型",
      "estimated_seconds": {duration_name.replace("秒", "")},
      "total_characters": 165,
      "visual_hook_instruction": "...",
      "voice_pacing_instruction": "...",
      "screen_cta_instruction": "...",
      "steps": {{
        "step1_target": {{ "instruction": "...", "script": "..." }},
        "step2_location": {{ "instruction": "...", "script": "..." }},
        "step3_usp": {{ "instruction": "...", "script": "..." }},
        "step4_reason": {{ "instruction": "...", "script": "..." }},
        "step5_offer": {{ "instruction": "...", "script": "..." }}
      }},
      "full_script_with_instructions": "...",
      "narration_only_script": "...",
      "text_overlay_suggestions": [...]
    }},
    {{
      "pattern_id": "pattern_c",
      "pattern_name": "専門家・説得型",
      "concept": "効果の出る理由と実績を軸に説得力を極限まで高めた信頼型",
      "estimated_seconds": {duration_name.replace("秒", "")},
      "total_characters": 155,
      "visual_hook_instruction": "...",
      "voice_pacing_instruction": "...",
      "screen_cta_instruction": "...",
      "steps": {{
        "step1_target": {{ "instruction": "...", "script": "..." }},
        "step2_location": {{ "instruction": "...", "script": "..." }},
        "step3_usp": {{ "instruction": "...", "script": "..." }},
        "step4_reason": {{ "instruction": "...", "script": "..." }},
        "step5_offer": {{ "instruction": "...", "script": "..." }}
      }},
      "full_script_with_instructions": "...",
      "narration_only_script": "...",
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

    try:
        return json.loads(raw_text)
    except Exception as e:
        raise ValueError(f"AIからのレスポンス解析に失敗しました: {e}\n生レスポンス: {raw_text[:200]}")
