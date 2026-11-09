# 音乐播放器 · Music Player

在 Booster K1 上播放**在线音频直链**和**本地音频文件**的 Agent，带播放模式、进度跳转和系统音量控制。

| 项 | 值 |
| --- | --- |
| 包 ID / 版本 | `com.wenzhoujike.k1.music` / `1.0.0` |
| 显示名称 | 音乐播放器（Music Player） |
| 入口 | `src/main.py:MusicPlayerAgent` |
| 适用机型 | Booster K1 |
| 支持平台 | `sim_x86_64`、`sim_aarch64`、`real_jetson`、`real_qcom` |
| 最低 API Level | `10700` |
| 依赖 | 机器人上有 `ffmpeg`（boosteros 自身也依赖它） |

---

## 一、功能

| 需求 | 实现情况 |
| --- | --- |
| 两个 Tab：在线 / 本地 | ✅ 用 `ComponentStatePageProxy` 的两个页面实现（见下方「界面」） |
| 在线播放 mp3 直链 | ✅ 参数页填 URL → 点「播放直链」 |
| 输入歌名搜索音频 | ✅ 参数页填关键词 → 点「搜索」（走 iTunes 公开搜索接口） |
| 网络异常 Toast / 无网禁用在线 Tab | ✅ 每 20s 探测网络；断网自动切回本地页并提示 |
| 本地音乐扫描（mp3 / wav） | ✅ 只扫 **Agent 私有目录**，不碰机器人其它目录 |
| 顺序 / 随机 / 单曲循环 | ✅ 三种模式，图标随模式切换 |
| 播放/暂停、上一首、下一首 | ✅ 图标按钮 + 手柄快捷键 |
| 进度条 + 拖动跳转 + 时间显示 | ✅ 参数页「播放进度」滑块；状态栏显示 `当前/总时长` |
| 音量 0~100 + 一键静音 | ✅ 参数页音量滑块 + 静音按钮，直接调 K1 系统音量 |
| 顺序播完停止 / 随机不重复一轮 / 单曲循环重播 | ✅ 见「播放模式规则」 |
| 保留播放模式与音量 | ✅ 存在 `music_player_settings.json`，**不保留进度** |
| 异常 Toast（链接失效/文件损坏/格式不支持） | ✅ |
| 音频逻辑不阻塞主线程 | ✅ 全部在后台线程 + 队列，回调立即返回 |
| 当前歌曲高亮 / loading / 中文界面 | ✅ 状态栏高亮当前曲目，加载时播放键显示环形 loading |

---

## 二、界面说明

App 里 Agent 的 UI 由**两块**组成，这是框架的既定形态：

### 1. 控制面板（图标网格）

**本地音乐页**

| 图标 | 作用 |
| --- | --- |
| 🌐 在线音乐 | 切到在线 Tab（无网时点击会提示禁用） |
| ⏮ 上一首 | 上一曲 |
| ▶/⏸ 播放/暂停 | 播放、暂停、继续 |
| ⏭ 下一首 | 下一曲 |
| 🔀 播放模式 | 顺序 → 随机 → 单曲循环（图标随模式变化） |
| 🔇 静音 | 静音 / 取消静音 |
| **🔉 音量 -** | 系统音量 −10%（每点一次 10%，Toast 显示当前百分比） |
| **🔊 音量 +** | 系统音量 +10% |
| 🔍+ 刷新歌单 | 重新扫描本地音乐目录 |

**在线音乐页**

| 图标 | 作用 |
| --- | --- |
| 📁 本地音乐 | 切回本地 Tab |
| 🔍 搜索 | 用参数页的关键词搜索音频 |
| 🔗 播放直链 | 播放参数页填写的音频直链 |
| ⏮ / ▶ / ⏭ / 🔇 | 同上 |
| **🔉 / 🔊** | 音量 −/+ 10%，与参数页滑块联动 |

第一行那个音乐符图标是**状态栏**，实时显示：

