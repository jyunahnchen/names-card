import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

# --- 安全讀取憑證 ---
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
# ⚠️ 請務必將 "您的表格名稱" 替換成您在 Airtable 中建立的 Table 名稱
AIRTABLE_TABLE_NAME = "名片王" 

# 定義您需要的 9 個關鍵欄位
FIELDS = [
    '公司名稱', '地址', '統一編號', '公司電話', '傳真', 
    '職稱', '姓名', '手機', 'Email'
]

# --------------------------------------------------------------------
# 【核心解析邏輯】
# 處理您提供的結構化文字，並將一對多名片拆分成多筆紀錄
# --------------------------------------------------------------------
def parse_text_data(raw_text):
    """將文字資料串解析為 Airtable 紀錄列表 (處理一對多拆分)"""
    
    # 1. 以「名片一：」「名片二：」等作為分隔符切分名片區塊
    # 使用 regex 確保能切分並保留分隔符
    card_blocks = re.split(r'(名片[一二三四五六七八九十]+：)', raw_text)[1:]
    
    # 將切分後的結果 (分隔符, 內容, 分隔符, 內容...) 組合成 [名片一：內容, 名片二：內容]
    card_pairs = [card_blocks[i] + card_blocks[i+1] for i in range(0, len(card_blocks), 2)]
    
    parsed_records = []
    
    for block in card_pairs:
        # 用來儲存從單一區塊解析出的所有資訊
        card_info = {}
        
        # 2. 提取單筆/共用資訊
        for field in FIELDS:
            # 找到 項目內容 + 關鍵字 + 值 的模式
            # 這裡使用 item_pattern 來匹配 '項目內容' '公司名稱' '值' 這種結構
            match = re.search(f'{field}(.+?)(?=職稱|姓名|手機|Email|名片[一二三四五六七八九十]+：|$)', block, re.DOTALL)
            
            if match:
                # 提取並清理值，去除 '項目內容' 或其他的表格文字
                value = match.group(1).split('項目內容')[-1].strip().replace('\n', ' ')
                
                # 移除所有在值開頭出現的欄位名稱 (這是因為您的輸入格式中，'項目內容'後面可能會跟著欄位名稱)
                for f in FIELDS:
                    value = value.replace(f, '').strip()

                card_info[field] = value

        # 3. 處理一對多 (多個人名) 的拆分邏輯
        names = [n.strip() for n in card_info.get('姓名', '').split('/') if n.strip()]
        
        if not names:
            continue # 如果沒有姓名，跳過這張名片

        # 取得多人的職稱、手機、Email 資訊
        titles = [t.strip() for t in re.split(r' \/ | / |\n', card_info.get('職稱', '')) if t.strip()]
        mobiles = [m.strip() for m in re.split(r' \(|\) | / ', card_info.get('手機', '')) if m.strip() and re.search(r'\d', m)]
        emails = [e.strip() for e in re.split(r' \(|\) | / ', card_info.get('Email', '')) if e.strip() and '@' in e]

        # 4. 建立最終的紀錄
        for i, name in enumerate(names):
            record = {}
            # 寫入共用資訊
            for field in ['公司名稱', '地址', '統一編號', '公司電話', '傳真']:
                record[field] = card_info.get(field, '')

            # 寫入個人資訊 (按順序匹配)
            record['姓名'] = name
            record['職稱'] = titles[i] if i < len(titles) else titles[0] if titles else '' # 嘗試按序取，若無則取第一個
            record['手機'] = mobiles[i] if i < len(mobiles) else ''
            record['Email'] = emails[i] if i < len(emails) else ''
            
            parsed_records.append(record)

    return parsed_records


# --------------------------------------------------------------------
# Netlify Function 入口點
# --------------------------------------------------------------------
def handler(event, context):
    # 檢查憑證是否已從 Netlify 環境變數載入
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        return {'statusCode': 500, 'body': json.dumps({'message': '後端憑證未設定 (AIRTABLE_API_KEY 或 BASE_ID 遺失)。請檢查 Netlify 環境變數。'})}
    
    try:
        # 1. 接收前端傳來的資料
        body = json.loads(event['body'])
        raw_text = body.get('text', '')

        if not raw_text:
            return {'statusCode': 400, 'body': json.dumps({'message': '未提供文字內容'})}

        # 2. 執行核心解析邏輯
        parsed_records = parse_text_data(raw_text) 
        
        if not parsed_records:
             return {
                'statusCode': 400,
                'body': json.dumps({'message': '未能從輸入文字中解析出任何有效名片資訊。請檢查輸入格式。'})
            }
        
        # 3. 準備 Airtable 寫入
        write_to_airtable(parsed_records)
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': f'成功寫入 {len(parsed_records)} 筆資料到 Airtable'})
        }

    except json.JSONDecodeError:
        # 處理前端送來的 JSON 格式錯誤
        return {'statusCode': 400, 'body': json.dumps({'message': '前端傳送的資料格式錯誤 (非有效 JSON)。'})}

    except Exception as e:
        # 處理其他伺服器端錯誤，並打印到 Netlify Log 以供偵錯
        print(f"寫入失敗: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': f'伺服器內部錯誤 (請檢查 Netlify Logs): {str(e)}'})
        }


def chunk_records(records, chunk_size=10):
    """將紀錄切分為符合 Airtable API 限制的批次"""
    for i in range(0, len(records), chunk_size):
        yield records[i:i + chunk_size]


def write_to_airtable(records):
    """使用 Airtable REST API 寫入資料，避免外部套件依賴"""
    base_url = "https://api.airtable.com/v0"
    encoded_table = urllib.parse.quote(AIRTABLE_TABLE_NAME)
    url = f"{base_url}/{AIRTABLE_BASE_ID}/{encoded_table}"

    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    for batch in chunk_records(records):
        payload = json.dumps({
            "records": [{"fields": record} for record in batch]
        }).encode("utf-8")

        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request) as response:
                # 讀取回應以確定請求成功，避免 Airtable 回傳錯誤訊息被忽略
                response.read()
        except urllib.error.HTTPError as http_error:
            error_message = _extract_error_message(http_error)
            raise RuntimeError(f"Airtable API HTTP {http_error.code}: {error_message}")
        except urllib.error.URLError as url_error:
            raise RuntimeError(f"無法連線 Airtable: {url_error.reason}")


def _extract_error_message(http_error):
    """從 Airtable HTTPError 解析錯誤訊息"""
    try:
        raw = http_error.read().decode("utf-8")
        if not raw:
            return str(http_error)

        payload = json.loads(raw)
        if isinstance(payload, dict):
            # Airtable 錯誤格式通常為 {"error": {"message": "..."}}
            return payload.get("error", {}).get("message", raw)
        return raw
    except Exception:
        return str(http_error)
