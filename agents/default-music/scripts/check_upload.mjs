import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile } from "node:fs/promises";
import { basename } from "node:path";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [panel, chrome, file] = process.argv.slice(2);
const name = basename(file);
const title = name.slice(0, -4);
const payload = await readFile(file);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function wait(test) {
  for (let i = 0; i < 70; i++) {
    const result = await test();
    if (result) return result;
    await sleep(100);
  }
  throw new Error("Timed out waiting for test condition");
}

async function api(message) {
  const response = await fetch(new URL("api", panel), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message), signal: AbortSignal.timeout(4000),
  });
  return response.json();
}

function upload(filename, body, path = "upload") {
  const url = new URL(path, panel);
  url.searchParams.set("name", filename);
  return fetch(url, {
    method: "POST", headers: { "Content-Type": "application/octet-stream" },
    body, signal: AbortSignal.timeout(18000),
  });
}

const initial = await api({ command: "status" });
assert(!initial.state.playing && !initial.state.loading);
assert(!initial.catalog.some(([, song]) => song === title));
assert.equal((await upload(name, Buffer.from("test"), "/upload")).status, 404);
assert.equal((await upload(`../${name}`, Buffer.from("test"))).status, 400);
assert.equal((await upload(name, Buffer.from("invalid mp3"))).status, 400);
assert.equal((await api({ command: "status" })).catalog.length, initial.catalog.length);
console.log("PASS: unauthorised, unsafe-name and invalid-MP3 requests rejected");

const profile = await mkdtemp(join(tmpdir(), "k1-default-upload-"));
const browser = spawn(chrome, [
  "--headless=new", "--disable-gpu", "--disable-extensions", "--no-first-run",
  "--disable-background-networking", "--remote-debugging-port=0", `--user-data-dir=${profile}`, panel,
], { stdio: "ignore", windowsHide: true });
let socket;
try {
  const port = await wait(async () => {
    try { return Number((await readFile(`${profile}/DevToolsActivePort`, "utf8")).split("\n")[0]); }
    catch { return undefined; }
  });
  const target = await wait(async () => (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json())
    .find((entry) => entry.type === "page" && entry.url === panel));
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  await sleep(800);
  let id = 0;
  function cdp(method, params = {}) {
    return new Promise((resolve, reject) => {
      const request = ++id;
      const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 10000);
      const listener = ({ data }) => {
        const reply = JSON.parse(data);
        if (reply.id !== request) return;
        socket.removeEventListener("message", listener);
        clearTimeout(timer);
        if (reply.error || reply.result?.exceptionDetails) reject(new Error(JSON.stringify(reply.error || reply.result.exceptionDetails)));
        else resolve(reply.result);
      };
      socket.addEventListener("message", listener);
      socket.send(JSON.stringify({ id: request, method, params }));
    });
  }
  const { root } = await cdp("DOM.getDocument");
  const { nodeId } = await cdp("DOM.querySelector", { nodeId: root.nodeId, selector: "#mp3-file" });
  assert(nodeId);
  await cdp("DOM.setFileInputFiles", { nodeId, files: [file] });
  const selected = await cdp("Runtime.evaluate", {
    expression: "document.getElementById('mp3-file').files.length === 1 && !document.getElementById('upload-play').disabled",
    returnByValue: true,
  });
  assert.equal(selected.result.value, true);
  await cdp("Runtime.evaluate", { expression: "document.getElementById('upload-play').click()" });
  const played = await wait(async () => {
    const { state } = await api({ command: "status", revision: initial.revision + 1 });
    return state.playing && state.title === title && state.duration > 10 && state;
  });
  const current = await api({ command: "status" });
  assert(current.catalog.some(([key, song]) => key === played.track && song === title));
  assert.equal(current.catalog.length, initial.catalog.length + 1);
  const display = await cdp("Runtime.evaluate", {
    expression: "document.getElementById('upload-status').textContent", returnByValue: true,
  });
  assert(display.result.value.includes("已上传并开始播放"), display.result.value);
  assert((await api({ command: "play_pause" })).success);
  await wait(async () => (await api({ command: "status", revision: current.revision })).state.paused);
  console.log("PASS: real browser selected a Unicode MP3, uploaded it to K1, and played it");

  const duplicate = await upload(name, payload);
  assert.equal(duplicate.status, 201);
  const item = await duplicate.json();
  assert.equal(item.title, `${title} (2)`);
  assert.notEqual(item.track, played.track);
  assert.equal((await api({ command: "status" })).catalog.length, initial.catalog.length + 2);
  console.log("PASS: same-name upload created a separate song without overwriting");
} finally {
  socket?.close();
  browser.kill();
  const status = (await api({ command: "status" })).state;
  if (status.title === title && (status.playing || status.loading)) await api({ command: "play_pause" });
}
