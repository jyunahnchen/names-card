const {
  requireAuthentication,
  jsonUnauthorized
} = require('./_utils_auth');

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

exports.handler = async function handler(event) {
  const session = requireAuthentication(event);
  if (!session) {
    return jsonUnauthorized();
  }

  if (!AIRTABLE_API_KEY || !AIRTABLE_BASE_ID) {
    return jsonResponse(500, { message: '後端憑證未設定 (AIRTABLE_API_KEY 或 BASE_ID 遺失)。請檢查 Netlify 環境變數。' });
  }

  const rawQuery = (event.queryStringParameters && event.queryStringParameters.query) || '';
  const query = rawQuery.trim();

  if (!query) {
    return jsonResponse(400, { message: '請輸入搜尋關鍵字。' });
  }

  try {
    const records = await fetchRecords(query);
    return jsonResponse(200, { records });
  } catch (error) {
    console.error('搜尋失敗:', error);
    return jsonResponse(500, { message: `伺服器內部錯誤 (請檢查 Netlify Logs): ${error.message}` });
  }
};

async function fetchRecords(term) {
  const allRecords = [];
  let offset;

  do {
    const url = buildRequestUrl(term, offset);
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${AIRTABLE_API_KEY}`,
        'Content-Type': 'application/json'
      }
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

    const data = await response.json();
    const pageRecords = (data.records || []).map((record) => mapRecord(record));
    allRecords.push(...pageRecords);
    offset = data.offset;
  } while (offset);

  return allRecords;
}

function buildRequestUrl(term, offset) {
  const baseUrl = new URL(`https://api.airtable.com/v0/${AIRTABLE_BASE_ID}/${encodeURIComponent(AIRTABLE_TABLE_NAME)}`);
  const params = baseUrl.searchParams;
  params.set('pageSize', '100');
  params.set('filterByFormula', buildFilterFormula(term));
  if (offset) {
    params.set('offset', offset);
  }
  return baseUrl.toString();
}

function buildFilterFormula(term) {
  const safeTerm = term.replace(/"/g, '\\"');
  return `OR(SEARCH("${safeTerm}",{公司名稱})>0,SEARCH("${safeTerm}",{姓名})>0)`;
}

function mapRecord(record) {
  const fields = record.fields || {};
  const normalized = {};
  for (const field of FIELDS) {
    const value = fields[field];
    normalized[field] = typeof value === 'string' ? value : value || '';
  }
  return {
    id: record.id,
    fields: normalized
  };
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
