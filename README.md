# Booster K1 · 温州极克

加速进化 **Booster K1** 人形机器人的 Agent 集合仓库。

本仓库用于长期收集、维护我们在 K1 上使用的各类 Agent（机器人端的「技能包」），并沉淀一份可以复用的环境准备、安装、开发与排错说明。

- 每个 Agent 一个目录，**源码 + 可直接安装的 `.agent` 包**放在一起
- 附带从零开始的使用说明，新手按 [快速开始](#快速开始) 走即可
- 新 Agent 按统一约定加入，见 [agents/README.md](agents/README.md)

> 官方文档：<https://docs.booster.tech/zh-CN/developer-guide/>

---

## Agent 一览

| Agent | 目录 | 功能 | 版本 | 手柄快捷键 |
| --- | --- | --- | --- | --- |
| 音量调节 | [`agents/volume-adjustment`](agents/volume-adjustment/README.md) | 读取 / 增大 / 减小 / 静音机器人系统音量 | 1.0.0 | `A` 音量+ ・ `B` 音量- ・ `Y` 静音 |
| 音乐播放器 | [`agents/music-player`](agents/music-player/README.md) | 在线音频直链 / 本地歌单，三种播放模式，进度跳转与系统音量 | 1.0.0 | `A` 播放暂停 ・ `X` 上一首 ・ `B` 下一首 ・ `Y` 静音 ・ `LB+A` 切模式 |

> 后续新增的 Agent 会继续加到这张表和 `agents/` 目录下。

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
- **方式 B（推荐给使用者）**：直接拿仓库里打好的 `.agent` 文件分发给别人安装

详细步骤见 **[docs/02-install-agent.md](docs/02-install-agent.md)**。

### 第 3 步：在 App 里切换并操作

安装完成后，在 Booster App **左上角点击 Agent 名称** → 选择「音量调节」即可进入。
界面上会出现 4 个按钮，也可以直接用手柄快捷键操作。

---

## 目录结构

```
Booster-K1-Wenzhou-Jike/
├── README.md                       # 本文件：仓库总览 + 快速开始
├── docs/
│   ├── 01-setup.md                 # 环境准备：Studio、固件、连接机器人
│   ├── 02-install-agent.md         # 安装 Agent：一键安装 / .agent 分发
│   ├── 03-develop-agent.md         # 开发自己的 K1 Agent
│   └── 04-faq.md                   # 常见问题排查
├── agents/
│   ├── README.md                   # 新增 Agent 的目录约定与检查清单
│   └── volume-adjustment/          # 音量调节 Agent
│       ├── README.md               # 该 Agent 的功能与使用说明
│       ├── src/main.py             # 源码（AgentBase 子类）
│       ├── agent.json              # 从 .agent 包提取的运行清单（参考）
│       ├── shortcut_list.json      # 从 .agent 包提取的快捷键清单（参考）
│       └── com.example.volumeagent-1.0.0.agent   # 可直接安装的 Agent 包
└── assets/
    └── booster-studio.png          # Booster Studio 账号页截图
```

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
