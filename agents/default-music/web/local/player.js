const get = (id) => document.getElementById(id);
const progress = get("progress");
const volume = get("volume");
const search = get("search");
const songList = get("songs");
const notice = get("notice");
const mp3File = get("mp3-file");
const uploadProgress = get("upload-progress");
const uploadStatus = get("upload-status");
const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
const normalize = (value) => value.normalize("NFKC").toLocaleLowerCase();

let catalog = [];
let catalogRevision;
let state = {};
let stateReceivedAt = 0;
let fetching = false;
let saving = false;
let uploading = false;
let previewingSeek = false;
let seekTrack;
let previewingVolume = false;
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
  const elapsed = state.playing ? Math.min(2, (performance.now() - stateReceivedAt) / 1000) : 0;
  const position = Math.min(duration, (state.position || 0) + elapsed);
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
  get("playback-label").textContent = state.loading ? "正在加载" : state.playing ? "正在播放" : state.paused ? "已暂停" : "等待播放";
  get("toggle").textContent = state.playing || state.loading ? "暂停" : state.paused ? "继续播放" : "播放";
  get("mode").value = state.mode || "order";
  get("mute").checked = Boolean(state.muted);
  get("mode").disabled = get("mute").disabled = volume.disabled = !state.ready;
  if (!previewingVolume) volume.value = state.volume ?? 50;
  get("volume-value").textContent = `${volume.value}%`;
  document.querySelectorAll("[data-action]").forEach((button) => { button.disabled = false; });
  renderProgress();
  updateHighlight();
}

async function request(message) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch("./api", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("Robot request failed");
    const result = await response.json();
    if (result?.success !== true) throw new Error("Robot rejected request");
    return result;
  } finally {
    clearTimeout(timer);
  }
}

async function refresh() {
  if (fetching || saving) return;
  fetching = true;
  try {
    const { state: next, revision, catalog: entries } = await request({ command: "status", revision: catalogRevision });
    if (state.track !== next.track) previewingSeek = false;
    state = next;
    stateReceivedAt = performance.now();
    if (entries) {
      catalog = entries.map(([id, title]) => ({ id, title, searchable: normalize(title) }));
      catalog.sort((a, b) => collator.compare(a.title, b.title));
      renderSongs();
    }
    catalogRevision = revision;
    renderPlayer();
    notice.textContent = state.ready ? "" : "正在加载播放设置…";
  } catch (error) {
    notice.textContent = "暂时读不到机器人数据，正在重试。";
  } finally {
    fetching = false;
  }
}

async function send(message) {
  saving = true;
  try {
    await request(message);
    notice.textContent = "";
  } catch (error) {
    notice.textContent = "操作没成功，请重试。";
    return false;
  } finally {
    saving = false;
  }
  refresh();
  return true;
}

function command(name, extra = {}) {
  commands = commands.then(() => send({ command: name, ...extra }));
  return commands;
}

function updateUploadButtons() {
  get("upload").disabled = get("upload-play").disabled = uploading || !mp3File.files.length;
}

function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const url = new URL("upload", window.location.href);
    url.searchParams.set("name", file.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.responseType = "json";
    xhr.timeout = 5 * 60 * 1000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round(event.loaded * 100 / event.total);
        uploadProgress.value = percent;
        uploadStatus.textContent = percent < 100 ? `正在上传 ${percent}%` : "正在检查 MP3…";
      }
    };
    xhr.onload = () => {
      if (xhr.status === 201 && xhr.response?.success) resolve(xhr.response);
      else reject(new Error(xhr.response?.message || "上传没完成，请重试。"));
    };
    xhr.onerror = xhr.ontimeout = () => reject(new Error("上传连接中断，请重试。"));
    xhr.send(file);
  });
}

async function uploadSelected(playAfter) {
  const file = mp3File.files[0];
  if (!file || uploading) return;
  if (!/\.mp3$/i.test(file.name) || !file.size || file.size > 64 * 1024 * 1024) {
    uploadStatus.textContent = "请选择不超过 64 MB 的 MP3 文件。";
    return;
  }
  uploading = true;
  mp3File.disabled = true;
  updateUploadButtons();
  uploadProgress.hidden = false;
  uploadProgress.value = 0;
  uploadStatus.textContent = "正在上传…";
  try {
    const result = await uploadFile(file);
    mp3File.value = "";
    const playing = playAfter && await command("select", { track: result.track });
    uploadStatus.textContent = playing ? `已上传并开始播放《${result.title}》`
      : playAfter ? `已上传《${result.title}》，请从歌单点播。` : `已加入歌单《${result.title}》`;
    refresh();
  } catch (error) {
    uploadStatus.textContent = error.message;
  } finally {
    uploadProgress.hidden = true;
    mp3File.disabled = false;
    uploading = false;
    updateUploadButtons();
  }
}

mp3File.addEventListener("change", () => {
  uploadStatus.textContent = "";
  updateUploadButtons();
});
get("upload").addEventListener("click", () => uploadSelected(false));
get("upload-play").addEventListener("click", () => uploadSelected(true));

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
  command("setting", { name: "volume", value: Number(volume.value) });
});
get("mute").addEventListener("change", (event) => command("setting", { name: "mute", value: event.target.checked }));
get("mode").addEventListener("change", (event) => command("setting", { name: "play_mode", value: event.target.value }));
get("close").addEventListener("click", () => (window.BoosterNativeBridge || window.parent.BoosterNativeBridge)?.close?.());

refresh();
setInterval(refresh, 1000);
setInterval(renderProgress, 250);
