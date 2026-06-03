import os
import sys
import json
from pathlib import Path

# backend ディレクトリをシステムパスに追加
sys.path.append(os.path.dirname(__file__))

import db
import logiction_integration as li

def _age_to_group(age) -> str:
    if age is None: return "不明"
    if age < 25: return "18-24歳"
    if age < 35: return "25-34歳"
    if age < 45: return "35-44歳"
    if age < 55: return "45-54歳"
    if age < 65: return "55-64歳"
    return "65歳以上"

def parse_birthday_to_age(bday_str) -> int:
    if not bday_str:
        return None
    try:
        parts = str(bday_str).split("-")
        if len(parts) >= 1:
            birth_year = int(parts[0])
            return 2026 - birth_year
    except:
        pass
    return None

def extract_pref(address) -> str:
    if not address:
        return None
    for p in ["東京都", "北海道", "京都府", "大阪府"]:
        if address.startswith(p):
            return p
    for suffix in ["県"]:
        idx = address.find(suffix)
        if idx != -1 and idx <= 4:
            return address[:idx+1]
    return None

def import_data():
    json_path = Path(__file__).parent / "logiction_backup.json"
    if not json_path.exists():
        print(f"❌ Error: {json_path} が見つかりません。")
        return

    print(f"📖 {json_path} を読み込み中...")
    with open(json_path, "r", encoding="utf-8") as f:
        backup = json.load(f)

    patients = backup.get("tables", {}).get("patients", [])
    if not patients:
        print("❌ Error: JSON 内に 'tables.patients' が存在しないか、空です。")
        return

    print(f"📊 {len(patients)} 件の患者データを検出しました。インポートを開始します...")

    conn = db.get_conn()
    cur = conn.cursor()

    synced = 0
    errors = 0

    for idx, p in enumerate(patients):
        pid = p.get("id")
        if not pid:
            continue

        gender_raw = p.get("gender", "")
        gender = "male" if gender_raw == "男" else "female" if gender_raw == "女" else None

        age = parse_birthday_to_age(p.get("birthday") or p.get("dob"))
        age_group = _age_to_group(age)

        address = p.get("address", "")
        pref = extract_pref(address)
        city = address.replace(pref, "") if pref and address else address

        complaint = p.get("chiefComplaint", "")
        symptoms = [complaint] if complaint else []
        symptoms_json = json.dumps(symptoms, ensure_ascii=False)

        visit_count = p.get("visitCount", 0)
        ltv = p.get("ltv", 0)
        channel = p.get("media", "不明")
        first_visit = p.get("firstVisitDate") or p.get("createdAt")

        ph = "%s" if db.USE_PG else "?"

        try:
            if db.USE_PG:
                cur.execute("""
                    INSERT INTO logiction_patients
                      (clinic_id, patient_id, gender, age, age_group,
                       address_pref, address_city, symptoms,
                       visit_count, total_revenue, ltv_yen,
                       acquisition_channel, gclid, first_visit_date)
                    VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                    ON CONFLICT(clinic_id, patient_id) DO UPDATE SET
                      gender=EXCLUDED.gender, age=EXCLUDED.age,
                      age_group=EXCLUDED.age_group,
                      address_pref=EXCLUDED.address_pref,
                      address_city=EXCLUDED.address_city,
                      symptoms=EXCLUDED.symptoms,
                      visit_count=EXCLUDED.visit_count,
                      total_revenue=EXCLUDED.total_revenue,
                      ltv_yen=EXCLUDED.ltv_yen,
                      acquisition_channel=EXCLUDED.acquisition_channel,
                      first_visit_date=EXCLUDED.first_visit_date,
                      synced_at=NOW()
                """, (
                    pid, gender, age, age_group,
                    pref, city, symptoms_json,
                    visit_count, ltv, ltv,
                    channel, first_visit
                ))
            else:
                cur.execute("""
                    INSERT INTO logiction_patients
                      (clinic_id, patient_id, gender, age, age_group,
                       address_pref, address_city, symptoms,
                       visit_count, total_revenue, ltv_yen,
                       acquisition_channel, gclid, first_visit_date)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(clinic_id, patient_id) DO UPDATE SET
                      gender=excluded.gender, age=excluded.age,
                      age_group=excluded.age_group,
                      address_pref=excluded.address_pref,
                      address_city=excluded.address_city,
                      symptoms=excluded.symptoms,
                      visit_count=excluded.visit_count,
                      total_revenue=excluded.total_revenue,
                      ltv_yen=excluded.ltv_yen,
                      acquisition_channel=excluded.acquisition_channel,
                      first_visit_date=excluded.first_visit_date,
                      synced_at=datetime('now','localtime')
                """, (
                    pid, gender, age, age_group,
                    pref, city, symptoms_json,
                    visit_count, ltv, ltv,
                    channel, first_visit
                ))
            synced += 1
        except Exception as ex:
            errors += 1
            print(f"⚠️ [Row {idx}] Sync error patient_id={pid}: {ex}")

    conn.commit()
    print(f"🎉 インポート完了: 成功 {synced} 件, エラー {errors} 件")

    try:
        print("🧠 ペルソナ分析の自動計算を実行中...")
        li._auto_update_persona_from_patients(1, db)
        print("✅ ペルソナ自動計算完了！")
    except Exception as ex:
        print(f"⚠️ ペルソナ自動更新エラー: {ex}")

if __name__ == "__main__":
    import_data()
