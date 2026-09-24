#!/usr/bin/env node
// Run the existing HUMAN-window extension fixture as a visible, paced manual
// observation. Connects only to an already-running isolated Browser CLI; it
// never launches or closes the browser and never creates a native window.

import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const DEFAULT_EVIDENCE =
  '/Users/jiangao/dev/browser-cli/build-logs/phase3-human-20260923-115339';
const ACTIONS = [
  ['activate', 'request focus:true', 'update.focused.true'],
  ['deactivate', 'request focus:false', 'update.focused.false'],
  ['minimize', 'request minimize', 'update.state.minimized'],
  ['maximize', 'request maximize', 'update.state.maximized'],
  ['restore', 'request restore', 'update.state.normal'],
  ['fullscreen-on', 'request fullscreen', 'update.state.fullscreen'],
  ['fullscreen-off', 'request exit fullscreen', 'update.state.normal.after-fullscreen'],
  ['bounds', 'request move + resize', 'update.bounds.move-resize'],
  // Deliberately no create-window operation: the product requirement is one
  // browser window with new pages in tabs. A background-tab attempt is safe.
  ['create-tab', 'request a background tab (no new native window)'],
];
const RESULT_KEY = 'bcli_human_ops_results';

function argValue(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

function parseState(file) {
  const state = {};
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (!match) continue;
    const raw = match[2];
    state[match[1]] = raw.startsWith("'") && raw.endsWith("'")
      ? raw.slice(1, -1).replaceAll("'\\''", "'")
      : raw;
  }
  return state;
}

function mainProcess(state) {
  const ps = execFileSync('ps', ['-axo', 'pid=,command='], { encoding: 'utf8' });
  const rows = ps.split(/\r?\n/).map((line) => {
    const match = line.match(/^\s*(\d+)\s+(.*)$/);
    return match ? { pid: Number(match[1]), command: match[2] } : null;
  }).filter(Boolean);
  const matches = rows.filter(({ command }) =>
    command.includes(state.APP_BIN) && command.includes(state.PROFILE_DIR));
  if (matches.length !== 1) {
    throw new Error(`expected one isolated main process; found ${matches.length}`);
  }
  const [main] = matches;
  for (const required of [
    '--use-mock-keychain',
    '--browser-cli-background-presentation',
    `--remote-debugging-port=${state.DEVTOOLS_PORT}`,
  ]) {
    if (!main.command.includes(required)) {
      throw new Error(`isolated process is missing required argument ${required}`);
    }
  }
  return main;
}

function listenPids(port) {
  let out;
  try {
    out = execFileSync(
      'lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-F', 'p'],
      { encoding: 'utf8' },
    );
  } catch (error) {
    if (error.status === 1) return new Set();
    throw error;
  }
  return new Set(out.split(/\r?\n/).filter((line) => /^p\d+$/.test(line))
    .map((line) => Number(line.slice(1))));
}

function humanWindowId(logPath) {
  const lines = fs.readFileSync(logPath, 'utf8').split(/\r?\n/);
  const grant = [...lines].reverse().find((line) =>
    line.includes('transition=human-ownership-granted'));
  const allow = [...lines].reverse().find((line) =>
    line.includes('decision=allow') && line.includes('origin=user-explicit') &&
    line.includes('state=human') && line.includes('owned=yes'));
  const grantId = grant?.match(/window_id=(\d+)/)?.[1];
  const allowId = allow?.match(/window_id=(\d+)/)?.[1];
  if (!grantId || grantId !== allowId) {
    throw new Error('no matching USER_EXPLICIT HUMAN grant/allow pair in browser log');
  }
  return grantId;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

class Cdp {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
  }

  async open() {
    this.ws = new WebSocket(this.url);
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result ?? {});
    });
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', () => reject(new Error('CDP WebSocket failed')), { once: true });
    });
  }

  request(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 10000);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.close();
  }
}

async function waitFor(predicate, description, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await sleep(100);
  }
  throw new Error(`timed out waiting for ${description}`);
}

async function evalValue(cdp, expression) {
  const response = await cdp.request('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.text || 'Runtime.evaluate failed');
  }
  return response.result?.value;
}

async function pageTargets(port) {
  const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
  return targets.filter((target) => target.type === 'page');
}

function sinkPayloads(sinkPath) {
  try {
    return fs.readFileSync(sinkPath, 'utf8').split(/\r?\n/).filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch { return []; }
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    sleep(2000),
  ]);
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
}

