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
    """將文字資料串解析為 Airtable 紀錄列表 (支援冒號分隔)"""
    
    # 1. 以「名片一：」「名片二：」等作為分隔符切分名片區塊
    # 使用 regex 確保能切分並保留分隔符
    card_blocks = re.split(r'(名片[一二三四五六七八九十]+：)', raw_text)[1:]
    
    # 將切分後的結果 (分隔符, 內容, 分隔符, 內容...) 組合成 [名片一：內容, 名片二：內容]
    card_pairs = [card_blocks[i] + card_blocks[i+1] for i in range(0, len(card_blocks), 2)]
    
    parsed_records = []
    
    # 定義卡片分隔符號
    card_delimiter_regex = '名片[一二三四五六七八九十]+：'

    for block in card_pairs:
        # 用來儲存從單一區塊解析出的所有資訊
        card_info = {}
        
        # 2. 提取單筆/共用資訊
        for field in FIELDS:
            
            # 建立一個包含所有其他字段名稱 + 冒號的列表作為分隔標記
            # 【關鍵修正：動態排除當前欄位】
            other_fields_regex = '|'.join([re.escape(f) + r'[:：]' for f in FIELDS if f != field]) 
            
            # 組合完整的 lookahead 模式
            if other_fields_regex:
                 full_delimiter_pattern = f'(?={other_fields_regex}|{card_delimiter_regex}|$)'
            else:
                 full_delimiter_pattern = f'(?={card_delimiter_regex}|$)'


            # 找到 關鍵字 + [可選冒號] + 值 的模式，使用 (.+?) 非貪婪匹配到下一個分隔符
            # 【強制匹配：支持英文/中文冒號並非貪婪捕獲】
            match = re.search(f'{re.escape(field)}[:：]?(.*?)?{full_delimiter_pattern}', block, re.DOTALL)
            
            if match and match.group(1) is not None:
                # 提取值
                value = match.group(1).strip().replace('\n', ' ')

                # 處理 '項目內容' 和 '欄位內容' 等前綴的清理
                for prefix in ['項目內容', '欄位內容']:
                    if value.startswith(prefix):
                        value = value[len(prefix):].strip()

                card_info[field] = normalize_field(field, value)

        # 3. 處理一對多 (多個人名) 的拆分邏輯 (此處邏輯保持不變，仍依賴 split_and_filter 邏輯)
        names = [normalize_field('姓名', n.strip()) for n in card_info.get('姓名', '').split('/') if n.strip()]
        names = [n for n in names if n]
        
        if not names:
            continue # 如果沒有姓名，跳過這張名片

        # 取得多人的職稱、手機、Email 資訊
        titles = [normalize_field('職稱', t) for t in re.split(r' \/ | / |\n', card_info.get('職稱', '')) if t.strip()]
        titles = [t for t in titles if t]
        mobiles = [normalize_field('手機', m) for m in re.split(r' \(|\) | / ', card_info.get('手機', '')) if m.strip()]
        mobiles = [m for m in mobiles if m]
        emails = [normalize_field('Email', e) for e in re.split(r' \(|\) | / ', card_info.get('Email', '')) if e.strip()]
        emails = [e for e in emails if e]

        # 4. 建立最終的紀錄
        for i, name in enumerate(names):
            record = {}
            # 寫入共用資訊
            for field in ['公司名稱', '地址', '統一編號', '公司電話', '傳真']:
                record[field] = normalize_field(field, card_info.get(field, ''))

            # 寫入個人資訊 (按順序匹配)
            record['姓名'] = name
            record['職稱'] = titles[i] if i < len(titles) else titles[0] if titles else '' # 嘗試按序取，若無則取第一個
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
    # 將 '名片上未顯示' 和 '未顯示' 等視為空值
    invalid_placeholders = ['名片上未顯示', '未顯示', '未填公司', '未填姓名', '(未顯示)']
    if cleaned.strip() in invalid_placeholders:
         return ''
    
    # 防止值包含其他字段名稱的終止檢查
    for marker in FIELDS:
        if marker != field and marker in cleaned:
            return ''

    if field == 'Email':
        emails = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', cleaned)
        return emails[0] if emails else ''

    if field in ('手機', '公司電話'):
        digits = re.sub(r'[^0-9+]', '', cleaned)
        return digits if digits else ''

    cleaned = cleaned.strip('-_.,;:/\\ ')
    if not cleaned:
        return ''

    if not re.search(r'[A-Za-z0-9\u4e00-\u9fff]', cleaned):
        return ''

    return cleaned