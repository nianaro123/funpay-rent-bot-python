const SteamUser = require('steam-user');
const SteamTotp = require('steam-totp');
const axios = require('axios');
const FormData = require('form-data');

const LOGIN = process.argv[2];
const PASSWORD = process.argv[3];
const SHARED_SECRET = process.argv[4] || '';

const RETRY_DELAY_MS = 5 * 60 * 1000; // 5 минут
const MAX_WEB_RETRIES = 1; // один повтор после 439

if (!LOGIN || !PASSWORD) {
  console.error('Usage: node steam_kick_all_sessions_worker.js <login> <password> [shared_secret]');
  process.exit(2);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function generateTwoFactorCode() {
  if (!SHARED_SECRET) return undefined;
  return SteamTotp.generateAuthCode(SHARED_SECRET);
}

function cookieArrayToHeader(cookies) {
  return cookies.map(cookie => cookie.split(';')[0].trim()).join('; ');
}

function extractCookieValueFromArray(cookies, name) {
  for (const raw of cookies) {
    const firstPart = raw.split(';')[0].trim();
    const prefix = `${name}=`;
    if (firstPart.startsWith(prefix)) {
      return firstPart.slice(prefix.length);
    }
  }
  return null;
}

function waitForLoggedOn(client, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error('steam-user login timeout'));
    }, timeoutMs);

    function cleanup() {
      clearTimeout(timer);
      client.removeListener('loggedOn', onLoggedOn);
      client.removeListener('steamGuard', onSteamGuard);
      client.removeListener('disconnected', onDisconnectedBeforeLogin);
      client.removeListener('error', onErrorBeforeLogin);
    }

    function onLoggedOn() {
      cleanup();
      resolve();
    }

    function onSteamGuard(domain, callback, lastCodeWrong) {
      if (!SHARED_SECRET) {
        cleanup();
        reject(new Error('Steam Guard required, but shared_secret not provided'));
        return;
      }

      if (lastCodeWrong) {
        cleanup();
        reject(new Error('Steam Guard code was rejected by steam-user'));
        return;
      }

      try {
        callback(generateTwoFactorCode());
      } catch (err) {
        cleanup();
        reject(err);
      }
    }

    function onDisconnectedBeforeLogin(eresult, msg) {
      cleanup();
      reject(new Error(`steam-user disconnected before loggedOn: ${eresult} ${msg || ''}`));
    }

    function onErrorBeforeLogin(err) {
      cleanup();
      reject(err);
    }

    client.once('loggedOn', onLoggedOn);
    client.on('steamGuard', onSteamGuard);
    client.once('disconnected', onDisconnectedBeforeLogin);
    client.once('error', onErrorBeforeLogin);
  });
}

function waitForWebSession(client, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error('webLogOn timeout'));
    }, timeoutMs);

    function cleanup() {
      clearTimeout(timer);
      client.removeListener('webSession', onWebSession);
      client.removeListener('disconnected', onDisconnectedBeforeWebSession);
      client.removeListener('error', onErrorBeforeWebSession);
    }

    function onWebSession(sessionID, cookies) {
      cleanup();
      resolve({ sessionID, cookies });
    }

    function onDisconnectedBeforeWebSession(eresult, msg) {
      cleanup();
      reject(new Error(`steam-user disconnected before webSession: ${eresult} ${msg || ''}`));
    }

    function onErrorBeforeWebSession(err) {
      cleanup();
      reject(err);
    }

    client.once('webSession', onWebSession);
    client.once('disconnected', onDisconnectedBeforeWebSession);
    client.once('error', onErrorBeforeWebSession);
  });
}

async function loginSteamUser(client) {
  console.log('🔐 Логинимся через steam-user...');

  client.logOn({
    accountName: LOGIN,
    password: PASSWORD,
    twoFactorCode: generateTwoFactorCode(),
  });

  await waitForLoggedOn(client, 30000);
  console.log('✅ steam-user loggedOn');
}

