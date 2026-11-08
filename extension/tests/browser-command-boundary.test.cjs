'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test').test;
const vm = require('node:vm');

const BACKGROUND_PATH = path.join(__dirname, '..', 'background.js');
const BACKGROUND_SOURCE = fs.readFileSync(BACKGROUND_PATH, 'utf8');

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.sends = [];
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
  }
  send(data) {
    this.sends.push(data);
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
    if (typeof this.onclose === 'function') this.onclose();
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;

function createChrome() {
  return {
    storage: {
      local: {
        get: async () => ({ sythionPort: 8000, sythionToken: 'test-token' }),
      },
    },
    action: {
      setBadgeText() {},
      setBadgeBackgroundColor() {},
    },
    alarms: {
      create() {},
      onAlarm: { addListener() {}, removeListener() {} },
    },
    tabs: {
      query: async () => [{ id: 7, windowId: 2, title: 'Active', url: 'https://active.example', active: true }],
      get: async (id) => ({ id, windowId: 2, title: `Tab ${id}` }),
      update: async (id, update) => ({ id, ...update }),
      remove: async () => undefined,
      create: async (options) => ({ id: 99, url: options.url, active: options.active }),
      onActivated: { addListener() {}, removeListener() {} },
      onUpdated: { addListener() {}, removeListener() {} },
    },
    windows: {
      update: async (id, update) => ({ id, ...update }),
    },
    scripting: {
      executeScript: async () => [{ result: { title: 'Read Page', url: 'https://read.example', headings: [], text_snippet: 'text', interactive_elements: [] } }],
    },
  };
}

function createConsole() {
  const calls = { log: [], warn: [], error: [] };
  return {
    calls,
    proxy: {
      log: (...args) => calls.log.push(args),
      warn: (...args) => calls.warn.push(args),
      error: (...args) => calls.error.push(args),
    },
  };
}

async function flush() {
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => queueMicrotask(resolve));
  }
}

async function loadSession() {
  const chrome = createChrome();
  const consoleMock = createConsole();
  const timerCalls = [];
  const context = vm.createContext({});
  Object.assign(context, {
    chrome,
    console: consoleMock.proxy,
    navigator: { userAgent: 'TestAgent/1.0', platform: 'TestPlatform' },
    WebSocket: FakeWebSocket,
    setTimeout: (cb, ms) => { timerCalls.push({ cb, ms }); return timerCalls.length; },
    clearTimeout: () => {},
    setInterval: (cb, ms) => { timerCalls.push({ cb, ms, interval: true }); return 1000 + timerCalls.length; },
    clearInterval: () => {},
    JSON,
    Promise,
    Object,
    Array,
    String,
    Number,
    Boolean,
    Error,
    TypeError,
    Math,
    Date,
    RegExp,
    Map,
    Set,
    URL,
    URLSearchParams,
    encodeURIComponent,
    decodeURIComponent,
  });

  vm.runInContext(BACKGROUND_SOURCE, context);
  await flush();
  vm.runInContext('__sythionWs = ws', context);
  const ws = context.__sythionWs;

  return {
    context,
    chrome,
    consoleCalls: consoleMock.calls,
    timerCalls,
    ws,
    WebSocket: context.WebSocket,
    setWs(nextWs) {
      context.__sythionNextWs = nextWs;
      vm.runInContext('ws = __sythionNextWs', context);
    },
    getWs() {
      vm.runInContext('__sythionCurrentWs = ws', context);
      return context.__sythionCurrentWs;
    },
  };
}

function parseSend(socket) {
  return JSON.parse(socket.sends[socket.sends.length - 1]);
}

test('AC-EXT-1: valid open_tab response preserves id, defaults to about:blank and active true', async () => {
  const session = await loadSession();
  let createOptions;
  session.chrome.tabs.create = async (options) => {
    createOptions = options;
    return { id: 42, url: options.url || 'about:blank', active: options.active };
  };

  await session.context.handleCommand({ id: 'open-default', action: 'open_tab', params: {} });

  assert.equal(createOptions.url, 'about:blank');
  assert.equal(createOptions.active, true);
  const response = parseSend(session.ws);
  assert.equal(response.id, 'open-default');
  assert.equal(response.error, null);
  assert.deepEqual(response.result, { success: true, tab_id: 42, url: 'about:blank' });
});

test('AC-EXT-1: explicit active=false is preserved for open_tab', async () => {
  const session = await loadSession();
  let createOptions;
  session.chrome.tabs.create = async (options) => {
    createOptions = options;
    return { id: 77, url: options.url || 'about:blank', active: options.active };
  };

  await session.context.handleCommand({ id: 'open-inactive', action: 'open_tab', params: { url: 'https://example.com', active: false } });

  assert.equal(createOptions.url, 'https://example.com');
  assert.equal(createOptions.active, false);
  const response = parseSend(session.ws);
  assert.equal(response.id, 'open-inactive');
  assert.equal(response.error, null);
  assert.deepEqual(response.result, { success: true, tab_id: 77, url: 'https://example.com' });
});

test('AC-EXT-2: unsupported action returns correlated unsupported-action error', async () => {
  const session = await loadSession();
  await session.context.handleCommand({ id: 'unknown-1', action: 'not_an_action', params: {} });

  const response = parseSend(session.ws);
  assert.equal(response.id, 'unknown-1');
  assert.equal(response.result, null);
  assert.equal(response.error, 'Unsupported action: not_an_action');
});

