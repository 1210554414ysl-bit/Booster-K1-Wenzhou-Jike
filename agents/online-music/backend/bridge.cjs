"use strict";

// Keep provider diagnostics separate from the JSON protocol on stdout.
const send = process.stdout.write.bind(process.stdout);
console.log = console.info = console.debug = () => {};
console.warn = console.error = () => {};
const api = require("@neteasecloudmusicapienhanced/api");
const readline = require("node:readline");
const allowed = new Set([
  "cloudsearch", "login_qr_key", "login_qr_create", "login_qr_check",
  "login_status", "user_playlist", "playlist_track_all", "playlist_detail",
  "song_url",
]);

readline.createInterface({ input: process.stdin }).on("line", async (line) => {
  let message;
  try {
    message = JSON.parse(line);
    if (!allowed.has(message.method)) throw new Error("Unsupported provider operation");
    const result = await api[message.method]({
      ...message.args,
      timeout: 12000,
      unblock: "false",
    });
    send(JSON.stringify({ id: message.id, body: result.body }) + "\n");
  } catch (error) {
    // API exceptions may contain request cookies. Never forward those objects.
    send(JSON.stringify({ id: message?.id, error: "网易云接口请求失败，请稍后重试。" }) + "\n");
  }
});
