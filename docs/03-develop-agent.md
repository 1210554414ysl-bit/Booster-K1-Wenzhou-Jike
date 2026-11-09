# 03 · 开发自己的 K1 Agent

> **目标**：从零写一个能在 K1 上跑的 Agent，并打包成 `.agent`。
> **官方章节**：[快速上手：5 步构建自定义 Agent](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/) ・ [配置与发布](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/configuration-files-and-options/) ・ [API 组成与使用](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/api/overview/)

---

## 1. 项目结构

在 Booster Studio 中基于模板创建工程，标准结构如下：

```
example_agent/
├── agent.toml          # 运行配置（ID、版本、名称、入口、依赖要求）
├── build.toml          # 构建配置（目标平台、Python 依赖、签名）
├── src/
│   └── main.py         # Agent 入口代码（组件、回调）
├── res/                # 资源文件（图标等）
└── build/              # 构建产物（自动生成）
    └── example-1.0.0.agent
```

> **不要手写 `build/` 目录**，它由 Studio 构建生成。

---

## 2. `agent.toml`：运行配置

### 2.1 基本信息

| 配置项 | 说明 |
| --- | --- |
| `id` | 唯一标识，建议反向域名，如 `com.example.my_agent` |
| `version` | 版本号，参与生成 `.agent` 包名，并在 App 里展示 |
| `name` | 显示名称，多语言，**必须含 `en`** |
| `logo` | 相对项目根目录的图标路径，**文件必须存在** |
| `description` | 简要介绍 |
| `entry` | 入口，格式 `文件路径:类名`，该类必须继承 `AgentBase` |
| `debuggable` | 可选，`true` 时调试模式启动会开 Python 调试服务 |
| `requirements.min_api_level` | 兼容的最低 API Level |
| `requirements.models` | 支持的机型，至少一个，如 `["Booster K1"]` |

最小可用示例：

```toml
id = "com.example.my_agent"
version = "1.0.0"
name = { en = "ExampleAgent", zh = "示例" }
logo = "res/logo.png"
description = "Example Python Agent"
entry = "src/main.py:ExampleAgent"

[requirements]
min_api_level = 10800
models = ["Booster K1"]
```

> ⚠️ `requirements.models` 的机型必须与当前连接的机器人在**同一 Ubuntu 版本族**：K1/T1 只能从 K1、T1 里选；T2/T2 Hand 只能从这两者里选。

### 2.2 手柄快捷键（可选）

先在 `agent.toml` 注册快捷键，再在代码里通过 `shortcut_id` 引用：

```toml
[component_shortcuts]
version = "1.0"

[[component_shortcuts.shortcut_list]]
id = "wave"
shortcut = ["A"]
locale_name = { en = "Wave", zh = "挥手" }
```

| 字段 | 含义 |
| --- | --- |
| `id` | 快捷键唯一标识，对应代码里的 `shortcut_id` |
| `shortcut` | 1~3 个按键的列表 |
| `locale_name` | 显示名称，必须含 `en` |

**按键限制**（容易踩坑）：

