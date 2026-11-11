# Booster K1 · 温州极克

加速进化 **Booster K1** 人形机器人的 Agent 集合，包含本地音乐播放器、在线音乐、系统音量控制和开发说明。

新版音乐播放器可在 Booster App 里浏览完整歌单、搜索并点歌、查看和拖动实时进度条。手机与 K1 接入同一局域网即可操作。

- 各 Agent 的源码、使用说明和已发布的安装包位于各自目录。
- 首次使用可从[快速开始](#快速开始)进入，开发者可查看 [Agent 目录约定](agents/README.md)。

> 官方文档：<https://docs.booster.tech/zh-CN/developer-guide/>

---

## Agent 一览

| Agent | 目录 | 功能 | 版本 | 手柄快捷键 |
| --- | --- | --- | --- | --- |
| 音量调节 | [`agents/volume-adjustment`](agents/volume-adjustment/README.md) | 读取 / 增大 / 减小 / 静音机器人系统音量 | 1.0.0 | `A` 音量+ ・ `B` 音量- ・ `Y` 静音 |
| 音乐播放器 | [`agents/music-player`](agents/music-player/README.md) | 本地 MP3 / WAV、完整歌单、按文件名搜索、进度跳转与系统音量 | 1.2.1（源码） | `A` 播放暂停 ・ `X` 上一首 ・ `B` 下一首 ・ `Y` 静音 ・ `LB+A` 切模式 |
| 在线音乐 | [`agents/online-music`](agents/online-music/README.md) | 网易云扫码登录、个人歌单和在线播放，QQ 公共搜索 | 0.1.2 | `A` 播放暂停 ・ `X` 上一首 ・ `B` 下一首 ・ `Y` 静音 ・ `LB+A` 切模式 |

## 音乐播放器怎么用

1. 手机与 K1 接入同一局域网。在 Booster App 左上角切换到 **音乐播放器**。
2. 在主控页点 **歌单与进度**。在搜索框输入歌名或文件名里的歌手名，也可以直接从完整歌单里点选歌曲。
3. 播放时可以查看已播时间和总时长，拖动进度条跳转，或者暂停后从原位置继续。面板也可以调节系统音量和静音。

支持顺序播放、随机播放和单曲循环。随机模式下手动点「下一首」也会随机，「上一首」会回到实际播过的歌曲。添加本地歌曲后可点 **刷新歌单** 重新扫描。

要从手机加歌，在「歌单与进度」的 **从手机添加 MP3** 里选择文件，再点 **上传** 或 **上传并播放**。单个文件最多 64 MB，同名文件会另存。

**版本说明** · `main` 提供 1.2.1 的源码和面板资源。仓库现有的音乐播放器 `.agent` 安装包是 1.2.0。通过安装包升级到新版时，需要重新构建包含 `resources/music_panel.zip` 的签名包。

---

## 在线音乐怎么用

1. 将[在线音乐 0.1.2 安装包](agents/online-music/com.wenzhoujike.k1.onlinemusic-0.1.2.agent)安装到 Jetson ARM64 K1，在 Booster App 左上角选择「在线音乐」。
2. 手机与机器人接入同一局域网，打开「在线点歌」。机器人还需要能够访问互联网。
3. 选择网易云音乐，扫码登录后搜索歌曲，或进入「我的歌单」点歌。

支持暂停续播、上一首和下一首、顺序播放、随机播放、单曲循环、进度跳转、系统音量和静音。QQ 音乐提供公共搜索，公开音频能否播放取决于平台返回的播放地址。网易云歌曲的播放权限取决于当前账号。

已在 Jetson ARM64 K1 上验证安装、网易云个人歌单、在线音频输出及播放控制。完整用法和构建说明见[在线音乐 Agent](agents/online-music/README.md)。

---

## 快速开始

### 第 1 步：准备环境

| 项目 | 要求 |
| --- | --- |
| 机器人固件 | **≥ 1.7.0**（Python Agent 的安装下限）；**≥ 1.8.0** 已预置 `boosteros` 库 |
| Booster Studio | 建议 **1.13.0 及以上**（对应 API Level `10800`） |
| Booster App | **1.7 及以上** |
| 机器人型号 | Booster K1（教育版 / 专业版 → `real_jetson`，**极客版 → `real_qcom`**） |

详细步骤见 **[docs/01-setup.md](docs/01-setup.md)**。

### 第 2 步：把 Agent 装到机器人

两种方式，二选一：

- **方式 A（推荐给开发者）**：用 Booster Studio 连接机器人后一键安装 / 调试
- **方式 B（推荐给使用者）**：选择仓库里已发布的 `.agent` 文件安装，安装包版本见文件名

详细步骤见 **[docs/02-install-agent.md](docs/02-install-agent.md)**。

### 第 3 步：在 App 里切换并操作

安装完成后，在 Booster App **左上角点击 Agent 名称**，选择「音乐播放器」、「在线音乐」或「音量调节」。音量调节提供音量加减和静音按钮，也支持表中的手柄快捷键。

---

## 目录结构

```
Booster-K1-Wenzhou-Jike/
├── README.md                       # 本文件：仓库总览 + 快速开始
├── CHANGELOG.md                    # 版本变更记录（与 git tag 对应）
├── docs/
│   ├── 01-setup.md                 # 环境准备：Studio、固件、连接机器人
│   ├── 02-install-agent.md         # 安装 Agent：一键安装 / .agent 分发
│   ├── 03-develop-agent.md         # 开发自己的 K1 Agent
│   ├── 04-faq.md                   # 常见问题排查
│   └── 05-versioning.md            # ⭐ 版本管理与回滚（装坏了怎么救）
├── scripts/
│   └── release.ps1                 # 一键发布：归档 .agent + 提交 + 打 tag
└── agents/
    ├── README.md                   # 新增 Agent 的目录约定与检查清单
    ├── music-player/               # 播放器源码、网页面板和安装包
    │   ├── src/main.py             # 歌单、播放和音量控制逻辑
    │   ├── web/                    # 歌单与进度面板源码
    │   └── resources/music_panel.zip  # 部署到 K1 的面板资源
    ├── online-music/               # 在线音乐源码、网页面板与已签名安装包
    └── volume-adjustment/          # 音量调节 Agent 源码和安装包
```

---

## 版本管理与回滚

每次改动都提交、每个能用的版本都打 tag —— 装坏了 30 秒能退回上一版。

| 内容 | 在哪 |
| --- | --- |
| 完整说明（含 git 入门、回滚步骤） | [docs/05-versioning.md](docs/05-versioning.md) |
| 变更记录 | [CHANGELOG.md](CHANGELOG.md) |
| 一键发布 | `.\scripts\release.ps1 -Agent music-player -Push` |
| 历史安装包 | [Releases](https://github.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/releases) |

> 一句话流程：改代码 → 构建 → **装真机验证** → `release.ps1` 打 tag → 推送。
> 出问题就删掉机器人上的 Agent，从 Releases 装回上一个 tag 的包。

---

## 什么是 Agent

Agent 是部署并运行在**机器人端**的功能程序包。它通过标准接口向 Booster App 声明可交互的组件（按钮、图标、状态、提示等），App 据此动态生成操作界面，把用户的点击下发给 Agent 执行。

从使用者角度看，切换 Agent 就像在手机上切换 App：换一个 Agent，机器人就换一套能力和交互方式。

开发层面，Agent 是继承 `booster_agent_framework.AgentBase` 的 Python 类，主要提供：

- **机器人控制与状态获取**：调用动作、切换模式、订阅状态
- **UI 组件管理**：`Component` 描述可展示 / 可交互内容，点击触发回调
- **输入处理**：声明手柄快捷键并绑定到组件
- **存储、配置与国际化**：文件读写、参数、多语言字符串与日志

---

## 官方文档索引

| 内容 | 链接 |
| --- | --- |
| 开发指南总览 | <https://docs.booster.tech/zh-CN/developer-guide/> |
| Agent 开发概述 | <https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/intro/> |
| 快速上手：5 步构建自定义 Agent | <https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/> |
| 配置文件与配置项（agent.toml / build.toml） | <https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/configuration-files-and-options/> |
| API 组成与使用（含 API Level、线程模型） | <https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/api/overview/> |
| K1 使用 Agents（切换方式、官方 Agent） | <https://docs.booster.tech/zh-CN/docs/product-manual/k1/basic-operations/agents/> |
| Agent Store | <https://agentstore.booster.tech/> |
| Booster Studio 下载 | <https://studio.booster.tech/cn/> |

---

## 来源与致谢

`agents/volume-adjustment` 的源码与安装包整理自仓库：

- <https://github.com/witchlayline/Booster-K1-volume-adjustment>

版权归原作者所有，本仓库仅作内部收集与使用说明补充。若需二次分发，请遵守原仓库的授权方式。
