# K1 在线音乐 Agent

在 Booster App 中搜索和点播网易云音乐、QQ 音乐，通过 K1 扬声器播放。当前版本为 `0.1.2`，包标识为 `com.wenzhoujike.k1.onlinemusic`，目标为 Jetson ARM64 K1。

网易云支持扫码登录、搜索分页、个人歌单、歌单点歌和播放队列。QQ 音乐提供公共搜索，并尝试播放平台公开提供的音频。播放器支持暂停续播、上一首、下一首、顺序播放、随机播放、单曲循环、进度跳转、系统音量和静音。播放进度读取 FFmpeg 实际解码输出。

## 使用

1. 下载[已签名安装包](https://raw.githubusercontent.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/main/agents/online-music/com.wenzhoujike.k1.onlinemusic-0.1.2.agent)，按[单包安装说明](../../docs/02-install-agent.md)安装到 Jetson ARM64 K1，切换到「在线音乐」。安装成品包无需下载源码、npm 或 Python wheels。
2. 手机与机器人连接同一局域网。打开「在线点歌」，选择音乐平台。
3. 选择网易云后点击「扫码登录」，使用网易云音乐 App 扫码。单手机操作时，可以保存二维码，再从相册识别。
4. 搜索歌名或歌手，点击歌曲开始播放。登录后可以打开「我的歌单」，选择歌单，再点选歌曲。

手柄 `A` 播放或暂停，`X` 上一首，`B` 下一首，`Y` 静音，`LB+A` 切换播放模式。切换到其他 Agent 时暂停在线音乐。

机器人需要能访问互联网。歌曲播放范围由平台和当前账号权限决定。平台接口失败、登录失效或缺少播放权限时，面板会显示错误并允许重试。网易云登录凭据保存在此 Agent 的私有存储目录，退出登录会清除本地网易云凭据。

## 包与验证范围

- 文件大小：`87492211` 字节（约 83.4 MiB）。SHA-256：`4d423d21a36e3324efb0285e3ec18a389f7e4aa057d636e37cae27700a663eb3`。
- 清单最低 / 构建 API Level 为 `10700` / `10800`，平台只声明 `real_jetson`。当前包不适用于 Qcom 或 x86 仿真，不能泛称所有 K1 通用。
- 原 Jetson K1 已验证网易云个人歌单、音频输出和播放控制；2026-11-12 另一台 Jetson K1 通过 SSH 安装器安装同一文件，启动、两平台搜索和网易云二维码检查通过。另一台上的登录后播放尚未验收。
- 另一台电脑经 Studio 单包导入的流程尚未复现，不能从 SSH 安装成功直接推断该流程已验证。
- QQ 登录及个人歌单入口已移除。公共搜索成功不代表返回的每首歌曲均可播放。

## 复用的项目

- QQ 音乐接入使用 `qqmusic-api-python==0.8.2`，遵循 GPL-3.0-or-later。[QQMusicApi](https://github.com/L-1124/QQMusicApi)
- 网易云接入使用 `@neteasecloudmusicapienhanced/api==4.41.1`，遵循 MIT。[NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)
- `src/local_player.py` 复用同仓库本地播放器的播放队列、设置、音量、快捷键及界面更新逻辑。
- Node.js 使用 ARM64 22.x 运行时。发布包保留依赖的许可文件。

本目录新增代码以 GPL-3.0-or-later 发布。

## 构建和检查

`scripts/build_release.py` 使用同仓库音乐播放器的 ROS 启动模板，加入独立包名、源码、网页资源、ARM64 Python wheels 和 Node.js 运行时，生成 `.unsigned.agent` 中间包。安装包携带平台接入所需的 Node、npm 和私有 Python 依赖，启动时无需再下载这些依赖；仍需要系统的 Booster SDK、FFmpeg 和 PulseAudio。下面是开发者重建流程，普通安装用户无需执行。

先在 `backend` 目录运行 `npm ci --ignore-scripts`。准备匹配 Python 3.10 / Linux ARM64 的 wheels，其中包含 Python 3.10 条件依赖 `exceptiongroup`。在 Python 3.11 或更新的电脑上交叉下载时，需要显式下载这个 wheel。再从 Node.js 官方获取 ARM64 22.x 压缩包及其 SHA-256 校验值，运行：

```powershell
python scripts/build_release.py --node-archive <node-linux-arm64.tar.xz> --node-sha256 <SHA256> --wheels <wheel目录>
```

使用 Booster 的 `signer` 为中间包签名，再验证签名，生成最终 `.agent` 安装包。签名密钥采用独立的 PKCS#12 文件，后续更新沿用同一密钥。

```bash
signer sign --ks <私有密钥.p12> --ks_type PKCS12 --ks_pass <密码> --input <中间包.unsigned.agent> --output com.wenzhoujike.k1.onlinemusic-0.1.2.agent
signer verify --input com.wenzhoujike.k1.onlinemusic-0.1.2.agent
```

密钥和密码保存在项目目录外，备份时一并保存。它们属于开发者私有文件。

本机安装 `requirements.txt` 和 FFmpeg 后，可运行针对实际音频流、暂停、切歌及请求并发的回归检查。检查使用模拟的 Booster SDK 和音源服务。

```powershell
python scripts/check_online.py
python scripts/check_online.py "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

已在真机验证网易云登录后的个人歌单读取、在线音频输出、暂停续播、进度跳转、音量和连续切歌。QQ 公共搜索已通过联网检查。