```
▶ 3/12 稻香  01:23/04:56
```

- `▶` 播放中 / `⏸` 暂停 / `…` 加载中
- `3/12` 当前曲在歌单里的位置
- 时间来自进度计算（MP3 走解码前探测的时长）

### 2. 参数页（schema.json 驱动，App 内「参数」入口）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| 音量 | 整数 0~100 | 直接映射到 K1 系统音量 0.0~1.0 |
| 静音 | 开关 | 与静音按钮联动 |
| **播放进度** | 浮点 0~100 | **拖动即跳转**到该百分比位置 |
| 播放模式 | 单选 | 顺序 / 随机 / 单曲循环 |
| 搜索关键词 | 文本 | 在线 Tab 点「搜索」时使用 |
| 音频直链 | 文本 | 在线 Tab 点「播放直链」时使用 |

> ⚠️ **进度条和音量滑块在参数页，不在控制面板**。原因是 Agent 的控制面板只能放图标按钮（框架只提供 `IconComponent` 系列），滑块/文本这类控件只能由参数页渲染。这是目前框架的硬限制，不是有意省略。

---

## 三、手柄快捷键

| 快捷键 | 功能 |
| --- | --- |
| `A` | 播放 / 暂停 |
| `X` | 上一首 |
| `B` | 下一首 |
| `Y` | 静音 |
| `LB + A` | 切换播放模式 |
---

## 四、放本地音乐

1. 连上机器人，把 `.mp3` / `.wav` 文件放进 **Agent 私有目录**：

   ```
   <Agent 私有存储目录>/music/
   ```

   （该目录由 Agent 首次启动时自动创建；路径可从 Agent 日志里的 `local scan: N track(s) under ...` 看到）

2. 回到 App，点「刷新歌单」图标，或重启 Agent（**启动会自动扫描**）

支持的格式：**mp3、wav**。扫描深度最多 4 层，只在该私有目录内，**不会全盘扫描机器人文件系统**。

---

## 五、在线音乐（真实全曲播放）

### 方式 1：搜索（推荐）

1. 参数页 → 搜索关键词 → 填歌名（如「稻香」）
2. 在线 Tab → 点「搜索」
3. 状态栏出现 `1/8 歌名`，点播放键开始

**工作方式**（v1.1.0 起）：

```
搜索  https://music.163.com/api/search/get/web?s=<关键词>&type=1
  ↓   拿到歌曲 id / 名称 / 歌手 / 时长
解析  https://music.163.com/api/song/enhance/player/url?ids=[<id>]&br=320000
  ↓   拿到真实 mp3 直链（HTTP 206，支持 Range）
下载  存到 <Agent存储>/cache/online_<hash>.mp3
  ↓
播放  robot.play_sound(本地文件)
```

**实测**：搜索「稻香」→ 解析 → 下载 8.5 MB → ffprobe 确认 `duration=218.49s / 320 kbps`，
是**完整歌曲**而不是试听片段。

> - 需要机器人能访问 `music.163.com`（这台机器人实测通）
> - 无版权 / 需会员的曲目拿不到地址，会 Toast「无法播放」并继续下一首
> - 不做任何登录，只使用公开接口返回的直链

### 方式 2：直链

1. 参数页 → 音频直链 → 填 `https://.../song.mp3`
2. 在线 Tab → 点「播放直链」

支持 http/https 的 mp3 / wav 直链，**不做任何登录或鉴权**。

---

## 六、播放模式规则

| 模式 | 一曲放完后的行为 |
| --- | --- |
| 顺序播放 | 下一首；**放到列表末尾就停止**（不循环） |
| 随机播放 | 从洗好的包里取下一首，**一轮内不重复**；包空了重新洗牌 |
| 单曲循环 | 从头重播当前曲 |

- 「下一首 / 上一首」是**手动**操作：即使在顺序模式，末尾也能循环回去
- 断网时在线播放会失败并 Toast，不会卡死

---

## 七、进度与音量

**进度**

