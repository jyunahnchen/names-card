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
# 處理您提供的結構化文字，並將每張名片拆分成單筆紀錄
# --------------------------------------------------------------------
def parse_text_data(raw_text):
    """將文字資料串解析為 Airtable 紀錄列表"""
    
    import re
    
    parsed_records = []
    
    # 使用 regex 提取每個名片區塊
    card_blocks = re.findall(r'(### 名片[一二三四五六七八九十]+：.*?)(?=---|$)', raw_text, re.DOTALL)
    
    for block in card_blocks:
        if not block.strip():
            continue
        
        card_info = {}
        lines = block.split('\n')
        
        for line in lines:
            line = line.strip()
            if '：' in line and not line.startswith('**備註：'):
                try:
                    field, value = line.split('：', 1)
                    field = field.strip()
                    if field.lower() == 'email':
                        field = 'Email'
                    value = value.strip().replace('**', '')
                    if field in FIELDS:
                        card_info[field] = value.strip()
                except ValueError:
                    continue
        
        # 如果沒有姓名，跳過
        if not card_info.get('姓名'):
            continue
        
        # 正規化每個欄位
        record = {}
        for field in FIELDS:
            record[field] = normalize_field(field, card_info.get(field, ''))
        
        parsed_records.append(record)
    
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