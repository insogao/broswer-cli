// Manual evidence fixture for HUMAN-owned window ops (test only).
//
// The user opens this page as a tab in the window they took over, presses
// "Detect HUMAN window", then each op button. Every op:
//   * snapshots the target window state via chrome.windows.get,
//   * performs the real Extension API call,
//   * snapshots again and appends {op, windowId, ok, error, before, after}
//     to chrome.storage.local under "bcli_human_ops_results",
//   * POSTs the full result set + a chrome.windows.getAll inventory to the
//     local sink (http://127.0.0.1:8737/ops-result) so the collector can read
//     machine evidence without any CDP/WebSocket session.
//
// The page never performs ops on its own; every call is a manual button press.

const SINK_URL = 'http://127.0.0.1:8737/ops-result';
const RESULT_KEY = 'bcli_human_ops_results';
let targetWindowId = null;

function snapshotWindow(id) {
  return chrome.windows.get(id).then((win) => ({
    id: win.id,
    state: win.state,
    focused: win.focused,
    left: win.left,
    top: win.top,
    width: win.width,
    height: win.height,
  }));
}

async function inventory() {
  const windows = await chrome.windows.getAll({ populate: false });
  return windows.map((win) => ({
    id: win.id,
    state: win.state,
    focused: win.focused,
    type: win.type,
  }));
}

async function record(op, action) {
  const entry = { op, windowId: targetWindowId, ok: false, error: null, at: Date.now() };
  try {
    if (targetWindowId === null) {
      throw new Error('press "Detect HUMAN window" first');
    }
    entry.before = await snapshotWindow(targetWindowId);
    await action(targetWindowId);
    entry.after = await snapshotWindow(targetWindowId);
    entry.ok = true;
  } catch (error) {
    entry.error = String((error && error.message) || error);
  }
  const stored = (await chrome.storage.local.get(RESULT_KEY))[RESULT_KEY] || [];
  stored.push(entry);
  await chrome.storage.local.set({ [RESULT_KEY]: stored });
  await sendResults();
  render(entry);
}

async function sendResults() {
  const results = (await chrome.storage.local.get(RESULT_KEY))[RESULT_KEY] || [];
  const payload = {
    results,
    inventory: await inventory(),
    focusedWindowId: targetWindowId,
    sentAt: Date.now(),
  };
  try {
    const response = await fetch(SINK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error('sink HTTP ' + response.status);
    }
  } catch (error) {
    const out = document.getElementById('out');
    out.textContent += '\n[sink error] ' + String((error && error.message) || error);
  }
}

function render(entry) {
  const out = document.getElementById('out');
  const line =
    entry.op +
    ' window=' + entry.windowId +
    ' ok=' + entry.ok +
    (entry.error ? ' error=' + entry.error : '') +
    (entry.after ? ' state=' + entry.after.state + ' focused=' + entry.after.focused : '');
  out.textContent = line + '\n' + out.textContent;
  out.className = entry.ok ? 'ok' : 'err';
}

function bind(id, op, action) {
  document.getElementById(id).addEventListener('click', () => record(op, action));
}

document.getElementById('detect').addEventListener('click', async () => {
  const win = await chrome.windows.getLastFocused({ populate: false });
  targetWindowId = win.id;
  document.getElementById('out').textContent =
    'HUMAN window id = ' + win.id + ' state=' + win.state + ' focused=' + win.focused;
  await sendResults();
});

bind('activate', 'update.focused.true', (id) => chrome.windows.update(id, { focused: true }));
bind('deactivate', 'update.focused.false', (id) => chrome.windows.update(id, { focused: false }));
bind('minimize', 'update.state.minimized', (id) => chrome.windows.update(id, { state: 'minimized' }));
bind('maximize', 'update.state.maximized', (id) => chrome.windows.update(id, { state: 'maximized' }));
bind('restore', 'update.state.normal', (id) => chrome.windows.update(id, { state: 'normal' }));
bind('fullscreen-on', 'update.state.fullscreen', (id) =>
  chrome.windows.update(id, { state: 'fullscreen' }));
bind('fullscreen-off', 'update.state.normal.after-fullscreen', (id) =>
  chrome.windows.update(id, { state: 'normal' }));
bind('bounds', 'update.bounds.move-resize', async (id) => {
  const before = await snapshotWindow(id);
  await chrome.windows.update(id, {
    left: before.left + 20,
    top: before.top + 20,
    width: Math.max(400, before.width - 40),
    height: Math.max(300, before.height - 40),
  });
});

document.getElementById('create-window').addEventListener('click', async () => {
  const created = await chrome.windows.create({ url: 'about:blank', focused: false });
  render({ op: 'create.background-window', windowId: created.id, ok: true, after: { state: created.state, focused: created.focused } });
  await sendResults();
});

document.getElementById('create-tab').addEventListener('click', async () => {
  const tab = await chrome.tabs.create({ active: false });
  render({ op: 'create.background-tab', windowId: tab.windowId, ok: true });
  await sendResults();
});

document.getElementById('send').addEventListener('click', sendResults);