async function nativeWindowIds(cdp) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const targets = await cdp.request('Target.getTargets');
    const ids = new Set();
    try {
      for (const target of targets.targetInfos.filter((item) => item.type === 'page')) {
        const owner = await cdp.request('Browser.getWindowForTarget', { targetId: target.targetId });
        ids.add(owner.windowId);
      }
      return ids;
    } catch (error) {
      lastError = error;
      await sleep(50);
    }
  }
  throw new Error(`could not map current page targets to native windows after retries: ${lastError?.message}`);
}

function latestSample(samplesPath) {
  try {
    const text = fs.readFileSync(samplesPath, 'utf8');
    const match = [...text.matchAll(/^sample ts=(\d+) count=(\d+)$/gm)].at(-1);
    return match ? { ts: Number(match[1]), count: Number(match[2]) } : false;
  } catch { return false; }
}

async function main() {
  const evidenceDir = path.resolve(argValue('--evidence', DEFAULT_EVIDENCE));
  const intervalMs = Number(argValue('--interval-ms', '2000'));
  if (!Number.isInteger(intervalMs) || intervalMs < 1000) {
    throw new Error('--interval-ms must be an integer >= 1000');
  }
  const focusTimeoutMs = Number(argValue('--focus-timeout-ms', '120000'));
  if (!Number.isInteger(focusTimeoutMs) || focusTimeoutMs < 1000) {
    throw new Error('--focus-timeout-ms must be an integer >= 1000');
  }
  const state = parseState(path.join(evidenceDir, 'state.env'));
  const port = Number(state.DEVTOOLS_PORT);
  const extensionUrl = `chrome-extension://${state.EXTENSION_ID}/ops.html`;
  const main = mainProcess(state);
  const appPid = listenPids(port);
  if (!appPid.has(main.pid)) {
    throw new Error(`DevTools port ${port} is not owned by isolated Browser CLI PID ${main.pid}`);
  }
  const version = await getJson(`http://127.0.0.1:${port}/json/version`);
  const policyWindowId = humanWindowId(state.BROWSER_LOG);

  const stamp = new Date().toISOString().replaceAll(':', '').replaceAll('-', '').replaceAll('.', '');
  const samplesPath = path.join(evidenceDir, `human-ops-sequence-${stamp}.samples.txt`);
  const sinkPath = path.join(evidenceDir, `human-ops-sequence-${stamp}.sink.jsonl`);
  const watcherPath = path.join(evidenceDir, 'onscreen-window-watch');
  const sinkScript = path.join(path.dirname(fileURLToPath(import.meta.url)), 'ops-result-sink.py');
  const python = process.env.PYTHON || 'python3';
  const sinkPort = Number(state.SINK_PORT || 8737);
  if (listenPids(sinkPort).size !== 0) {
    throw new Error(`local result sink port ${sinkPort} is already in use`);
  }
  let watcher;
  let sink;
  let browserCdp;
  let pageCdp;

  try {
    watcher = spawn(watcherPath, [state.APP_BIN, samplesPath, '10'], { stdio: 'ignore' });
    sink = spawn(python, [sinkScript, sinkPath, String(sinkPort)], { stdio: 'ignore' });
    await waitFor(async () => {
      try {
        return (await getJson(`http://127.0.0.1:${sinkPort}/health`)).ok;
      } catch { return false; }
    }, 'local result sink');

    const initialSample = await waitFor(() => latestSample(samplesPath), 'visible human window sample');
    if (initialSample.count < 1) {
      throw new Error('the isolated browser has no visible window; stop without opening a tab');
    }

    browserCdp = new Cdp(version.webSocketDebuggerUrl);
    await browserCdp.open();
    const beforeTargets = await browserCdp.request('Target.getTargets');
    const existingPage = beforeTargets.targetInfos.find((target) => target.type === 'page');
    if (!existingPage) throw new Error('no existing page/window to reuse');
    const initialWindowIds = await nativeWindowIds(browserCdp);
    if (initialWindowIds.size !== 1) {
      throw new Error(`expected exactly one existing native window; found ${initialWindowIds.size}`);
    }
    const beforeWindow = await browserCdp.request('Browser.getWindowForTarget', {
      targetId: existingPage.targetId,
    });

    let targetInfo = beforeTargets.targetInfos.find((target) =>
      target.type === 'page' && target.url === extensionUrl);
    if (!targetInfo) {
      const created = await browserCdp.request('Target.createTarget', {
        url: extensionUrl,
        newWindow: false,
      });
      targetInfo = await waitFor(async () =>
        (await pageTargets(port)).find((target) => target.id === created.targetId),
      'ops page as a new tab');
    }
    const opsWindow = await browserCdp.request('Browser.getWindowForTarget', {
      targetId: targetInfo.targetId,
    });
    if (opsWindow.windowId !== beforeWindow.windowId) {
      throw new Error('ops page landed in a different native window; refusing the sequence');
    }

    const currentTargets = await pageTargets(port);
    const pageMeta = currentTargets.find((target) => target.id === targetInfo.targetId);
    if (!pageMeta?.webSocketDebuggerUrl || !pageMeta.url.startsWith(extensionUrl)) {
      throw new Error('the existing HUMAN-ops extension page could not be verified');
    }
    pageCdp = new Cdp(pageMeta.webSocketDebuggerUrl);
    await pageCdp.open();
    await waitFor(async () => evalValue(pageCdp,
      `document.readyState === 'complete' && !!document.getElementById('detect')`),
    'ops page ready');

    console.log(`Browser: ${version.Browser}; PID ${main.pid}; profile ${state.PROFILE_DIR}`);
    console.log(`Human policy window=${policyWindowId}; reused native window=${opsWindow.windowId}`);
    console.log(`Visible-window baseline=${initialSample.count}; native windows=${[...initialWindowIds].join(',')}; no new native window will be created.`);
    console.log('Opening the existing test extension as a tab. After HUMAN detection, operations run 2 seconds apart.');

    await evalValue(pageCdp, "document.getElementById('detect').click(); true");
    const detectedText = await waitFor(async () => {
      const text = await evalValue(pageCdp, "document.getElementById('out').textContent");
      return text.includes('HUMAN window id =') ? text : false;
    }, 'Detect HUMAN window');
    const detectedId = detectedText.match(/HUMAN window id\s*=\s*(\d+)/)?.[1];
    console.log(`[detect] ${detectedText.split('\n')[0]}`);
    if (!detectedId) throw new Error('extension did not identify a HUMAN window; no ops run');
    if (detectedId !== String(opsWindow.windowId)) {
      throw new Error(
        `Detect targeted extension window ${detectedId} but the ops tab lives in native window ${opsWindow.windowId}; refusing the sequence`);
    }
    console.log(`    policy log window id=${policyWindowId}; extension API id=${detectedId} (reported independently)`);
    let focusState = await evalValue(pageCdp,
      'chrome.windows.getLastFocused({populate:false}).then(w => ({id:w.id, focused:w.focused}))');
    if (!focusState.focused) {
      console.log('Waiting up to 120s for you to click the Browser CLI window; the script will not activate it.');
      focusState = await waitFor(async () => {
        const current = await evalValue(pageCdp,
          'chrome.windows.getLastFocused({populate:false}).then(w => ({id:w.id, focused:w.focused}))');
        if (String(current.id) !== detectedId) {
          throw new Error(`focused window changed from HUMAN window ${detectedId} to ${current.id}`);
        }
        return current.focused ? current : false;
      }, 'user click on the same Browser CLI window', focusTimeoutMs);
    }
    console.log(`[focus] HUMAN window ${focusState.id} focused=${focusState.focused}`);
    await sleep(intervalMs);

    const baseCount = Number(await evalValue(pageCdp,
      `chrome.storage.local.get(${JSON.stringify(RESULT_KEY)}).then(v => (v[${JSON.stringify(RESULT_KEY)}] || []).length)`));
    for (let index = 0; index < ACTIONS.length; index += 1) {
      const [buttonId, description] = ACTIONS[index];
      if (index > 0) await sleep(intervalMs);
      console.log(`[${index + 1}/${ACTIONS.length}] ${description}`);

      if (buttonId === 'create-tab') {
        const pagesBefore = await pageTargets(port);
        const beforePageIds = new Set(pagesBefore.map((target) => target.id));
        const countBefore = pagesBefore.length;
        const actionStartedAt = Date.now();
        await evalValue(pageCdp, `document.getElementById(${JSON.stringify(buttonId)}).click(); true`);
        const countAfter = await waitFor(async () => {
          const count = (await pageTargets(port)).length;
          return count > countBefore ? count : false;
        }, 'background tab creation');
        const now = await pageTargets(port);
        const added = now.filter((target) => !beforePageIds.has(target.id));
        const sameWindow = [];
        for (const target of added) {
          const owner = await browserCdp.request('Browser.getWindowForTarget', { targetId: target.id });
          if (owner.windowId === opsWindow.windowId) sameWindow.push(target);
        }
        console.log(`    pages ${countBefore}→${countAfter}; added tabs in same native window=${sameWindow.length}`);
        if (countAfter !== countBefore + 1 || sameWindow.length !== 1) {
          throw new Error('create-tab did not add exactly one page in the existing native window');
        }
        const windowIds = await nativeWindowIds(browserCdp);
        if (windowIds.size !== initialWindowIds.size || [...windowIds].some((id) => !initialWindowIds.has(id))) {
          throw new Error(`native window inventory changed after create-tab: ${[...windowIds].join(',')}`);
        }
        const sample = await waitFor(() => {
          const current = latestSample(samplesPath);
          return current && current.ts >= actionStartedAt ? current : false;
        }, 'create-tab native-window sample');
        console.log(`    app-visible native windows=${sample.count}`);
        if (sample.count > initialSample.count) {
          throw new Error(`create-tab increased visible native windows from ${initialSample.count} to ${sample.count}`);
        }
        continue;
      }

      const countBefore = Number(await evalValue(pageCdp,
        `chrome.storage.local.get(${JSON.stringify(RESULT_KEY)}).then(v => (v[${JSON.stringify(RESULT_KEY)}] || []).length)`));
      const actionStartedAt = Date.now();
      await evalValue(pageCdp, `document.getElementById(${JSON.stringify(buttonId)}).click(); true`);
      const entry = await waitFor(async () => {
        const rows = await evalValue(pageCdp,
          `chrome.storage.local.get(${JSON.stringify(RESULT_KEY)}).then(v => (v[${JSON.stringify(RESULT_KEY)}] || []))`);
        return rows.length > countBefore ? rows.at(-1) : false;
      }, `${buttonId} result`);
      console.log(`    API result ok=${entry.ok}; state ${entry.before?.state ?? '?'}→${entry.after?.state ?? '?'}; focused ${entry.before?.focused ?? '?'}→${entry.after?.focused ?? '?'}`);
      const [expectedOpName] = ACTIONS[index].slice(2);
      const sinkEntry = await waitFor(() => {
        const payloads = sinkPayloads(sinkPath);
        const payload = payloads.findLast((item) =>
          item.results?.some((row) => row.op === expectedOpName && row.at === entry.at));
        return payload?.results?.findLast((row) =>
          row.op === expectedOpName && row.at === entry.at) || false;
      }, `${buttonId} result at local evidence sink`);
      if (!sinkEntry) {
        throw new Error(`${buttonId} result was not confirmed in the fresh local evidence sink`);
      }
      const sample = await waitFor(() => {
        const current = latestSample(samplesPath);
        return current && current.ts >= actionStartedAt ? current : false;
      }, `${buttonId} native-window sample`);
      console.log(`    app-visible native windows=${sample.count}`);
      if (sample.count > initialSample.count) {
        throw new Error(`${buttonId} increased visible native windows from ${initialSample.count} to ${sample.count}`);
      }
      const windowIds = await nativeWindowIds(browserCdp);
      if (windowIds.size !== initialWindowIds.size || [...windowIds].some((id) => !initialWindowIds.has(id))) {
        throw new Error(`native window inventory changed after ${buttonId}: ${[...windowIds].join(',')}`);
      }
    }

    const results = await evalValue(pageCdp,
      `chrome.storage.local.get(${JSON.stringify(RESULT_KEY)}).then(v => (v[${JSON.stringify(RESULT_KEY)}] || []).slice(${baseCount}))`);
    console.log(`Sequence finished; recorded ${results.length} existing-window Extension API operations.`);
    console.log(`Fresh window samples: ${samplesPath}`);
    console.log(`Fresh extension sink: ${sinkPath}`);
    console.log('The browser and ops tab are intentionally left open for inspection.');
  } finally {
    pageCdp?.close();
    browserCdp?.close();
    await stopChild(watcher);
    await stopChild(sink);
  }
}

main().catch((error) => {
  console.error(`HUMAN-OPS-SEQUENCE: FAIL: ${error.message}`);
  process.exitCode = 1;
});