- 状态栏每秒刷新一次 `当前时间/总时长`
- 拖动参数页「播放进度」滑块 → 从对应位置继续播放
- 时长在首次播放时用 `ffmpeg -i` 探测并缓存

**音量**

- 直接调 `robot.audio_manager.set_system_volume(0.0 ~ 1.0)`，改变的是 **K1 系统音量**（和其他声音一起变）
- 静音 = 记为 0，取消静音恢复到「最后一个非零音量」
- 音量与模式写入 `music_player_settings.json`，重启 Agent 自动恢复

---

## 八、已知限制（重要）

1. **歌单不能点选** —— 框架的控制面板只有图标组件，没有列表控件。位置用「上一首 / 下一首」切换，状态栏显示 `3/12`。要可点选歌单只能用 `OnlineWebviewComponent` + 网页 UI（需公网托管）。
2. **暂停/跳转不是原生的** —— `robot.play_sound()` 没有 pause/seek，只有 cancel。所以暂停/跳转的实现是：取消当前播放 → 用 ffmpeg 从目标位置转码成临时 wav → 重新播放。**代价是 0.5~3 秒的响应延迟**（本地文件快，在线音频要重新拉流）。
3. **在线歌曲受平台版权限制** —— 付费/版权受限曲目解析不到直链，会跳过并提示；能播的通常是平台公开可播的曲目。
4. **依赖 ffmpeg** —— 没有 ffmpeg 时只能整首播放本地文件，暂停/跳转/在线播放会失败。
5. **首次播放有大文件转码等待** —— 在线直链会先下载并转码；长音频可能等几秒，期间状态栏显示 `…`。
6. **进度是估算** —— 基于墙钟时间推算，不做音频时钟同步，长曲可能有 1~2 秒误差。

---

## 九、排错

| 现象 | 检查 |
| --- | --- |
| 状态栏一直 `…` | 看日志有没有 `ffmpeg transcode failed`；确认机器人上有 `ffmpeg` |
| 点播放无反应 | 歌单是否为空（`共 0 首`）；本地文件是否放在 Agent 私有目录的 `music/` 下 |
| 在线 Tab 消失 | 网络探测失败（20~40 秒一次），检查机器人网络 |
| 搜索失败 | 机器人访问不到 `itunes.apple.com`，属于网络限制 |
| 无法播放：链接失效/文件损坏/格式不支持 | 这是 Toast 原文，按顺序排查 URL、文件完整性、扩展名 |
| 音量滑了没变化 | 确认没在静音状态；确认 `audio_manager.set_system_volume` 未抛异常（看日志） |

日志关键字：`MusicPlayerAgent`、`local scan`、`ffmpeg`、`set_system_volume`、`search failed`。

---

## 十、实现说明（给维护者）

```
music-player/
├── agent.toml               # id/入口/机型/快捷键声明
├── build.toml               # 平台与依赖
├── src/main.py              # 全部逻辑（约 1250 行，单文件）
└── res/
    ├── params/schema.json   # 参数页：音量/进度/模式/关键词/直链
    ├── logo.png
    ├── play.png  pause.png  prev.png  next.png
    ├── mode_order.png  mode_shuffle.png  mode_repeat.png
    ├── mute.png  scan.png  search.png  link.png
    ├── tab_local.png  tab_online.png  music_note.png
```

**线程模型**（框架要求回调必须快速返回）：

- 所有 UI 回调只改状态 + 起线程，不做 IO
- `_play_worker`：ffmpeg 转码 → `play_sound` → `wait()` 阻塞等待（在后台线程）
- `_ui_ticker`：每秒刷新状态栏与按钮高亮
- `_network_watcher`：每 20s 探测网络，断网自动回本地页
- 用 `_generation` 计数器作废过期的播放任务，避免暂停/切歌时的竞态

**播放链路**：

```
本地 offset=0        → robot.play_sound(原文件)          （原生解码，最快）
本地 offset>0 / 在线  → ffmpeg 转 PCM wav → play_sound   （支持任意位置起播）
```

