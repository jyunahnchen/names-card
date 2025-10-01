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

const SHARED_FIELDS = ['公司名稱', '地址', '統一編號', '公司電話', '傳真'];

const CARD_FIELD_ALIASES = {
  公司名稱: ['公司名稱', 'companyName', 'company', 'organization'],
  地址: ['地址', 'address'],
  統一編號: ['統一編號', 'taxId', '統編', 'uniformNumber'],
  公司電話: ['公司電話', 'companyPhone', 'telephone', 'phone'],
  傳真: ['傳真', 'fax']
};

const CONTACT_FIELD_ALIASES = {
  姓名: ['姓名', 'name'],
  職稱: ['職稱', 'title', 'position'],
  手機: ['手機', 'mobile', 'cell', 'phone'],
  Email: ['Email', 'email', 'eMail']
};

const CONTACT_COLLECTION_KEYS = ['聯絡人', 'contacts', 'people', '人員'];

const CARD_SPLIT_REGEX = /(名片[一二三四五六七八九十]+：)/;
const FIELD_LOOKAHEAD = '(?=職稱|姓名|手機|Email|名片[一二三四五六七八九十]+：|$)';

exports.handler = async function handler(event) {
  const session = requireAuthentication(event);
  if (!session) {
    return jsonUnauthorized();
  }

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

  const cardsPayload = Array.isArray(payload.cards) ? payload.cards : [];
  const rawText = typeof payload.text === 'string' ? payload.text.trim() : '';

  let parsedRecords = [];
  if (cardsPayload.length > 0) {
    parsedRecords = buildRecordsFromCards(cardsPayload);
  } else if (rawText) {
    parsedRecords = parseTextData(rawText);
  } else {
    return jsonResponse(400, { message: '未提供名片資料（cards 或 text）。' });
  }

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
        cardInfo[field] = normalizeField(field, value);
      }
    }

    const names = splitAndFilter(cardInfo['姓名'], /\//)
      .map((name) => normalizeField('姓名', name))
      .filter(Boolean);
    if (names.length === 0) {
      continue;
    }

    const titles = splitAndFilter(cardInfo['職稱'], /\s*\/\s*|\n/)
      .map((title) => normalizeField('職稱', title))
      .filter(Boolean);
    const mobiles = splitAndFilter(cardInfo['手機'], / \(|\) | \/ /)
      .map((mobile) => normalizeField('手機', mobile))
      .filter(Boolean);
    const emails = splitAndFilter(cardInfo['Email'], / \(|\) | \/ /)
      .map((email) => normalizeField('Email', email))
      .filter(Boolean);

    for (let index = 0; index < names.length; index += 1) {
      const record = {};
      for (const sharedField of ['公司名稱', '地址', '統一編號', '公司電話', '傳真']) {
        record[sharedField] = normalizeField(sharedField, cardInfo[sharedField] || '');
      }

      record['姓名'] = names[index];
      record['職稱'] = titles[index] || titles[0] || '';
      record['手機'] = mobiles[index] || '';
      record['Email'] = emails[index] || '';

      records.push(cleanRecord(record));
    }
  }

  return records;
}

function buildRecordsFromCards(cards) {
  const records = [];

  for (const card of cards) {
    if (!card || typeof card !== 'object') {
      continue;
    }

    const shared = {};
    for (const field of SHARED_FIELDS) {
      shared[field] = normalizeField(field, readField(card, field, CARD_FIELD_ALIASES));
    }

    const contacts = extractContacts(card);
    if (contacts.length === 0) {
      continue;
    }

    for (const contact of contacts) {
      if (!contact || typeof contact !== 'object') {
        continue;
      }

      const record = { ...shared };

      const name = normalizeField(
        '姓名',
        readField(contact, '姓名', CONTACT_FIELD_ALIASES) || readField(card, '姓名', CONTACT_FIELD_ALIASES)
      );
      if (!name) {
        continue;
      }

      record['姓名'] = name;
      record['職稱'] = normalizeField('職稱', readField(contact, '職稱', CONTACT_FIELD_ALIASES));
      record['手機'] = normalizeField('手機', readField(contact, '手機', CONTACT_FIELD_ALIASES));
      record['Email'] = normalizeField('Email', readField(contact, 'Email', CONTACT_FIELD_ALIASES));

      records.push(cleanRecord(record));
    }
  }

  return records;
}

