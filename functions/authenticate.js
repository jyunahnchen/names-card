const {
  createSessionToken,
  validateCredentials,
  ensureAuthConfig
} = require('./_utils_auth');

exports.handler = async function handler(event) {
  try {
    ensureAuthConfig();
  } catch (error) {
    return jsonResponse(500, { message: error.message });
  }

  if (event.httpMethod && event.httpMethod !== 'POST') {
    return {
      statusCode: 405,
      headers: {
        'Content-Type': 'application/json',
        Allow: 'POST'
      },
      body: JSON.stringify({ message: 'Method Not Allowed' })
    };
  }

  let payload;
  try {
    payload = event.body ? JSON.parse(event.body) : {};
  } catch (error) {
    return jsonResponse(400, { message: '請提供正確的 JSON 格式。' });
  }

  const username = String(payload.username || '').trim();
  const password = String(payload.password || '');

  if (!username || !password) {
    return jsonResponse(400, { message: '請輸入帳號與密碼。' });
  }

  const valid = validateCredentials(username, password);
  if (!valid) {
    return jsonResponse(401, { message: '帳號或密碼錯誤。' });
  }

  const token = createSessionToken(username);
  return jsonResponse(200, { token });
};

function jsonResponse(statusCode, body) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  };
}
