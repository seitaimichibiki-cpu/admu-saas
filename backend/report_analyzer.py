import os
import tempfile
import json
import google.genai as genai

class ReportAnalyzer:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY が設定されていません")
        self._client = genai.Client(api_key=api_key)
        self.model = None  # Client形式では不要
        # 複雑なレポート解析には推論能力の高い Gemini 1.5 Pro を推奨
        

    def analyze_file(self, file_content: bytes, file_name: str, mime_type: str, persona: dict = None) -> dict:
        """アップロードされたレポートファイル（PDF/CSV等）をGeminiで解析する"""
        
        # 拡張子を取得して一時ファイルを作成
        ext = os.path.splitext(file_name)[1]
        if not ext:
            if "pdf" in mime_type:
                ext = ".pdf"
            elif "csv" in mime_type:
                ext = ".csv"
            else:
                ext = ".txt"
                
        # Gemini APIはローカルファイルパスを求めるため一時保存
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name

        uploaded_file = None
        try:
            # Geminiへファイルをアップロード
            uploaded_file = genai.upload_file(path=temp_path, mime_type=mime_type)
            
            persona_text = ""
            if persona and any(persona.values()):
                persona_text = f"""
## 【重要】ターゲットペルソナ設定
本システムでは以下の患者層をターゲットとしています。解析・提案時は、この層と「ズレている」検索語句（安価でもターゲット外のもの）を厳しく「除外推奨（wasted_spend）」に分類してください。
- 年齢・性別: {persona.get('target_age_gender', '未設定')}
- 職業・ライフスタイル: {persona.get('target_job_lifestyle', '未設定')}
- 深い悩み: {persona.get('target_pain_point', '未設定')}
- 求める理想（ゴール）: {persona.get('target_desired_outcome', '未設定')}
"""

            prompt = f"""
あなたはプロのGoogle広告コンサルタントです。
与えられた広告レポートデータ（過去の実績）を詳細に解析し、以下の4つの項目について具体的な知見を抽出してください。
{persona_text}

出力は必ず以下のキーを持つJSONフォーマットのみとしてください。Markdown修飾(```json等)は除外した、そのままパース可能な純粋なJSON文字列を返してください。

{{
    "good_keywords": ["成果の良かったキーワード1", "キーワード2"],
    "wasted_spend": ["無駄な費用が発生している配信先やキーワード1", "キーワード2"],
    "demographic_trends": "コンバージョン率が高い地域、年齢層、デバイスなどの傾向（テキスト）",
    "recommendation": "次の初動キャンペーン構築への具体的な推奨設定やアドバイス（テキスト）"
}}
"""
            # 解析実行
            response = self._client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            
            # JSONパース
            try:
                result = json.loads(response.text)
                return result
            except json.JSONDecodeError:
                # フェイルセーフ：JSON以外が返ってきた場合は適当にラップする
                return {
                    "good_keywords": [],
                    "wasted_spend": [],
                    "demographic_trends": "解析結果のフォーマットエラー",
                    "recommendation": response.text
                }
        except Exception as e:
            return {
                "good_keywords": [],
                "wasted_spend": [],
                "demographic_trends": f"API通信エラー: {str(e)}",
                "recommendation": "ファイルの形式がサポートされていないか、一時的なエラーが発生しました。"
            }
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if uploaded_file:
                try:
                    genai.delete_file(uploaded_file.name)
                except Exception:
                    pass
