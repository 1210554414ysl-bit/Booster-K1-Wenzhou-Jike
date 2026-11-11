# 音乐播放器 · Music Player

Booster K1 本地音乐 Agent，当前发布版本 **1.2.1**。在 Booster App 中播放机器人里的 MP3 / WAV，浏览完整歌单、搜索点歌，并从手机上传 MP3。

## 安装与使用

1. 下载 [1.2.1 签名安装包](com.wenzhoujike.k1.music-1.2.1.agent)，通过 Booster Studio 安装到 K1。
2. 在 Booster App 左上角选择「音乐播放器」。手机与机器人须在同一局域网。
3. 点击「歌单与进度」，从完整歌单点选歌曲，也可以输入歌名或文件名里的歌手名搜索。
4. 播放时显示已播时间和总时长；可暂停续播、拖动进度条、切歌、调整系统音量和静音。

顺序播放到歌单末尾停止；手动切歌可以循环到首尾。随机模式使用打乱的歌曲队列，「上一首」返回实际播放历史。单曲循环模式在当前歌曲结束后重播。

### 从手机添加歌曲

在「歌单与进度」的「从手机添加 MP3」区域选择文件，点击「上传」或「上传并播放」。

- 单个 MP3 最多 **64 MiB**，显示上传进度；上传后自动刷新歌单。
- 校验实际音频格式，拒绝无效 MP3 和包含目录的文件名。
- 同名歌曲另存为 `歌名 (2).mp3` 等名称，已有歌曲保留。
- WAV 可以通过文件传输放入音乐目录；手机上传入口目前仅接受 MP3。

### 歌曲存放位置

歌曲位于 Agent 私有存储目录下的 `music/`，不打包在 GitHub 安装包内。当前机器人的路径为：

```text
/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.music/music/
```

其他系统版本以日志中 `local scan: N track(s) under ...` 输出的存储根目录为准。首次启动会创建目录，手机上传也写入这里。通过文件传输加歌后，在面板点「刷新歌单」。扫描支持子目录，最大深度为 4。

## 主控按钮和快捷键

| 功能 | 手柄 |
| --- | --- |
| 播放 / 暂停 / 继续 | `A` |
| 上一首 | `X` |
| 下一首 | `B` |
| 静音 / 取消静音 | `Y` |
| 顺序 / 随机 / 单曲循环 | `LB + A` |

主控页另有音量加减、刷新歌单和「歌单与进度」入口。音量按钮每次调整 10%，网页面板提供音量滑块。参数页也支持 `volume`、`mute`、`play_mode`、`seek_percent`。音量、静音与播放模式会保存，重启后恢复。

## 环境与限制

- Booster K1，最低 Agent API Level `10700`，本包由 API `10800` SDK 构建；建议固件 1.8.0 及以上并提供 `boosteros`。
- 机器人需要 `ffmpeg`、`ffprobe` 和可用的 PulseAudio 输出。播放进程由单个后台线程管理，切歌和暂停不会在按钮回调中等待音频进程。
- 手机须能访问机器人的局域网地址；机器人 IP 改变后重新打开 Agent。页面地址包含临时访问令牌，不要转发给无关设备。
- 本 Agent 播放本地文件，在线播放使用独立的 [在线音乐 Agent](../online-music/README.md)。
- 安装包保留 ARM64 / x86_64 启动器，声明支持 Jetson、Qcom 和两种仿真平台；Qcom 真机仍待验证。
- 此次发布完成官方构建、签名和包内容校验，以及模拟 SDK 下的真实 FFmpeg、浏览器上传回归。机器人关机，尚未对这次重打包进行新的真机安装验收。

## 构建与资源

修改网页后先执行：

```bash
python scripts/build_panel.py
```

随后在 Booster Studio 打开本目录并构建。脚本把 `web/` 下的 HTML、CSS、JS 生成到 `res/music_panel.zip`，官方构建工具会将 `res/` 收入安装包。旧的 `resources/music_panel.zip` 路径不会被官方工具自动收录，1.2.1 已同步修正读取路径。

签名配置使用 Studio 的既有配置。当前发布包沿用 1.2.0 的 SDK 调试证书，证书 SHA-256 为 `836323d5f9da105c344c30a6375a65bd4c66ea2d4c1cff802d75c2a4b127129f`。私钥和密码不随仓库发布。

安装包 SHA-256：

```text
3f5b16de743902f1c52112826e888609eded74a275a141c3ca4afe051ece9523
```

源码入口为 `src/main.py:MusicPlayerAgent`，配置在 `agent.toml` / `build.toml`，快捷键在 `shortcut_list.json`。

## 排查

| 现象 | 检查 |
| --- | --- |
| 只看到旧按钮，没有歌单入口 | 确认安装版本为 1.2.1，并重新进入 Agent |
| 网页提示读取不到机器人数据 | 检查局域网连接、机器人是否开机，重新打开「歌单与进度」 |
| 歌单为空 | 先上传 MP3，或检查歌曲目录后刷新歌单 |
| 播放失败 / 没有声音 | 查看 `playback failed` 日志，检查 FFmpeg、PulseAudio、系统音量和静音 |
| 上传失败 | 检查文件是否为有效 MP3、大小是否超限、连接与机器人存储空间 |

## 来源

本仓库的 K1 本地音乐播放器工程，使用 Booster Agent Framework、BoosterOS SDK 和 FFmpeg。框架与平台说明见 [Booster 官方开发文档](https://docs.booster.tech/zh-CN/developer-guide/)。
