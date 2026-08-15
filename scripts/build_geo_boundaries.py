#!/usr/bin/env python3
"""
e-Stat 国勢調査 小地域（町丁・字等）境界データ → 市区町村別 GeoJSON 変換パイプライン

1. e-Stat から都道府県の Shapefile をダウンロード
2. geopandas で読み込み、市区町村コード別に分割
3. 座標精度を丸めてファイルサイズ最適化
4. frontend/geo/{pref_code}/{city_code}.json として出力
"""

import os
import sys
import json
import zipfile
import urllib.request
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, "..")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "frontend", "geo")
TEMP_DIR = os.path.join(PROJECT_DIR, "temp_shapefiles")

COORD_PRECISION = 6


def round_coords(coords, precision=COORD_PRECISION):
    """座標配列を再帰的に丸める"""
    if isinstance(coords, (list, tuple)):
        if len(coords) > 0 and isinstance(coords[0], (int, float)):
            return [round(c, precision) for c in coords]
        return [round_coords(c, precision) for c in coords]
    return coords


def download_shapefile(pref_code):
    """e-Stat から Shapefile ZIP をダウンロード"""
    os.makedirs(TEMP_DIR, exist_ok=True)
    zip_path = os.path.join(TEMP_DIR, f"r2ka{pref_code:02d}.zip")
    
    if os.path.exists(zip_path):
        print(f"  [SKIP] {zip_path} は既にダウンロード済み")
        return zip_path
    
    url = f"https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code={pref_code:02d}&coordSys=1&format=shape&downloadType=5"
    
    try:
        print(f"  [DL] {url}")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get('Content-Type', '')
            data = resp.read()
            if len(data) > 1000:  # ZIP は通常 1KB 以上
                with open(zip_path, 'wb') as f:
                    f.write(data)
                print(f"  [OK] ダウンロード完了: {zip_path} ({len(data)/1024:.0f} KB)")
                return zip_path
            else:
                print(f"  [WARN] データが小さすぎます ({len(data)} bytes)")
    except Exception as e:
        print(f"  [ERR] {e}")
    
    return None


def convert_shapefile_to_geojson(pref_code):
    """Shapefile を市区町村コード別 GeoJSON に変換"""
    import geopandas as gpd
    
    zip_path = os.path.join(TEMP_DIR, f"r2ka{pref_code:02d}.zip")
    if not os.path.exists(zip_path):
        print(f"  [SKIP] Shapefile が見つかりません: {zip_path}")
        return 0
    
    extract_dir = os.path.join(TEMP_DIR, f"pref_{pref_code:02d}")
    os.makedirs(extract_dir, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_dir)
    
    shp_files = glob.glob(os.path.join(extract_dir, "**", "*.shp"), recursive=True)
    if not shp_files:
        print(f"  [ERR] .shp ファイルが見つかりません")
        return 0
    
    shp_path = shp_files[0]
    print(f"  [READ] {shp_path}")
    
    gdf = gpd.read_file(shp_path, encoding='cp932')
    print(f"  [INFO] カラム: {list(gdf.columns)}")
    print(f"  [INFO] 行数: {len(gdf)}")
    
    # KEY_CODE の先頭5桁が市区町村コード
    key_col = None
    name_col = None
    for col in gdf.columns:
        cu = col.upper()
        if cu in ('KEY_CODE',): key_col = col
        if cu in ('S_NAME', 'MOJI'): name_col = col
    
    if key_col is None:
        print(f"  [ERR] KEY_CODE列が見つかりません")
        for col in gdf.columns:
            print(f"    {col}: {gdf[col].iloc[0] if len(gdf) > 0 else ''}")
        return 0
    
    gdf['_city_code'] = gdf[key_col].astype(str).str[:5]
    
    pref_dir = os.path.join(OUTPUT_DIR, f"{pref_code:02d}")
    os.makedirs(pref_dir, exist_ok=True)
    
    count = 0
    for city_code, group in gdf.groupby('_city_code'):
        features = []
        for _, row in group.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            
            name = str(row[name_col]) if name_col else ""
            if name.strip() in ('', 'nan', 'None'):
                continue
            
            # GeoJSON Feature を生成
            geom_geojson = json.loads(gpd.GeoSeries([geom]).to_json())["features"][0]["geometry"]
            geom_geojson["coordinates"] = round_coords(geom_geojson["coordinates"])
            
            # centroid（中心座標）を計算
            centroid = geom.centroid
            
            features.append({
                "type": "Feature",
                "properties": {
                    "name": name.strip(),
                    "lat": round(centroid.y, 6),
                    "lng": round(centroid.x, 6)
                },
                "geometry": geom_geojson
            })
        
        if not features:
            continue
        
        geojson = {"type": "FeatureCollection", "features": features}
        
        out_path = os.path.join(pref_dir, f"{city_code}.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, ensure_ascii=False, separators=(',', ':'))
        
        size_kb = os.path.getsize(out_path) / 1024
        print(f"  [OUT] {city_code}.json ({len(features)} 町丁字, {size_kb:.1f} KB)")
        count += 1
    
    return count


def main():
    if len(sys.argv) > 1:
        pref_codes = [int(x) for x in sys.argv[1:]]
    else:
        pref_codes = list(range(1, 48))
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    total = 0
    for pc in pref_codes:
        names = {1:"北海道",2:"青森",3:"岩手",4:"宮城",5:"秋田",6:"山形",7:"福島",
                 8:"茨城",9:"栃木",10:"群馬",11:"埼玉",12:"千葉",13:"東京",14:"神奈川",
                 15:"新潟",16:"富山",17:"石川",18:"福井",19:"山梨",20:"長野",21:"岐阜",
                 22:"静岡",23:"愛知",24:"三重",25:"滋賀",26:"京都",27:"大阪",28:"兵庫",
                 29:"奈良",30:"和歌山",31:"鳥取",32:"島根",33:"岡山",34:"広島",35:"山口",
                 36:"徳島",37:"香川",38:"愛媛",39:"高知",40:"福岡",41:"佐賀",42:"長崎",
                 43:"熊本",44:"大分",45:"宮崎",46:"鹿児島",47:"沖縄"}
        print(f"\n{'='*50}\n[{pc:02d}] {names.get(pc,'?')}\n{'='*50}")
        
        result = download_shapefile(pc)
        if result:
            count = convert_shapefile_to_geojson(pc)
            total += count
            print(f"  [DONE] {count} 市区町村")
    
    print(f"\n合計 {total} 市区町村の GeoJSON 生成完了 → {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
