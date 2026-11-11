import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [panel, chrome] = process.argv.slice(2);
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function wait(check) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const value = await check();
    if (value) return value;
    await pause(100);
  }
  throw new Error("Browser check timed out");
}
const profile = await mkdtemp(join(tmpdir(), "k1-online-check-"));
const browser = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--window-size=1080,1000", panel], { stdio: "ignore", windowsHide: true });
let socket;
try {
  const port = await wait(async () => {
    try { return Number((await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]); }
    catch { return undefined; }
  });
  const target = await wait(async () => (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((item) => item.type === "page" && item.url === panel));
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const reply = JSON.parse(data);
    const request = pending.get(reply.id);
    if (!request) return;
    pending.delete(reply.id);
    clearTimeout(request.timer);
    if (reply.error || reply.result?.exceptionDetails) request.reject(new Error(JSON.stringify(reply.error || reply.result.exceptionDetails)));
    else request.resolve(reply.result);
  });
  function cdp(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out`)); }, 10000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    return result.result.value;
  }
  await wait(() => evaluate("Boolean(document.getElementById('toggle')) && !document.getElementById('toggle').disabled"));
  await evaluate("document.getElementById('search').value='测试'; document.getElementById('search-form').requestSubmit()");
  await wait(() => evaluate("document.querySelectorAll('#songs button[data-track]').length === 4"));
  await evaluate("document.querySelector('#songs button[data-track]').click()");
  await wait(() => evaluate("document.getElementById('playback-label').textContent === '正在播放' && Number(document.getElementById('progress').value) > 1"));
  await evaluate("document.getElementById('toggle').click()");
  await wait(() => evaluate("document.getElementById('toggle').textContent === '继续播放'"));
  const before = await evaluate("document.getElementById('progress').value");
  await pause(1000);
  assert.equal(await evaluate("document.getElementById('progress').value"), before);
  await evaluate("document.getElementById('volume').value=28; document.getElementById('volume').dispatchEvent(new Event('change'))");
  await wait(() => evaluate("document.getElementById('volume-value').textContent === '28%'"));
  await evaluate("document.getElementById('playlists-tab').click()");
  await wait(() => evaluate("document.querySelectorAll('#songs button[data-playlist]').length === 1"));
  await evaluate("document.querySelector('#songs button[data-playlist]').click()");
  await wait(() => evaluate("document.getElementById('list-title').textContent === '测试歌单' && document.querySelectorAll('#songs button[data-track]').length === 4"));
  await evaluate("document.getElementById('login').click()");
  await wait(() => evaluate("document.getElementById('login-dialog').open && !document.getElementById('qr-image').hidden"));
  await evaluate("document.getElementById('close-login').click()");
  assert.equal(await evaluate("document.getElementById('login-dialog').open"), false);
  await evaluate("document.getElementById('provider').value='qq'; document.getElementById('provider').dispatchEvent(new Event('change'))");
  assert.equal(await evaluate("document.getElementById('login').hidden && document.getElementById('logout').hidden && document.getElementById('playlists-tab').hidden"), true);
  assert.equal(await evaluate("document.getElementById('account-status').textContent"), "公共搜索");
  await evaluate("document.getElementById('provider').value='netease'; document.getElementById('provider').dispatchEvent(new Event('change'))");
  assert.equal(await evaluate("!document.getElementById('login').hidden && !document.getElementById('playlists-tab').hidden"), true);
  if (process.env.K1_SCREENSHOT) {
    const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(process.env.K1_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
  }
  console.log("PASS browser playback and playlists, NetEase login, and QQ login controls removed");
} finally {
  socket?.close();
  browser.kill();
}
