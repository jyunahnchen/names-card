const crypto = require('crypto');

const ADMIN_USERNAME = process.env.ADMIN_USERNAME;
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD;
const SESSION_SECRET = process.env.ADMIN_SESSION_SECRET;
const TOKEN_TTL_MS = Number(process.env.ADMIN_SESSION_TTL_MS || 12 * 60 * 60 * 1000); // 預設 12 小時

function ensureAuthConfig() {
  if (!ADMIN_USERNAME || !ADMIN_PASSWORD || !SESSION_SECRET) {
    throw new Error('後端登入環境變數未設定完整 (ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_SESSION_SECRET)');
  }
}

function createSessionToken(username) {
  ensureAuthConfig();
  const expiresAt = Date.now() + TOKEN_TTL_MS;
  const payload = base64UrlEncode(Buffer.from(JSON.stringify({ u: username, exp: expiresAt })));
  const signature = signPayload(payload);
  return `${payload}.${signature}`;
}

function verifySessionToken(token) {
  if (!token || typeof token !== 'string') {
    return null;
  }
  if (!SESSION_SECRET) {
    throw new Error('ADMIN_SESSION_SECRET 未設定');
  }
  const parts = token.split('.');
  if (parts.length !== 2) {
    return null;
  }
  const [payloadPart, signaturePart] = parts;
  const expectedSignature = signPayload(payloadPart);
  if (!timingSafeCompare(signaturePart, expectedSignature)) {
    return null;
  }

  try {
    const decoded = JSON.parse(Buffer.from(base64UrlDecode(payloadPart)));
    if (!decoded || typeof decoded !== 'object') {
      return null;
    }
    if (typeof decoded.exp !== 'number' || Date.now() > decoded.exp) {
      return null;
    }
    return decoded;
  } catch (error) {
    return null;
  }
}

function validateCredentials(username, password) {
  ensureAuthConfig();
  return timingSafeCompare(username, ADMIN_USERNAME) && timingSafeCompare(password, ADMIN_PASSWORD);
}

function getTokenFromEvent(event) {
  const headers = (event && event.headers) || {};
  const authHeader = headers.authorization || headers.Authorization || '';
  if (typeof authHeader !== 'string' || !authHeader.toLowerCase().startsWith('bearer ')) {
    return null;
  }
  return authHeader.slice(7).trim();
}

function requireAuthentication(event) {
  const token = getTokenFromEvent(event);
  const payload = verifySessionToken(token);
  if (!payload) {
    return null;
  }
  return payload;
}

function jsonUnauthorized() {
  return {
    statusCode: 401,
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ message: '未授權的存取，請重新登入。' })
  };
}

function timingSafeCompare(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') {
    return false;
  }
  try {
    return crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
  } catch (error) {
    return false;
  }
}

function signPayload(payload) {
  return base64UrlEncode(crypto.createHmac('sha256', SESSION_SECRET).update(payload).digest());
}

function base64UrlEncode(buffer) {
  return Buffer.from(buffer)
    .toString('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}

function base64UrlDecode(str) {
  const padded = str.replace(/-/g, '+').replace(/_/g, '/');
  const padLength = (4 - (padded.length % 4)) % 4;
  return Buffer.from(padded + '='.repeat(padLength), 'base64');
}

module.exports = {
  createSessionToken,
  verifySessionToken,
  validateCredentials,
  requireAuthentication,
  jsonUnauthorized,
  ensureAuthConfig
};