- ❌ 不可用系统保留键：`BACK`、`START`、`LS`、`RS`
- ❌ 当前不支持的键：`CENTER`、`LEFT_UP`、`LEFT_DOWN`、`RIGHT_UP`、`RIGHT_DOWN`
- 完整合法按键名见官方 [按键列表](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/api/joystick-and-shortcuts/basic-types/#joystickkey)

---

## 3. `build.toml`：构建配置

```toml
[platform]
supports = ["sim_x86_64", "sim_aarch64", "real_jetson", "real_qcom"]

[python]
pip_repos = ["https://pypi.org/simple"]
obfuscation = false

[python.dependencies]
common = ["numpy==1.21.5"]
```

| 配置项 | 说明 |
| --- | --- |
| `platform.supports` | 目标平台列表；省略 `[platform]` 时按当前构建环境自动选择 |
| `python.pip_repos` | pip 源；`dir:` 开头表示项目内本地 wheel 目录 |
| `python.obfuscation` | 是否混淆源码（只降低可读性，**不能真正防逆向**） |
| `python.dependencies.common` | 全平台共用依赖，标准 pip 语法 |
| `python.dependencies.<平台名>` | 仅该平台的依赖 |

> 🚩 **极客版注意**：`real_qcom` 是 **K1 极客版**专用平台。如果团队里既有教育/专业版（`real_jetson`）又有极客版，`supports` 里两个都要写，否则另一类机器装不上。

### 签名（可选）

Studio ≥ 1.13 支持 `[sign]` 节。不配置时使用通用 debug 证书，正式发布建议配置专属证书：

```toml
[sign]
keystore_path = "..."
keystore_pass = "..."
```

详见官方 [签名与证书](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/signing-and-certificates/)。

> ⚠️ 密钥库和密码**不要提交到仓库**，本仓库的 `.gitignore` 已忽略 `*.keystore` / `*.jks` / `*.pem` / `*.key` / `.env`。

---

## 4. 写入口类

入口类继承 `booster_agent_framework.AgentBase`，在 `__init__` 里初始化机器人客户端和 UI：

```python
from booster_agent_framework import AgentBase, AgentFeatures
from boosteros.robots.booster import BoosterRobot


class ExampleAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__(AgentFeatures(enable_auto_getup=True))
        self.robot = BoosterRobot()
        self.setup_components()

    def setup_components(self) -> None:
        ...
```

**生命周期回调**：

| 回调 | 时机 | 典型用途 |
| --- | --- | --- |
| `on_agent_activated()` | Agent 被激活 | 读取初始状态、刷新 UI |
| `on_agent_close()` | Agent 正在关闭 | 保存状态、释放资源 |

```python
def on_agent_activated(self) -> None:
    self.logger.info("activated")

def on_agent_close(self) -> None:
    self.logger.info("closing")
```

---

## 5. 组件（Component）与回调

组件是「App 上能看到的按钮」，也是「点击事件的入口」。

```python
from booster_agent_framework import Component, LocaleString

def on_wave_click(self, component: Component) -> LocaleString | None:
    self.robot.do_action("hand_wave")
    return LocaleString("Waving started", "开始挥手")
```

- 回调返回值是 `LocaleString` → App 以 **Toast** 弹出
- 返回 `None` → 不提示

常用组件类型：

| 类型 | 用途 |
| --- | --- |
| `IconComponent` | 普通图标按钮 |
| `DefaultStateIconComponent` | 带开/关状态的图标按钮（如静音开关） |
| `Component` | 基类 |

注册组件：

```python
from booster_agent_framework import DefaultStateIconComponent, LocaleString

wave_btn = DefaultStateIconComponent(
    "wave_btn",                       # 组件 ID
    LocaleString("Wave", "挥手"),      # 显示名称（也可传 {"en": ..., "zh": ...}）
    "res/wave.png",                   # 图标
    False,                            # 初始状态
    self.on_wave_click,               # 点击回调
    shortcut_id="wave",               # 关联 agent.toml 里的快捷键 id
    display_sequence=0,               # 排序
)
self.component_manager.add_component(wave_btn)
```

组件管理 API：

```python
self.component_manager.get_component("wave_btn")     # 取组件
self.component_manager.update_component(wave_btn)    # 更新组件（改文案/状态后必须调用才会同步到 App）
```

---

## 6. 存储、日志、多语言

```python
from pathlib import Path

STATE_FILE = Path("volume_state.txt")   # 相对路径，落在 Agent 自己的存储目录

# 读
if self.storage_manager.file_exists(STATE_FILE):
    text = self.storage_manager.read_text_file(STATE_FILE)

# 写
self.storage_manager.write_text_file(STATE_FILE, "0.50")
```

- **日志**：`self.logger.info(...)` / `self.logger.warn(...)` / `self.logger.error(...)`
- **多语言**：`LocaleString({"en": "...", "zh": "..."})`，或 `LocaleString("English", "中文")`

> 存储路径必须是**相对路径**，不要写绝对路径；绝对路径在机器人上通常没有写权限。

---

## 7. 线程模型（最容易翻车的地方）

框架的回调模型类似 Android：**所有用户回调都在主线程串行执行**，框架会等待需要返回值的回调返回。

所以：

- ❌ 不要在回调里做长耗时 IO、`sleep`、`join`、`future wait`
- ❌ 不要在回调里等待**另一个 Python 回调**或后续 App 操作
- ✅ 耗时任务丢到你自己的后台线程 / 进程 / 外部服务
- ✅ 回调只做参数校验、状态记录、任务启动，然后**快速返回**
- ✅ 必须同步等待时，给明确的**短超时**，并确认不会死锁

违反这条的典型表现：App 界面卡住、后续按钮点了没反应。

---

## 8. API Level 兼容

```python
from booster_agent_framework import get_sys_api_level

if get_sys_api_level() >= 10800:
    # 使用 10800 才有的接口
    ...
else:
    # 降级方案
    ...
```

- `min_api_level` 填**兼容的最低**版本，不要填当前开发环境的版本
- 在 Studio 中按住 **Ctrl + 单击**任意 Agent API，可查看它的 `since` / `deprecated` 信息

---

## 9. 构建与命名约定

### 构建

在 Booster Studio 中构建，产物位于 `build/<id>-<version>.agent`，文件名为 `<包名>-<版本号>.agent`。

### 本仓库的目录与命名约定

新增 Agent 放到 `agents/<kebab-case-名字>/`，并遵守 [agents/README.md](../agents/README.md) 的检查清单。

建议包 id 统一为：

```
com.<组织>.k1.<功能>
例如：com.wenzhoujike.k1.volume
```

> 现有 `agents/volume-adjustment` 的 id 是 `com.example.volumeagent`（原作者的），**保留原样不做改动**，避免破坏已分发出去的包。新写的 Agent 按上面的约定来。

---

## 10. 最小完整示例

```python
# src/main.py
from booster_agent_framework import (
    AgentBase,
    AgentFeatures,
    Component,
    IconComponent,
    LocaleString,
)
from boosteros.robots.booster import BoosterRobot


class HelloAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__(AgentFeatures())
        self.robot = BoosterRobot()
        self.setup_components()

    def setup_components(self) -> None:
        btn = IconComponent(
            "wave_btn",
            LocaleString({"en": "Wave", "zh": "挥手"}),
            "res/wave.png",
            self.on_wave_click,
            shortcut_id="wave",
            display_sequence=0,
        )
        self.component_manager.add_component(btn)

    def on_wave_click(self, component: Component) -> LocaleString | None:
        self.robot.do_action("hand_wave")
        return LocaleString({"en": "Waving", "zh": "开始挥手"})
```

配套 `agent.toml` 见 [第 2 节](#2-agenttoml运行配置)。

---

## 参考实现

- 本仓库：[`agents/volume-adjustment/src/main.py`](../agents/volume-adjustment/src/main.py) —— 一个完整的音量控制 Agent，展示了组件注册、状态刷新、异常回退、持久化
- 官方示例：[RoboCup Demo](https://docs.booster.tech/zh-CN/docs/developer-guide/open-source/robocup-demo/)
