const get = (id) => document.getElementById(id);
const progress = get("progress");
const volume = get("volume");
const search = get("search");
const songList = get("songs");
const notice = get("notice");
const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
const normalize = (value) => value.normalize("NFKC").toLocaleLowerCase();

let catalog = [];
let catalogValue;
let state = {};
let fetching = false;
let saving = false;
let previewingSeek = false;
let seekTrack;
let previewingVolume = false;
let nonce = 0;
let highlighted;
let commands = Promise.resolve();

function formatTime(seconds) {
  const whole = Math.max(0, Math.floor(seconds || 0));
  const pad = (n) => String(n).padStart(2, "0");
  const hours = Math.floor(whole / 3600);
  const minutes = pad(Math.floor(whole / 60) % 60);
  return `${hours ? `${hours}:` : ""}${minutes}:${pad(whole % 60)}`;
}

function renderProgress() {
  const duration = state.duration || 0;
  const elapsed = state.started ? Math.max(0, Date.now() / 1000 - state.started) : 0;
  const position = Math.min(duration, (state.offset || 0) + elapsed);
  if (!previewingSeek) {
    progress.value = duration ? 100 * position / duration : 0;
  }
  progress.disabled = !state.track || !duration || state.loading;
  const displayed = previewingSeek ? duration * Number(progress.value) / 100 : position;
  get("elapsed").textContent = formatTime(displayed);
  get("duration").textContent = formatTime(duration);
  progress.setAttribute("aria-valuetext", `${formatTime(displayed)} / ${formatTime(duration)}`);
}

function updateHighlight() {
  if (highlighted?.dataset.track === state.track) return;
  highlighted?.removeAttribute("aria-current");
  highlighted = state.track ? document.getElementById(`song-${state.track}`) : undefined;
  highlighted?.setAttribute("aria-current", "true");
}

function renderSongs() {
  const words = normalize(search.value).trim().split(/\s+/).filter(Boolean);
  const matches = catalog.filter(({ searchable }) => words.every((word) => searchable.includes(word)));
  const fragment = document.createDocumentFragment();
  matches.forEach(({ id, title }, index) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const number = document.createElement("span");
    const name = document.createElement("span");
    button.id = `song-${id}`;
    button.className = "song";
    button.type = "button";
    button.dataset.track = id;
    number.className = "song-number";
    number.textContent = `${index + 1}`;
    number.setAttribute("aria-hidden", "true");
    name.textContent = title;
    button.append(number, name);
    item.append(button);
    fragment.append(item);
  });
  songList.replaceChildren(fragment);
  highlighted = undefined;
  updateHighlight();
  get("song-count").textContent = words.length ? `找到 ${matches.length} / ${catalog.length} 首` : `共 ${catalog.length} 首`;
  get("empty").hidden = Boolean(matches.length);
  get("empty").textContent = words.length ? "没有匹配的歌曲" : "歌单里还没有歌曲";
}

function renderPlayer() {
  get("track-title").textContent = state.title || "从下方歌单选一首歌";
  get("playback-label").textContent = state.loading ? "正在加载" : state.started ? "正在播放" : state.paused ? "已暂停" : "等待播放";
  get("toggle").textContent = state.started || state.loading ? "暂停" : state.paused ? "继续播放" : "播放";
  get("mode").value = state.mode || "order";
  get("mute").checked = Boolean(state.muted);
  get("mode").disabled = get("mute").disabled = volume.disabled = !state.ready;
  if (!previewingVolume) volume.value = state.volume ?? 50;
  get("volume-value").textContent = `${volume.value}%`;
  renderProgress();
  updateHighlight();
}

async function refresh() {
  if (fetching || saving) return;
  const bridge = window.BoosterNativeBridge;
  if (!bridge) {
    notice.textContent = "请在 Booster App 的音乐播放器参数面板中打开。";
    return;
  }
  fetching = true;
  try {
    const { parameters } = await bridge.getParameters();
    const next = JSON.parse(parameters.player_status || "{}");
    if (typeof next.mode !== "string") {
      notice.textContent = "正在等待机器人加载歌单…";
      return;
    }
    if (state.track !== next.track) previewingSeek = false;
    state = next;
    if (catalogValue !== parameters.music_catalog) {
      const entries = JSON.parse(parameters.music_catalog || "[]");
      catalog = entries.map(([id, title]) => ({ id, title, searchable: normalize(title) }));
      catalog.sort((a, b) => collator.compare(a.title, b.title));
      catalogValue = parameters.music_catalog;
      renderSongs();
    }
    renderPlayer();
    notice.textContent = state.ready ? "" : "正在加载播放设置…";
  } catch (error) {
    notice.textContent = "暂时读不到机器人数据，正在重试。";
  } finally {
    fetching = false;
  }
}

async function send(parameters) {
  if (!window.BoosterNativeBridge) return;
  saving = true;
  try {
    const result = await window.BoosterNativeBridge.saveParameters(parameters);
    if (result?.success === false) throw new Error("Parameter rejected");
    notice.textContent = "";
  } catch (error) {
    notice.textContent = "操作没成功，请重试。";
    return;
  } finally {
    saving = false;
  }
  await refresh();
}

function save(parameters) {
  commands = commands.then(() => send(parameters));
  return commands;
}

function command(action, extra = {}) {
  return save({ player_command: JSON.stringify({ action, ...extra, nonce: `${Date.now()}-${++nonce}` }) });
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => command(button.dataset.action));
});
songList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-track]");
  if (button) command("select", { track: button.dataset.track });
});
search.addEventListener("input", renderSongs);
function clearSeekPreview() {
  seekTrack = undefined;
  previewingSeek = false;
}
progress.addEventListener("pointerdown", () => { seekTrack = state.track; });
progress.addEventListener("pointerup", () => setTimeout(clearSeekPreview, 0));
progress.addEventListener("pointercancel", clearSeekPreview);
progress.addEventListener("input", () => {
  seekTrack ??= state.track;
  previewingSeek = seekTrack === state.track;
  renderProgress();
});
progress.addEventListener("change", () => {
  const percent = Number(progress.value);
  const track = seekTrack ?? state.track;
  clearSeekPreview();
  if (track && track === state.track) command("seek", { track, percent });
});
volume.addEventListener("input", () => {
  previewingVolume = true;
  get("volume-value").textContent = `${volume.value}%`;
});
volume.addEventListener("change", () => {
  previewingVolume = false;
  save({ volume: Number(volume.value) });
});
get("mute").addEventListener("change", (event) => save({ mute: event.target.checked }));
get("mode").addEventListener("change", (event) => save({ play_mode: event.target.value }));
get("close").addEventListener("click", () => window.BoosterNativeBridge?.close());

refresh();
setInterval(refresh, 1000);
setInterval(renderProgress, 250);
