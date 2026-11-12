"use strict";

const get = (id) => document.getElementById(id);
let state = {};
let accounts = {};
let queue = [];
let results = [];
let playlists = [];
let visible = [];
let view = "results";
let page = 1;
let more = false;
let query = "";
let playlist = null;
let revision;
let listGeneration = 0;
let loginGeneration = 0;
let loginTimer;
let refreshing = false;
let draggingVolume = false;
let seekingTrack;
let transport = Promise.resolve();

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

async function api(message, timeout = 45000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch("./api", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message), signal: controller.signal,
    });
    if (!response.ok) throw new Error("机器人请求失败，请重试。");
    const result = await response.json();
    if (!result.success) throw new Error(result.message || "操作未完成，请重试。");
    return result;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请检查机器人网络后重试。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function renderPlayer() {
  get("track-title").textContent = state.title || "搜索并选择一首歌";
  get("playback-label").textContent = state.loading ? "正在加载" : state.playing ? "正在播放" : state.paused ? "已暂停" : "等待播放";
  get("toggle").textContent = state.loading || state.playing ? "暂停" : state.paused ? "继续播放" : "播放";
  get("elapsed").textContent = formatTime(state.position);
  get("duration").textContent = formatTime(state.duration);
  const progress = get("progress");
  progress.disabled = !state.track || !state.duration || state.loading;
  if (!seekingTrack) progress.value = state.duration ? 100 * state.position / state.duration : 0;
  if (!draggingVolume) get("volume").value = state.volume ?? 50;
  get("volume-value").textContent = `${get("volume").value}%`;
  get("mode").value = state.mode || "order";
  get("mute").checked = Boolean(state.muted);
  for (const control of document.querySelectorAll("[data-action], #volume, #mode, #mute")) control.disabled = !state.ready;
  get("playback-error").textContent = state.error || "";
  const account = accounts[get("provider").value];
  const canLogin = account?.can_login !== false;
  get("account-status").textContent = !canLogin ? "公共搜索" : account?.logged_in ? (account.name && account.name !== "已登录" ? `已登录 · ${account.name}` : "已登录") : "未登录";
  get("login").hidden = !canLogin;
  get("logout").hidden = !canLogin || !account?.logged_in;
  get("playlists-tab").hidden = account?.can_playlists === false;
  get("login").textContent = account?.logged_in ? "重新登录" : "扫码登录";
  for (const button of document.querySelectorAll("button[data-track]")) {
    if (button.dataset.track === state.track) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const result = await api({ command: "status", revision }, 3000);
    if (state.track !== result.state.track) seekingTrack = undefined;
    state = result.state;
    accounts = result.accounts;
    if (result.catalog) {
      queue = result.catalog.map(([key, title]) => ({ key, title, artist: "" }));
      if (view === "queue") renderList();
    }
    revision = result.revision;
    renderPlayer();
  } catch (error) {
    get("playback-error").textContent = "暂时连接不到机器人，正在重试。";
  } finally {
    refreshing = false;
  }
}

function command(name, extra = {}) {
  transport = transport.then(async () => {
    try {
      await api({ command: name, ...extra }, 5000);
      await refresh();
    } catch (error) {
      get("notice").textContent = error.message;
    }
  });
  return transport;
}

function renderList() {
  visible = view === "playlists" ? playlists : view === "queue" ? queue : results;
  const fragment = document.createDocumentFragment();
  visible.forEach((item, index) => {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "song";
    if (view === "playlists") button.dataset.playlist = item.id;
    else button.dataset.track = item.key;
    const number = document.createElement("span");
    number.className = "song-number";
    number.textContent = index + 1;
    const text = document.createElement("span");
    text.className = "song-info";
    const title = document.createElement("span");
    title.textContent = item.title;
    const detail = document.createElement("span");
    detail.className = "muted";
    detail.textContent = view === "playlists" ? `${item.count} 首` : item.artist || "";
    text.append(title, detail);
    button.append(number, text);
    row.append(button);
    fragment.append(row);
  });
  get("songs").replaceChildren(fragment);
  get("song-count").textContent = `${visible.length} ${view === "playlists" ? "个歌单" : "首"}`;
  get("list-title").textContent = view === "playlists" ? "我的歌单" : view === "queue" ? "播放队列" : playlist?.title || "搜索结果";
  get("back-playlists").hidden = !playlist || view !== "results";
  get("load-more").hidden = !more || view === "queue";
  get("results-tab").setAttribute("aria-pressed", String(view === "results"));
  get("playlists-tab").setAttribute("aria-pressed", String(view === "playlists"));
  get("queue-tab").setAttribute("aria-pressed", String(view === "queue"));
  renderPlayer();
}

