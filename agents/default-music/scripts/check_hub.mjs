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
  await wait(() => evaluate("Boolean(document.getElementById('local-frame').contentDocument?.getElementById('toggle'))"));
  assert.equal(await evaluate("document.getElementById('local-frame').hidden"), false);
  await evaluate("window.BoosterNativeBridge={close:()=>window.closedByBridge=true}");
  await evaluate("document.getElementById('local-frame').contentDocument.getElementById('close').click()");
  assert.equal(await evaluate("window.closedByBridge"), true);
  await evaluate("window.closedByBridge=false;document.getElementById('online').click()");
  await wait(() => evaluate("Boolean(document.getElementById('online-frame').contentDocument?.getElementById('search-form'))"));
  assert.equal(await evaluate("document.getElementById('local-frame').hidden && !document.getElementById('online-frame').hidden"), true);
  await evaluate("document.getElementById('online-frame').contentDocument.getElementById('close').click()");
  assert.equal(await evaluate("window.closedByBridge"), true);
  await evaluate("document.getElementById('local').click()");
  assert.equal(await evaluate("!document.getElementById('local-frame').hidden && document.getElementById('online-frame').hidden"), true);
  if (process.env.K1_SCREENSHOT) {
    const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(process.env.K1_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
  }
  console.log("PASS hub source navigation, both embedded panels and return-to-native controls");
} finally {
  socket?.close();
  browser.kill();
}
