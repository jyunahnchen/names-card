import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

# --- 安全讀取憑證 ---
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
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
    card_blocks = re.split(r'(名片[一二三四五六七八九十]+：)', raw_text)
    
    # 過濾掉空字串並重新組合，確保每個名片區塊都包含其標題
    card_pairs = []
    for i in range(1, len(card_blocks), 2):
        if i + 1 < len(card_blocks):
            card_pairs.append(card_blocks[i] + card_blocks[i+1])
    
    parsed_records = []
    
    for block in card_pairs:
        card_info = {}
        
        # 建立所有字段的正規表達式，用於作為下一個字段的停止點
        # 這裡使用非捕獲組 (?:...) 和正向預查 (?=...) 來定義停止點
        # 確保停止點包含中文冒號和英文冒號
        field_delimiters = '|'.join([re.escape(f) + r'[:：]' for f in FIELDS])
        
        for i, field in enumerate(FIELDS):
            # 構建當前字段的匹配模式
            # 匹配 '字段名稱' 後面跟著可選的冒號，然後是非貪婪匹配任意字符直到下一個字段的開頭或區塊結束
            # 如果是最後一個字段，則匹配到區塊結束
            if i < len(FIELDS) - 1:
                # 下一個字段的標記作為停止點
                next_field_pattern = re.escape(FIELDS[i+1]) + r'[:：]'
                # 匹配當前字段的值，直到下一個字段的標記出現
                match = re.search(f'{re.escape(field)}[:：](.*?)(?={next_field_pattern}|\n\n|\Z)', block, re.DOTALL)
            else:
                # 如果是最後一個字段 (Email)，則匹配到區塊結束
                match = re.search(f'{re.escape(field)}[:：](.*)', block, re.DOTALL)
            
            value = ''
            if match:
                value = match.group(1).strip()
                # 移除值中可能包含的下一個字段的名稱（如果匹配到了）
                if i < len(FIELDS) - 1:
                    next_field_name = FIELDS[i+1]
                    if value.startswith(next_field_name):
                        value = '' # 如果值以其他字段名稱開頭，說明匹配錯誤，將其視為空

            card_info[field] = normalize_field(field, value)

        # 3. 處理一對多 (多個人名) 的拆分邏輯
        # 這裡假設姓名、職稱、手機、Email 都是以 '/' 或 ' ' 分隔的多個值
        names_raw = card_info.get('姓名', '')
        titles_raw = card_info.get('職稱', '')
        mobiles_raw = card_info.get('手機', '')
        emails_raw = card_info.get('Email', '')

        # 使用更精確的分隔符號，避免誤分
        names = [normalize_field('姓名', n.strip()) for n in re.split(r'[\s/、]', names_raw) if n.strip()]
        titles = [normalize_field('職稱', t.strip()) for t in re.split(r'[\s/、]', titles_raw) if t.strip()]
        mobiles = [normalize_field('手機', m.strip()) for m in re.split(r'[\s/、]', mobiles_raw) if m.strip()]
        emails = [normalize_field('Email', e.strip()) for e in re.split(r'[\s/、]', emails_raw) if e.strip()]

        names = [n for n in names if n]
        titles = [t for t in titles if t]
        mobiles = [m for m in mobiles if m]
        emails = [e for e in emails if e]
        
        if not names:
            # 如果沒有姓名，但有其他資訊，也嘗試建立一筆紀錄
            if any(card_info.values()):
                record = {}
                for field in FIELDS:
                    record[field] = normalize_field(field, card_info.get(field, ''))
                parsed_records.append(record)
            continue

        # 4. 建立最終的紀錄
        for i, name in enumerate(names):
            record = {}
            # 寫入共用資訊
            for field in ['公司名稱', '地址', '統一編號', '公司電話', '傳真']:
                record[field] = normalize_field(field, card_info.get(field, ''))

            # 寫入個人資訊 (按順序匹配，如果個人資訊數量不夠，則留空)
            record['姓名'] = name
            record['職稱'] = titles[i] if i < len(titles) else ''
            record['手機'] = mobiles[i] if i < len(mobiles) else ''
            record['Email'] = emails[i] if i < len(emails) else ''

            parsed_records.append({k: normalize_field(k, v) for k, v in record.items()})

    return parsed_records


# --------------------------------------------------------------------
# Netlify Function 入口點 (不變)
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
            'body': json.dumps({'message': f'伺服器內部錯誤 (請檢查 Netlify Logs): {str(e)}'})}


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


def clean_markdown(value):
    """移除常見 Markdown 標記，避免寫入 Airtable 時夾帶符號"""
    if not isinstance(value, str):
        return value

    cleaned = value
    cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    cleaned = re.sub(r'__([^_]+)__', r'\1', cleaned)
    cleaned = re.sub(r'_([^_]+)_', r'\1', cleaned)
    cleaned = re.sub(r'~~([^~]+)~~', r'\1', cleaned)
    cleaned = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 \2', cleaned)
    cleaned = re.sub(r'mailto:\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^\s*#+\s*', '', cleaned.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*([-*+]\s+|\d+\.\s+)', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = cleaned.replace('|', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned)

    return cleaned.strip()


def normalize_field(field, value):
    if not value:
        return ''

    cleaned = clean_markdown(value)

    # 移除無效佔位符
    invalid_placeholders = ['名片上未顯示', '未顯示', '未填公司', '未填姓名', '(未顯示)', '：'] # 新增冒號作為無效佔位符
    if cleaned.strip() in invalid_placeholders:
         return ''
    
    # 修正：不再檢查值是否包含其他字段名稱，因為這應該由解析邏輯處理
    # if field != 'Email': # Email 字段可能包含其他字段名稱的子串，不應過濾
    #     for marker in FIELDS:
    #         if marker != field and marker in cleaned:
    #             return ''

    if field == 'Email':
        emails = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', cleaned)
        return emails[0] if emails else ''

    if field in ('手機', '公司電話'):
        digits = re.sub(r'[^0-9+]', '', cleaned)
        return digits if digits else ''

    cleaned = cleaned.strip('-_. ,;:/\\') # 移除更多可能的標點符號
    if not cleaned:
        return ''

    # 確保值中包含至少一個字母、數字或中文字符，避免純標點符號或空白被視為有效值
    if not re.search(r'[A-Za-z0-9\u4e00-\u9fff]', cleaned):
        return ''

    return cleaned

