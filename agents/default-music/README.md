# 默认模式＋音乐 · Booster + Music

独立的 Booster K1 Agent，在保留已安装默认模式实现的基础上增加本地和在线音乐。当前版本 **0.1.0**，包 ID 为 `com.wenzhoujike.k1.defaultmusic`，App 显示名称为「默认模式＋音乐」。

## 原默认模式如何保留

- `com.boosterobotics.default` 继续保留，不修改、覆盖或重新打包它的文件。
- 新 Agent 只读加载机器人中已安装的官方 `DefaultAgent`，继承其逻辑和框架功能，并保留清单中的 13 个技能引用。准备、行走和动作的具体可用状态仍由原实现、系统技能与机器人状态决定。
- 音乐控制器是同一 Agent 内的普通 Python 对象，不再创建第二个 ROS Agent。它只增加网页入口，不占用默认动作的手柄快捷键。
- 原实现会随机器人状态重建组件，新入口在重建后保留。退出新 Agent 时暂停音乐，原默认 Agent 可从 App 列表切回。
- 只读导入期间关闭字节码写入，避免在原目录生成新缓存。部署前后已对原默认目录的完整文件集合和 SHA-256 做比对，结果一致。

## 兼容范围

当前包仅声明支持 **Booster K1 / `real_jetson` / Agent API 10800**，依赖 FFmpeg、FFprobe、PulseAudio 和 Booster SDK。

这是针对已检查默认实现的扩展，并非所有 K1 固件通用。`res/default_contract.json` 记录原默认 Agent 1.0.0 的五个 Python 文件校验值和技能引用；运行时会核对它们。系统升级后即使默认 Agent 仍显示 1.0.0，代码发生变化也需要重新适配。校验不匹配时新 Agent 会拒绝启动，原默认模式仍保留。

当前包不会包含官方默认 Agent 的源码或安装包，也不能脱离已安装的原默认 Agent 单独运行。

## 安装与使用

1. [下载签名安装包](https://raw.githubusercontent.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/main/agents/default-music/com.wenzhoujike.k1.defaultmusic-0.1.0.agent)。按[单包安装步骤](../../docs/02-install-agent.md)传输并调用机器人安装器，将示例文件名替换为本包名称。
2. 在 Booster App 的 Agent 列表中选择「默认模式＋音乐」。
3. 打开「本地与在线音乐」，切换「本地音乐」或「在线音乐」。两页共用播放状态、音量和播放引擎；浏览另一页不会自动换歌，点选歌曲才会切换音源。
4. 点击「返回控制界面」继续使用基础操作。仅关闭网页不会停止音乐；切换到其他 Agent 时暂停音乐。

安装前无需卸载原默认模式。实际运动与动作验收应在机器人站稳且有人照看时手动完成，本项目自动检查不触发行走、握手或起身指令。

## 本地音乐

支持 MP3 / WAV、完整歌单、按文件名搜索、暂停续播、切歌、进度跳转、三种播放模式、系统音量和静音。

新上传 MP3 写入新 Agent 自己的 `music/` 目录。上传上限为 64 MiB，同名文件另存。当前已验证系统的路径为：

```text
/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.defaultmusic/music/
```

歌单还会只读扫描现有本地播放器的歌曲目录：

```text
/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.music/music/
```

不会搬动或覆盖原歌曲，也不会读取原播放器的设置。换机器人时安装包不会携带你的歌曲；不存在原歌曲目录时可在新页面上传。

## 在线音乐

复用仓库在线 Agent 的平台接入。网易云提供扫码登录、搜索、个人歌单及账号有权限播放的歌曲；QQ 仅提供公共搜索，公开音频能否播放取决于平台。

账号和设置保存在新 Agent 的私有目录，需在新页面重新登录；不会复制其他 Agent 的 Cookie。机器人需要互联网，手机需要能访问面板的局域网地址。平台服务故障会影响搜索或播放，不能保证任意曲目可播。

## 构建与签名

本目录使用自定义打包脚本，不提供直接 Studio 构建的 TOML 工程。

```bash
python scripts/build_release.py
```

脚本核对同仓库 `online-music` 0.1.2 已发布包的 SHA-256，复用其 ROS 启动器、ARM64 Node 和私有 Python 依赖，将本目录源码、运行清单、网页打入新的 `.unsigned.agent`。然后用 Booster `signer`、开发者私有证书完成签名并验证；私钥与密码不在仓库中。

安装必须使用已签名的 `.agent`，不能使用 `.unsigned.agent`。升级新 Agent 须沿用其现有签名证书。

安装包大小为 `87503792` 字节，SHA-256：

```text
a41760f65e5ac4820a52c7c1d392d876636e250657d9099f1ed04d565545ad18
```

## 检查记录

2026-11-12 完成：

- 模拟默认宿主下的组件重建保留入口、真实 FFmpeg 本地 / 网络流播放、暂停续播、跳转、音源切换、队列隔离、连续切歌、音量和慢搜索并发检查。
- Chrome 中的在线搜索与歌单模拟流程、登录控件、MP3 上传并播放、无效文件与路径拒绝、同名另存、两个内嵌页面切换和返回桥接检查。
- 签名验证、独立安装与启动；真机读到原歌曲目录中的 216 首歌曲。
- 真机本地播放进度、暂停续播、跳转以及 PulseAudio 播放流检查；网易云搜索、QQ 公共搜索通过，QQ 一次请求失败后重试成功。
- 部署和播放检查后，原默认 Agent 全部文件校验保持一致。用户确认新界面可使用。

尚未完成：新 Agent 登录后的网易云真实在线播放与个人歌单、手机内所有交互组合、默认运动 / 动作的现场验收、长时间运行和其他固件兼容性。模拟平台返回不能代替实际音乐账号权限验证。

在仓库中运行回归检查需要 FFmpeg、Node、`online-music/requirements.txt` 的依赖，以及可选 Chrome；测试复用同仓库在线 Agent 的测试辅助代码。

```powershell
python scripts/check_integration.py
python scripts/check_integration.py "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

## 来源与许可

音乐引擎和网页复用本仓库两个音乐 Agent，平台依赖和许可随包保留。本目录代码按 GPL-3.0-or-later 发布，见 [LICENSE](LICENSE)。官方默认实现和技能属于原厂系统依赖，未随此包分发，其授权不由本项目改变。