async function loadList(append = false) {
  const generation = ++listGeneration;
  const provider = get("provider").value;
  const requestedView = view;
  const nextPage = append ? page + 1 : 1;
  get("notice").textContent = "正在读取…";
  get("load-more").disabled = true;
  let message;
  if (view === "playlists") message = { command: "playlists" };
  else if (playlist) message = { command: "playlist_tracks", playlist: playlist.id };
  else message = { command: "search", query };
  try {
    const result = await api({ ...message, provider, page: nextPage });
    if (generation !== listGeneration || provider !== get("provider").value || requestedView !== view) return;
    page = nextPage;
    more = Boolean(result.more);
    if (view === "playlists") playlists = append ? playlists.concat(result.items) : result.items;
    else results = append ? results.concat(result.items) : result.items;
    get("notice").textContent = result.items.length ? "" : "没有找到内容。";
    renderList();
  } catch (error) {
    if (generation === listGeneration) get("notice").textContent = error.message;
  } finally {
    if (generation === listGeneration) get("load-more").disabled = false;
  }
}

function cancelLogin() {
  loginGeneration++;
  clearTimeout(loginTimer);
  get("qr-image").hidden = true;
}

async function startLogin() {
  cancelLogin();
  const generation = loginGeneration;
  const provider = get("provider").value;
  if (provider !== "netease") return;
  get("qr-status").textContent = "正在获取二维码…";
  get("retry-login").hidden = true;
  try {
    const result = await api({ command: "login_start", provider });
    if (generation !== loginGeneration) return;
    get("qr-image").src = result.image;
    get("qr-image").hidden = false;
    get("qr-status").textContent = result.hint;
    const deadline = Date.now() + result.expires_in * 1000;
    const poll = async () => {
      if (generation !== loginGeneration) return;
      try {
        if (Date.now() > deadline) throw new Error("二维码已过期，请重新获取。");
        const reply = await api({ command: "login_poll", provider, session: result.session });
        if (generation !== loginGeneration) return;
        if (reply.status === "confirmed") {
          get("login-dialog").close();
          get("notice").textContent = "登录成功，可以搜索歌曲或打开我的歌单。";
          await refresh();
          return;
        }
        if (reply.status === "expired") throw new Error("二维码已过期或登录已取消，请重新获取。");
        get("qr-status").textContent = reply.status === "scanned" ? "已扫描，请在手机上确认登录。" : result.hint;
        loginTimer = setTimeout(poll, 2500);
      } catch (error) {
        if (generation !== loginGeneration) return;
        get("qr-status").textContent = error.message;
        get("retry-login").hidden = false;
      }
    };
    loginTimer = setTimeout(poll, 2500);
  } catch (error) {
    if (generation !== loginGeneration) return;
    get("qr-status").textContent = error.message;
    get("retry-login").hidden = false;
  }
}

get("provider").addEventListener("change", () => {
  cancelLogin();
  listGeneration++;
  results = [];
  playlists = [];
  playlist = null;
  more = false;
  view = "results";
  get("notice").textContent = "输入歌名开始搜索";
  renderList();
});
get("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  query = get("search").value.trim();
  if (!query) return;
  playlist = null;
  view = "results";
  loadList();
});
get("playlists-tab").addEventListener("click", () => { view = "playlists"; loadList(); });
get("back-playlists").addEventListener("click", () => { playlist = null; view = "playlists"; loadList(); });
get("results-tab").addEventListener("click", () => {
  listGeneration++;
  view = "results";
  if (query || playlist) loadList();
  else renderList();
});
get("queue-tab").addEventListener("click", () => { listGeneration++; view = "queue"; renderList(); });
get("load-more").addEventListener("click", () => loadList(true));
get("songs").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button?.dataset.playlist) {
    playlist = playlists.find((item) => item.id === button.dataset.playlist);
    view = "results";
    loadList();
  } else if (button?.dataset.track) {
    command("select", { track: button.dataset.track, queue: visible.map((item) => item.key) });
  }
});
document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => command(button.dataset.action)));
get("volume").addEventListener("input", () => { draggingVolume = true; get("volume-value").textContent = `${get("volume").value}%`; });
get("volume").addEventListener("change", () => { draggingVolume = false; command("setting", { name: "volume", value: Number(get("volume").value) }); });
get("mode").addEventListener("change", () => command("setting", { name: "play_mode", value: get("mode").value }));
get("mute").addEventListener("change", () => command("setting", { name: "mute", value: get("mute").checked }));
get("progress").addEventListener("input", () => {
  seekingTrack ??= state.track;
  get("elapsed").textContent = formatTime(state.duration * Number(get("progress").value) / 100);
});
get("progress").addEventListener("change", () => {
  const track = seekingTrack;
  seekingTrack = undefined;
  if (track && track === state.track) command("seek", { track, percent: Number(get("progress").value) });
});
get("progress").addEventListener("pointercancel", () => { seekingTrack = undefined; });
get("login").addEventListener("click", () => { get("login-dialog").showModal(); startLogin(); });
get("retry-login").addEventListener("click", startLogin);
get("close-login").addEventListener("click", () => get("login-dialog").close());
get("login-dialog").addEventListener("close", cancelLogin);
get("logout").addEventListener("click", async () => {
  try {
    await api({ command: "logout", provider: get("provider").value });
    playlists = [];
    if (view === "playlists") renderList();
    await refresh();
  } catch (error) { get("notice").textContent = error.message; }
});
get("close").addEventListener("click", () => (window.BoosterNativeBridge || window.parent.BoosterNativeBridge)?.close?.());
refresh();
setInterval(refresh, 750);
