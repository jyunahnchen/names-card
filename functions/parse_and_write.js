const AIRTABLE_API_KEY = process.env.AIRTABLE_API_KEY;
const AIRTABLE_BASE_ID = process.env.AIRTABLE_BASE_ID;
const AIRTABLE_TABLE_NAME = '名片王';

const FIELDS = [
  '公司名稱',
  '地址',
  '統一編號',
  '公司電話',
  '傳真',
  '職稱',
  '姓名',
  '手機',
  'Email'
];

const CARD_SPLIT_REGEX = /(名片[一二三四五六七八九十]+：)/;
const FIELD_LOOKAHEAD = '(?=職稱|姓名|手機|Email|名片[一二三四五六七八九十]+：|$)';

exports.handler = async function handler(event) {
  if (!AIRTABLE_API_KEY || !AIRTABLE_BASE_ID) {
    return jsonResponse(500, {
      message: '後端憑證未設定 (AIRTABLE_API_KEY 或 BASE_ID 遺失)。請檢查 Netlify 環境變數。'
    });
  }

  let payload;
  try {
    payload = event.body ? JSON.parse(event.body) : {};
  } catch (error) {
    return jsonResponse(400, { message: '前端傳送的資料格式錯誤 (非有效 JSON)。' });
  }

  const rawText = (payload.text || '').trim();
  if (!rawText) {
    return jsonResponse(400, { message: '未提供文字內容' });
  }

  const parsedRecords = parseTextData(rawText);
  if (parsedRecords.length === 0) {
    return jsonResponse(400, { message: '未能從輸入文字中解析出任何有效名片資訊。請檢查輸入格式。' });
  }

  try {
    await writeToAirtable(parsedRecords);
  } catch (error) {
    console.error('寫入失敗:', error);
    return jsonResponse(500, { message: `伺服器內部錯誤 (請檢查 Netlify Logs): ${error.message}` });
  }

  return jsonResponse(200, { message: `成功寫入 ${parsedRecords.length} 筆資料到 Airtable` });
};

function parseTextData(rawText) {
  const parts = rawText.split(CARD_SPLIT_REGEX).slice(1);
  const blocks = [];
  for (let i = 0; i < parts.length; i += 2) {
    const label = parts[i] || '';
    const body = parts[i + 1] || '';
    blocks.push(`${label}${body}`);
  }

  const records = [];
  for (const block of blocks) {
    const cardInfo = {};
    for (const field of FIELDS) {
      const value = extractField(block, field);
      if (value) {
        cardInfo[field] = value;
      }
    }

    const names = splitAndFilter(cardInfo['姓名'], /\//);
    if (names.length === 0) {
      continue;
    }

    const titles = splitAndFilter(cardInfo['職稱'], /\s*\/\s*|\n/);
    const mobiles = splitAndFilter(cardInfo['手機'], / \(|\) | \/ /).filter((entry) => /\d/.test(entry));
    const emails = splitAndFilter(cardInfo['Email'], / \(|\) | \/ /).filter((entry) => entry.includes('@'));

    for (let index = 0; index < names.length; index += 1) {
      const record = {};
      for (const sharedField of ['公司名稱', '地址', '統一編號', '公司電話', '傳真']) {
        record[sharedField] = cardInfo[sharedField] || '';
      }

      record['姓名'] = names[index];
      record['職稱'] = titles[index] || titles[0] || '';
      record['手機'] = mobiles[index] || '';
      record['Email'] = emails[index] || '';

      records.push(record);
    }
  }

  return records;
}

function extractField(block, field) {
  const regex = new RegExp(`${escapeForRegex(field)}(.+?)${FIELD_LOOKAHEAD}`, 's');
  const match = block.match(regex);
  if (!match) {
    return '';
  }

  let value = match[1].split('項目內容').pop().trim().replace(/\n/g, ' ');
  for (const target of FIELDS) {
    value = value.replace(new RegExp(escapeForRegex(target), 'g'), '').trim();
  }

  return value.replace(/\s+/g, ' ').trim();
}

function splitAndFilter(source = '', pattern) {
  if (!source) {
    return [];
  }
  return source
    .split(pattern)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

async function writeToAirtable(records) {
  const baseUrl = 'https://api.airtable.com/v0';
  const endpoint = `${baseUrl}/${AIRTABLE_BASE_ID}/${encodeURIComponent(AIRTABLE_TABLE_NAME)}`;

  for (const batch of chunkRecords(records, 10)) {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${AIRTABLE_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        records: batch.map((fields) => ({ fields }))
      })
    });

    if (!response.ok) {
      let detail = '';
      try {
        const payload = await response.json();
        detail = payload?.error?.message || JSON.stringify(payload);
      } catch (error) {
        detail = await response.text();
      }
      throw new Error(`Airtable API ${response.status}: ${detail}`);
    }
  }
}

function chunkRecords(records, size) {
  const chunks = [];
  for (let index = 0; index < records.length; index += size) {
    chunks.push(records.slice(index, index + size));
  }
  return chunks;
}

function escapeForRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function jsonResponse(statusCode, body) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  };
}
