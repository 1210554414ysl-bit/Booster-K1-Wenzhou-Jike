# agents/ · Agent 目录约定

本目录放所有 K1 Agent，**一个 Agent 一个子目录**。

---

## 必需结构

```
agents/<agent-name>/
├── README.md                      # 必须：做什么、怎么用、快捷键、已知限制
├── src/
│   └── main.py                    # 必须：入口类（继承 AgentBase）
├── agent.json                     # 建议：从 .agent 包提取的运行清单，便于快速查配置
├── shortcut_list.json             # 可选：从包内提取的快捷键清单
└── <id>-<version>.agent           # 必须：可直接安装的构建产物
```

命名规则：

- 目录名用 **kebab-case**，如 `volume-adjustment`、`ball-tracking`
- 包 id 建议 `com.<组织>.k1.<功能>`，如 `com.wenzhoujike.k1.volume`
- 构建产物文件名遵循 `<id>-<version>.agent`

> 💡 目录里**不需要**放 `build/` 中间产物，只保留最终的 `.agent`。

---

## 新增一个 Agent 的流程

### 1. 在 Studio 里创建并调通

按官方 [5 步快速上手](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/) 创建工程，先在**虚拟机器人**上跑通界面和逻辑，再上真机。

开发要点见本仓库 [docs/03-develop-agent.md](../docs/03-develop-agent.md)。

### 2. 构建出 `.agent`

标准 Studio 工程的产物路径为 `build/<id>-<version>.agent`。本地音乐需先生成网页资源；在线音乐使用自己的打包和签名脚本。先读对应 Agent 的构建说明。

### 3. 拷进本仓库

```bash
mkdir -p agents/<agent-name>/src
cp <你的工程>/src/main.py        agents/<agent-name>/src/main.py
cp <你的工程>/build/*.agent      agents/<agent-name>/
# 可选：把包内清单也提出来，方便查
```

### 4. 写 `README.md`

至少包含：**功能、界面/按钮、快捷键、安装方式、参数、已知限制、来源**。

可以直接拷贝 [`volume-adjustment/README.md`](volume-adjustment/README.md) 当模板。

### 5. 更新根目录清单

在 [根 README 的「Agent 一览」表格](../README.md#agent-一览) 里加一行。

---

## 提交前检查清单

- [ ] `main.py` 里的 `shortcut_id` 与快捷键配置一致
- [ ] 没有提交密钥库 / 证书 / `.env`（`.gitignore` 已覆盖，但请自查）
- [ ] `.agent` 包能装上并在目标机型跑起来
- [ ] `README.md` 写清了快捷键和已知限制
- [ ] 根 README 的「Agent 一览」表已更新
- [ ] 行为变更已更新版本，版本号、包内源码和资源一致；仅文档变更无需重发安装包
- [ ] 分别记录平台声明与实际验收范围，文档中的安装步骤有验证依据

---

## 当前收录

| 目录 | 功能 | 版本 |
| --- | --- | --- |
| [`volume-adjustment`](volume-adjustment/README.md) | 机器人系统音量读取 / 增减 / 静音 | 1.0.0 |
| [`music-player`](music-player/README.md) | 本地歌单、搜索点歌、实时进度、音量与手机 MP3 上传 | 1.2.1 |
| [`online-music`](online-music/README.md) | 网易云登录、歌单与在线播放，QQ 公共搜索 | 0.1.2 |
| [`default-music`](default-music/README.md) | 只读复用已安装默认实现，附加本地／在线音乐 | 0.1.0 |