async function kickPlayingSessionStep(client) {
  const result = {
    attempted: true,
    kicked: false,
    playingApp: null,
    error: null,
  };

  try {
    console.log('🎮 Вызываем kickPlayingSession()...');
    const response = await client.kickPlayingSession();

    result.playingApp = response?.playingApp ?? null;
    result.kicked = result.playingApp !== null && result.playingApp !== 0;

    console.log(`🎮 kickPlayingSession completed, playingApp=${result.playingApp}`);
  } catch (err) {
    result.error = err?.message || String(err);
    console.log(`⚠️ kickPlayingSession error: ${result.error}`);
  }

  return result;
}

async function getWebSessionFromSteamUser(client) {
  console.log('🌐 Запрашиваю webSession через webLogOn()...');

  const webSessionPromise = waitForWebSession(client, 30000);
  client.webLogOn();

  const { sessionID, cookies } = await webSessionPromise;

  const cookieHeader = cookieArrayToHeader(cookies);
  const sessionid = extractCookieValueFromArray(cookies, 'sessionid') || sessionID;
  const steamLoginSecure = extractCookieValueFromArray(cookies, 'steamLoginSecure');

  if (!sessionid) {
    throw new Error('sessionid not found in web cookies');
  }

  if (!steamLoginSecure) {
    throw new Error('steamLoginSecure not found in web cookies');
  }

  console.log('🍪 webSession получена');
  console.log('steamLoginSecure=true');
  console.log('sessionid=true');

  return {
    cookieHeader,
    sessionid,
    rawCookies: cookies,
  };
}

async function verifyAuthorizedDevices(cookieHeader) {
  const response = await axios.get(
    'https://store.steampowered.com/account/authorizeddevices',
    {
      headers: {
        Cookie: cookieHeader,
        'User-Agent': 'Mozilla/5.0',
      },
      maxRedirects: 0,
      validateStatus: () => true,
    }
  );

  console.log(`📄 authorizeddevices status=${response.status}`);
  console.log(`📍 authorizeddevices location=${response.headers.location || '(none)'}`);

  return response.status === 200;
}

async function sendDeauthorize(cookieHeader, sessionid) {
  const form = new FormData();
  form.append('action', 'deauthorize');
  form.append('sessionid', sessionid);

  const response = await axios.post(
    'https://store.steampowered.com/twofactor/manage_action',
    form,
    {
      headers: {
        ...form.getHeaders(),
        Cookie: cookieHeader,
        Origin: 'https://store.steampowered.com',
        Referer: 'https://store.steampowered.com/account/authorizeddevices',
        'User-Agent': 'Mozilla/5.0',
        Accept: 'application/json, text/plain, */*',
      },
      maxRedirects: 0,
      validateStatus: () => true,
    }
  );

  console.log(`🧨 deauthorize status=${response.status}`);
  console.log(`📍 deauthorize location=${response.headers.location || '(none)'}`);

  return response;
}

async function sendLogout(cookieHeader, sessionid) {
  const body = new URLSearchParams({ sessionid }).toString();

  const response = await axios.post(
    'https://store.steampowered.com/logout',
    body,
    {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Cookie: cookieHeader,
        Origin: 'https://store.steampowered.com',
        Referer: 'https://store.steampowered.com/account/authorizeddevices',
        'User-Agent': 'Mozilla/5.0',
        Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      },
      maxRedirects: 0,
      validateStatus: () => true,
    }
  );

  console.log(`🚪 logout status=${response.status}`);
  console.log(`📍 logout location=${response.headers.location || '(none)'}`);

  return response;
}