function extractContacts(card) {
  for (const key of CONTACT_COLLECTION_KEYS) {
    if (Array.isArray(card[key])) {
      return card[key];
    }
  }

  const fallbackName = readField(card, '姓名', CONTACT_FIELD_ALIASES);
  if (!fallbackName) {
    return [];
  }

  return [
    {
      姓名: fallbackName,
      職稱: readField(card, '職稱', CONTACT_FIELD_ALIASES),
      手機: readField(card, '手機', CONTACT_FIELD_ALIASES),
      Email: readField(card, 'Email', CONTACT_FIELD_ALIASES)
    }
  ];
}

function readField(source, field, aliasMap) {
  if (!source || typeof source !== 'object') {
    return '';
  }

  const aliases = aliasMap[field] || [field];
  for (const key of aliases) {
    if (Object.prototype.hasOwnProperty.call(source, key) && source[key] !== undefined && source[key] !== null) {
      return source[key];
    }
  }

  return '';
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

  return sanitizeMarkdown(value.replace(/\s+/g, ' ').trim());
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

function cleanRecord(record) {
  const cleaned = {};
  for (const [key, value] of Object.entries(record)) {
    cleaned[key] = normalizeField(key, value);
  }
  return cleaned;
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

function sanitizeMarkdown(value) {
  if (!value || typeof value !== 'string') {
    return value || '';
  }

  let cleaned = value;
  cleaned = cleaned.replace(/`([^`]+)`/g, '$1');
  cleaned = cleaned.replace(/\*\*([^*]+)\*\*/g, '$1');
  cleaned = cleaned.replace(/__([^_]+)__/g, '$1');
  cleaned = cleaned.replace(/\*([^*]+)\*/g, '$1');
  cleaned = cleaned.replace(/_([^_]+)_/g, '$1');
  cleaned = cleaned.replace(/~~([^~]+)~~/g, '$1');
  cleaned = cleaned.replace(/\[(.*?)\]\((.*?)\)/g, '$1 $2');
  cleaned = cleaned.replace(/mailto:\s*/gi, '');
  cleaned = cleaned.replace(/^\s*#+\s*/gm, '');
  cleaned = cleaned.replace(/^\s*([-*+]\s+|\d+\.\s+)/gm, '');
  cleaned = cleaned.replace(/<[^>]+>/g, '');
  cleaned = cleaned.replace(/\|/g, ' ');
  cleaned = cleaned.replace(/\s+/g, ' ');

  return cleaned.trim();
}

function normalizeField(field, value) {
  const sanitized = sanitizeMarkdown(value);
  if (!sanitized) {
    return '';
  }

  for (const marker of FIELDS) {
    if (marker !== field && sanitized.includes(marker)) {
      return '';
    }
  }

  if (field === 'Email') {
    const match = sanitized.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
    return match ? match[0] : '';
  }

  if (field === '手機' || field === '公司電話') {
    const candidates = sanitized
      .split(/[、／/,，;；]+/)
      .map((part) => part.replace(/[^0-9+]/g, ''))
      .filter(Boolean);

    if (candidates.length === 0) {
      const single = sanitized.replace(/[^0-9+]/g, '');
      return single || '';
    }

    const unique = Array.from(new Set(candidates));
    return unique.join(' / ');
  }

  const trimmed = sanitized.trim().replace(/^[\-_.;,:/\\\s]+|[\-_.;,:/\\\s]+$/g, '');
  if (!trimmed) {
    return '';
  }

  if (!/[A-Za-z0-9\u4e00-\u9fff]/.test(trimmed)) {
    return '';
  }

  return trimmed;
}