---

## 十一、真机验证记录（K1 教育版 / 固件 1.8.1.2）

已在真实机器人上安装并验证，**当前安装版本 v1.0.3**。

### 安装方式（不依赖 App）

机器人上有官方安装客户端，走本地 socket 6868：

```bash
# 1) 上传安装包
scp com.wenzhoujike.k1.music-1.0.3.agent booster@<robot-ip>:/tmp/

# 2) 用官方客户端安装（同名包自动升级）
python3 /opt/booster/BoosterAgent/install_agent_client /tmp/com.wenzhoujike.k1.music-1.0.3.agent
# → Install success!  resp: {'success': True, 'result': {'agent': {...}}}
```

安装后文件落在：

```
/opt/booster/booster_agent_data/data/agents/installed/com.wenzhoujike.k1.music-1.0.3.agent
/opt/booster/booster_agent_data/data/agents/extract/com.wenzhoujike.k1.music/
/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.music/     ← 私有存储
```

### 切换 / 启停 Agent（不用 App）

管理器的协议支持 `get_agents` / `set_agent` / `start_agent` / `stop_agent` / `uninstall_agent`，
JSON + 4 字节长度前缀走 `127.0.0.1:6868`。见仓库内 `agent_client.py`。

> ⚠️ `set_agent` 对**已经在运行的同一个 Agent 是空操作**。要真正重启，必须
> `stop_agent` → 等 6 秒 → `start_agent`。

### 实测结果

| 验证项 | 结果 |
| --- | --- |
| 安装 / 升级（1.0.0 → 1.0.1 → 1.0.2 → 1.0.3） | ✅ 全部 `success: True` |
| Agent 启动 | ✅ `MusicPlayerAgent activated`，状态 `Active / running=True` |
| 16 个组件注册 | ✅ 本地页 8 个 + 在线页 8 个 |
| 页面切换（Tab） | ✅ `page switch -> page_local, component count=8` |
| 本地歌单扫描 | ✅ `local scan: 2 track(s)` |
| **系统音量接口** | ✅ 设置 80% → `pactl` 实测 80%；恢复 50% 同样生效 |
| **状态持久化** | ✅ 连续两次重启都保持在写入的 30%，不再被默认值覆盖 |
| ffmpeg 转码 | ✅ 4 分 53 秒 mp3 → PCM wav 仅 **1.6 秒** |
| 启动期异常 | ✅ 无 Traceback（`Loco operation service wait timeout` 是机器人未站立时的正常日志） |

### 真机上修掉的两个 bug（v1.0.1 / v1.0.2）

1. **页面不显示**（v1.0.0 → v1.0.1）
   现象：日志 `Next page ID not found in pages map: AutoGeneralDampingOrPVTInactive, processing as empty page`
   原因：旧的 `register_page()` 写法会被框架默认的机器人状态页面谓词覆盖。
   修复：改用 API 10800 的 `ComponentStatePageProxy(agent, page_predicate)` 构造方式，
   谓词直接返回当前页 id。

2. **重启后音量被重置成 50%**（v1.0.1 → v1.0.2）
   现象：`settings loaded: volume=80` 之后紧跟 `system volume -> 50%`
   原因：框架启动时会异步推送 schema 默认参数值，回调把刚读出来的设置覆盖了。
   修复：加 `_params_ready` 门闸 + `PARAM_SETTLE_SEC = 2s` 静默窗口，之后再以设置文件为准回写参数并应用。

### 还没验证的

- **实际出声播放**：需要 App 里点一次播放键（或无头注入手柄事件）。机器人没有安装
  `ros2` 命令行，无法从外部注入快捷键事件，因此这一步留给人工确认。
- 在线搜索（需要机器人访问 `itunes.apple.com`）。

### 放音乐的位置

```
/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.music/music/
```

放进去后点「刷新歌单」图标，或重启 Agent（启动会自动扫描）。