async function runWebSignoutCycle(client, attemptNumber, state) {
  console.log(`🌐 Web signout attempt ${attemptNumber}...`);

  const webSession = await getWebSessionFromSteamUser(client);

  const authorizedOk = await verifyAuthorizedDevices(webSession.cookieHeader);
  if (!authorizedOk) {
    throw new Error('authorizeddevices page is not reachable with current web session');
  }

  state.deauthStarted = true;

  const deauthResponse = await sendDeauthorize(webSession.cookieHeader, webSession.sessionid);

  if (deauthResponse.status === 439) {
    return {
      retryable439: true,
      ok: false,
      deauthorize_status: deauthResponse.status,
      deauthorize_location: deauthResponse.headers.location || null,
      logout_status: null,
      logout_location: null,
    };
  }

  state.expectedDisconnectAfterDeauth = true;

  const logoutResponse = await sendLogout(webSession.cookieHeader, webSession.sessionid);

  if (logoutResponse.status === 439) {
    return {
      retryable439: true,
      ok: false,
      deauthorize_status: deauthResponse.status,
      deauthorize_location: deauthResponse.headers.location || null,
      logout_status: logoutResponse.status,
      logout_location: logoutResponse.headers.location || null,
    };
  }

  return {
    retryable439: false,
    ok:
      (deauthResponse.status === 302 || deauthResponse.status === 200) &&
      (logoutResponse.status === 302 || logoutResponse.status === 200),
    deauthorize_status: deauthResponse.status,
    deauthorize_location: deauthResponse.headers.location || null,
    logout_status: logoutResponse.status,
    logout_location: logoutResponse.headers.location || null,
  };
}

async function runWebSignoutWithRetry(client, state) {
  let attempt = 0;
  let lastResult = null;

  while (attempt <= MAX_WEB_RETRIES) {
    attempt += 1;
    lastResult = await runWebSignoutCycle(client, attempt, state);

    if (!lastResult.retryable439) {
      return {
        ...lastResult,
        attempts_used: attempt,
        retried_after_439: attempt > 1,
      };
    }

    if (attempt <= MAX_WEB_RETRIES) {
      console.log(`⏳ Получен 439. Жду ${RETRY_DELAY_MS / 1000} секунд перед повтором...`);
      await sleep(RETRY_DELAY_MS);
    }
  }

  return {
    ...lastResult,
    ok: false,
    attempts_used: attempt,
    retried_after_439: true,
  };
}

(async () => {
  const result = {
    ok: false,
    login: LOGIN,
    kick_playing_session: null,
    web_signout: null,
    error: null,
  };

  const state = {
    deauthStarted: false,
    expectedDisconnectAfterDeauth: false,
    ignoredSteamUserErrors: [],
    disconnectedAfterDeauth: false,
  };

  const client = new SteamUser({
    autoRelogin: false,
  });

  client.on('error', (err) => {
    const msg = err?.message || String(err);
    const eresult = err?.eresult ?? null;

    const expected =
      state.expectedDisconnectAfterDeauth &&
      (eresult === 5 || msg === 'InvalidPassword');

    if (expected) {
      console.log(`ℹ️ Ignoring expected steam-user error after deauth/logout: ${msg} (eresult=${eresult})`);
      state.ignoredSteamUserErrors.push({ message: msg, eresult });
      return;
    }

    console.log(`⚠️ steam-user unexpected error event: ${msg} (eresult=${eresult})`);
  });

  client.on('disconnected', (eresult, msg) => {
    if (state.expectedDisconnectAfterDeauth) {
      state.disconnectedAfterDeauth = true;
      console.log(`ℹ️ steam-user disconnected after deauth/logout: ${eresult} ${msg || ''}`);
      return;
    }

    console.log(`🔌 steam-user disconnected: ${eresult} ${msg || ''}`);
  });

  try {
    await loginSteamUser(client);

    result.kick_playing_session = await kickPlayingSessionStep(client);

    await sleep(3000);

    result.web_signout = await runWebSignoutWithRetry(client, state);
    result.web_signout.ignored_steam_user_errors = state.ignoredSteamUserErrors;
    result.web_signout.disconnected_after_deauth = state.disconnectedAfterDeauth;

    result.ok = !!result.web_signout.ok;

    // даём немного времени на поздние события steam-user
    await sleep(1500);

    console.log('RESULT_JSON=' + JSON.stringify(result));
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    result.error = err?.message || String(err);
    console.error('❌ Fatal error:', result.error);

    if (result.web_signout) {
      result.web_signout.ignored_steam_user_errors = state.ignoredSteamUserErrors;
      result.web_signout.disconnected_after_deauth = state.disconnectedAfterDeauth;
    }

    console.log('RESULT_JSON=' + JSON.stringify(result));
    process.exit(1);
  } finally {
    try {
      client.logOff();
    } catch {}
  }
})();