test('AC-EXT-2: caught browser API rejection returns correlated error and handler continues', async () => {
  const session = await loadSession();
  session.chrome.tabs.create = async () => {
    throw new Error('tabs.create failed');
  };

  await session.context.handleCommand({ id: 'api-fail', action: 'open_tab', params: {} });
  const failedResponse = parseSend(session.ws);
  assert.equal(failedResponse.id, 'api-fail');
  assert.equal(failedResponse.result, null);
  assert.equal(failedResponse.error, 'tabs.create failed');

  session.chrome.tabs.create = async (options) => ({ id: 5, url: options.url || 'about:blank', active: options.active });
  await session.context.handleCommand({ id: 'after-fail', action: 'open_tab', params: {} });
  const successResponse = parseSend(session.ws);
  assert.equal(successResponse.id, 'after-fail');
  assert.equal(successResponse.error, null);
});
test('AC-EXT-2: close_tab follows current response contract', async () => {
  const session = await loadSession();
  let removedId;
  session.chrome.tabs.remove = async (id) => { removedId = id; };

  await session.context.handleCommand({ id: 'close-1', action: 'close_tab', params: { tab_id: 42 } });

  assert.equal(removedId, 42);
  const response = parseSend(session.ws);
  assert.equal(response.id, 'close-1');
  assert.equal(response.error, null);
  assert.deepEqual(response.result, { success: true, tab_id: 42 });
});

test('AC-EXT-2: switch_tab follows current response contract', async () => {
  const session = await loadSession();
  let updated;
  let focused;
  session.chrome.tabs.get = async (id) => ({ id, windowId: 3, title: 'Switched Tab' });
  session.chrome.tabs.update = async (id, update) => { updated = { id, update }; return { id, ...update }; };
  session.chrome.windows.update = async (id, update) => { focused = { id, update }; return { id, ...update }; };

  await session.context.handleCommand({ id: 'switch-1', action: 'switch_tab', params: { tab_id: 17 } });

  assert.equal(updated.id, 17);
  assert.equal(JSON.stringify(updated.update), JSON.stringify({ active: true }));
  assert.equal(focused.id, 3);
  assert.equal(JSON.stringify(focused.update), JSON.stringify({ focused: true }));
  const response = parseSend(session.ws);
  assert.equal(response.id, 'switch-1');
  assert.equal(response.error, null);
  assert.deepEqual(response.result, { success: true, tab_id: 17, title: 'Switched Tab' });
});

test('AC-EXT-2: read_page follows current response contract', async () => {
  const session = await loadSession();

  await session.context.handleCommand({ id: 'read-1', action: 'read_page', params: {} });

  const response = parseSend(session.ws);
  assert.equal(response.id, 'read-1');
  assert.equal(response.error, null);
  assert.deepEqual(response.result, { title: 'Read Page', url: 'https://read.example', headings: [], text_snippet: 'text', interactive_elements: [] });
});

test('AC-EXT-3: ping, pong, and handshake_ok control messages neither dispatch nor send', async () => {
  const session = await loadSession();
  const beforeSends = session.ws.sends.length;
  const beforeErrors = session.consoleCalls.error.length;

  await session.ws.onmessage({ data: JSON.stringify({ type: 'handshake_ok' }) });
  await session.ws.onmessage({ data: JSON.stringify({ type: 'ping' }) });
  await session.ws.onmessage({ data: JSON.stringify({ type: 'pong' }) });
  await flush();

  assert.equal(session.ws.sends.length, beforeSends);
  assert.equal(session.consoleCalls.error.length, beforeErrors);
});

test('AC-EXT-3: message with no action is ignored without sending or crashing', async () => {
  const session = await loadSession();
  const beforeSends = session.ws.sends.length;
  const beforeErrors = session.consoleCalls.error.length;

  await session.ws.onmessage({ data: JSON.stringify({ id: 'no-action', type: 'event' }) });
  await flush();

  assert.equal(session.ws.sends.length, beforeSends);
  assert.equal(session.consoleCalls.error.length, beforeErrors);
});

test('AC-EXT-3: invalid JSON is caught and logged without crashing', async () => {
  const session = await loadSession();
  const beforeErrors = session.consoleCalls.error.length;

  await session.ws.onmessage({ data: '{invalid json' });
  await flush();

  assert.ok(session.consoleCalls.error.length > beforeErrors);
  const lastError = session.consoleCalls.error[session.consoleCalls.error.length - 1].join(' ');
  assert.match(lastError, /Error parsing message/);
});

test('AC-EXT-4: closed WebSocket suppresses command send', async () => {
  const session = await loadSession();
  const closedSocket = new session.WebSocket('ws://closed');
  closedSocket.readyState = session.WebSocket.CLOSED;
  session.setWs(closedSocket);

  await session.context.handleCommand({ id: 'closed-1', action: 'open_tab', params: {} });
  assert.equal(closedSocket.sends.length, 0);
});

test('AC-EXT-4: null WebSocket does not crash handleCommand send path', async () => {
  const session = await loadSession();
  session.setWs(null);

  await session.context.handleCommand({ id: 'null-ws', action: 'open_tab', params: {} });
  assert.equal(session.getWs(), null);
});

test('AC-EXT-4: no reconnect timer is scheduled on the token-configured startup path', async () => {
  const session = await loadSession();
  assert.equal(session.timerCalls.filter((call) => call.ms === 2000).length, 0);
  assert.equal(vm.runInContext('reconnectTimer', session.context), null);
});
