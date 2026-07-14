#!/usr/bin/env node
// Produce a screenshot of a URL by driving the shared HOST browser over CDP.
// Runs INSIDE a microsandbox guest (where Chromium can't launch) and proves the
// visual-proof path end to end. Dependency-free: raw CDP over the DevTools
// WebSocket, no npm install.
//
// Usage (inside a guest, after `run_host_browser.sh` is up on the host):
//   node verify_guest_screenshot.js <cdp-url> <target-url> <out.png>
//   node verify_guest_screenshot.js http://host.microsandbox.internal:9222 \
//        http://host.microsandbox.internal:9100/ /root/.proof/taskit.png
//
// This is exactly what chrome-devtools-mcp does when launched with
// `--browserUrl <cdp-url>`; agents normally use the MCP's take_screenshot tool
// and never call this directly. It exists as an operator verification harness.

const http = require('http');
const fs = require('fs');

const [cdpUrl, targetUrl, outPath] = process.argv.slice(2);
if (!cdpUrl || !targetUrl || !outPath) {
  console.error('usage: node verify_guest_screenshot.js <cdp-url> <target-url> <out.png>');
  process.exit(2);
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { timeout: 10000 }, (res) => {
      let d = '';
      res.on('data', (c) => (d += c));
      res.on('end', () => resolve(JSON.parse(d)));
    }).on('error', reject).on('timeout', function () { this.destroy(new Error('timeout')); });
  });
}

async function main() {
  // Discover the browser-level WebSocket endpoint.
  const version = await getJson(`${cdpUrl}/json/version`);
  const wsUrl = version.webSocketDebuggerUrl;
  if (!wsUrl) throw new Error('no webSocketDebuggerUrl from ' + cdpUrl);

  // Open a fresh page target (isolation: one target per verification run).
  const target = await getJson(`${cdpUrl}/json/new?${encodeURIComponent(targetUrl)}`);
  const pageWs = target.webSocketDebuggerUrl;

  // Prefer Node's built-in global WebSocket (stable since Node 21); fall back to
  // the `ws` package only if present. No hard dependency either way.
  const tryRequire = (m) => { try { const x = require(m); return x.WebSocket || x; } catch { return null; } };
  const WS = globalThis.WebSocket || tryRequire('ws');
  if (!WS) throw new Error('No WebSocket implementation available in this Node.');

  const ws = new WS(pageWs);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) =>
    new Promise((resolve) => { const i = ++id; pending.set(i, resolve); ws.send(JSON.stringify({ id: i, method, params })); });

  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
  };

  await send('Page.enable');
  await send('Page.navigate', { url: targetUrl });
  await new Promise((r) => setTimeout(r, 2500)); // settle
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.mkdirSync(require('path').dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, Buffer.from(shot.data, 'base64'));
  console.log('wrote', outPath, fs.statSync(outPath).size, 'bytes');
  ws.close();
  process.exit(0);
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
