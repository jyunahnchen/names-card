import json
import os
from pyairtable import Table
# from airtable_parser import parse_text_data # 假設您自己寫的解析模組

# --- 安全讀取憑證 ---
# 從 Netlify 環境變數中讀取 API Key 和 Base ID
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = "您的表格名稱" # 例如: "名片資料"


def handler(event, context):
    try:
        # 1. 接收前端傳來的資料
        body = json.loads(event['body'])
        raw_text = body.get('text', '')

        if not raw_text:
            return {'statusCode': 400, 'body': json.dumps({'message': '未提供文字內容'})}

        # 2. 【核心解析邏輯】
        # 這裡需要執行您先前規劃的解析步驟，將 raw_text 轉換為一個列表，
        # 每個元素是一個字典，包含 9 個欄位 (含一對多的拆分)
        
        # 假設 parse_text_data(raw_text) 會返回如下格式:
        # parsed_records = [
        #    {'公司名稱': '有泓創意', '姓名': '洪雨彤 Ivy Hung', ...},
        #    {'公司名稱': '有泓創意', '姓名': '吳至軒 Edward Wu', ...},
        #    ...
        # ]
        parsed_records = your_custom_parser(raw_text) 
        
        # 3. 準備 Airtable 寫入
        table = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)
        
        # 4. 執行寫入 (使用 pyairtable 的 create 方法)
        # pyairtable 需要一個 {field_name: value} 的字典列表
        table.batch_create(parsed_records)
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': f'成功寫入 {len(parsed_records)} 筆資料到 Airtable'})
        }

    except Exception as e:
        print(f"寫入失敗: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': f'伺服器錯誤: {str(e)}'})
        }

# (註: 您的文字解析函數 your_custom_parser 需要另外編寫)