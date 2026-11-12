# 01 · 环境准备与兼容性

安装成品 `.agent` 与从源码构建的准备工作不同。只安装成品包可使用系统自带的 SSH / SCP；从源码开发还需要 Booster Studio 及相应构建环境。

## 当前包的范围

| Agent | 版本 | 包内最低 / 构建 API Level | 包内平台声明 | 验证范围 |
| --- | --- | --- | --- | --- |
| 本地音乐播放器 | 1.2.1 | 10700 / 10800 | `sim_x86_64`、`sim_aarch64`、`real_jetson`、`real_qcom` | 构建、签名、资源及模拟播放和上传回归；本次重打包的真机覆盖安装待验收 |
| 在线音乐 | 0.1.2 | 10700 / 10800 | `real_jetson` | Jetson 真机播放控制；2026-11-12 另一台 Jetson K1 安装、启动、搜索和二维码检查 |
| 音量调节 | 1.0.0 | 10700 / 10800 | `sim_x86_64`、`sim_aarch64`、`real_jetson`、`real_qcom` | 原项目整理包；各环境的音频服务能力需实测 |

平台声明表示包允许的目标，不代表所有平台都完成真机验收。在线音乐 0.1.2 不支持 Qcom 或 x86 仿真，不能只改平台声明就当作兼容。

## 固件、SDK 与 API Level

官方快速上手给出的 Python Agent 安装下限是固件 1.7.0；1.8.0 及以上预置 `boosteros`。本仓库音乐包使用 API 10800 的接口，优先使用已配套该 API 和音频服务的系统。包中最低 API 声明为 10700，不等于已验证 1.7.0 的全部功能。

固件版本、Studio 版本和 Agent API Level 是不同信息，不能靠一个版本号推算全部兼容性。安装前记录 App / Studio 中的固件版本，并核对包内 `agent.json` 的 `requirements`；新接口的支持范围查对应 SDK 文档。

## 网络和运行环境

- 电脑要能连接机器人的 SSH 地址，手机要能访问机器人的网页面板地址。网线连接电脑不代表手机也能访问该地址。
- 本地音乐的网页和 MP3 上传使用局域网，播放已保存歌曲无需音乐平台账号或公网。
- 在线音乐需要机器人能访问音乐平台；新机器人需要自行登录网易云。
- 音乐播放依赖机器人上的 FFmpeg 和 PulseAudio；本地 MP3 上传还需 FFprobe。
- 需要手柄操作时，连接手柄并使用各 Agent 说明中的快捷键。

## 开发环境

从 [Booster Studio 官网](https://studio.booster.tech/cn/) 安装 Studio。构建要求以当前 SDK 为准：本地音乐先生成网页资源再构建；在线音乐使用独立的依赖准备、打包和签名流程，见各自 README。

[官方快速上手](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/) · [配置说明](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/configuration-files-and-options/)

下一步见 [安装单个 Agent](02-install-agent.md)。
