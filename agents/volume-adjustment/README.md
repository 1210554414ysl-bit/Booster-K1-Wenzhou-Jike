# 音量调节 · Volume Agent

在 Booster App 里直接**读取、增大、减小、静音** K1 的系统输出音量。

| 项 | 值 |
| --- | --- |
| Agent 包 | `com.example.volumeagent-1.0.0.agent` |
| 包 ID / 版本 | `com.example.volumeagent` / `1.0.0` |
| 显示名称 | 音量调节（Volume Agent） |
| 入口 | `src/main.py:VolumeAgent` |
| 适用机型 | Booster K1 |
| 支持平台 | `sim_x86_64`、`sim_aarch64`、`real_jetson`、`real_qcom` |
| 最低 API Level | `10700`（构建 API Level `10800`） |
| 兼容性说明 | 最低 API 声明不是所有固件与平台均已验收的保证，见[环境说明](../../docs/01-setup.md) |

---

## 怎么装

1. 把 `com.example.volumeagent-1.0.0.agent` 装到机器人（见 [docs/02-install-agent.md](../../docs/02-install-agent.md)）
2. 手机 App 连接机器人 → 点击**左上角 Agent 名称** → 选「音量调节」

> 前置条件：固件 ≥ 1.7.0；如果是 1.7.0 固件，需先 SSH 登录机器人执行 `pip install boosteros`。

---

## 界面与操作

进入 Agent 后，App 上会出现 4 个按钮：

| 按钮 | 作用 | 手柄快捷键 |
| --- | --- | --- |
| 音量状态（如「音量 50%」） | 点击查看当前音量，弹 Toast 提示 | — |
| 音量 + | 音量增加一档（+10%） | `A` |
| 音量 - | 音量减小一档（−10%） | `B` |
| 静音 | 开/关静音；开启时按钮显示为激活态 | `Y` |

快捷键可用 Booster 手柄或 XBOX 手柄（`A`/`B`/`Y` 为通用面键）。

### 行为细节

- 音量取值范围 `0.0 ~ 1.0`，每次点击步进 **0.10**
- 初始默认音量 **50%**
- **静音逻辑**：静音时记录「最后一个非零音量」，取消静音时恢复到该值（不是简单的开关 0/1）
- **状态持久化**：最后一次非零音量写入 Agent 存储目录下的 `volume_state.txt`，重启后仍可恢复
- **异常处理**：读写音量的接口异常会被捕获并记录；读取失败时可显示缓存值，设置失败时会提示。启动阶段的 SDK 初始化等其他异常不在此保证范围内。

---

## 参数（想改的话）

都在 `src/main.py` 顶部，改完重新构建即可：

| 常量 | 默认值 | 含义 |
| --- | --- | --- |
| `VOLUME_STEP` | `0.10` | 每次点击的音量步进 |
| `DEFAULT_VOLUME` | `0.50` | 默认音量（50%） |
| `MUTE_EPSILON` | `0.001` | 判定为「静音」的阈值 |
| `STATE_FILE` | `volume_state.txt` | 状态文件名（相对 Agent 存储目录） |

> ⚠️ `STATE_FILE` 必须用**相对路径**，不能改成绝对路径。

---

## 实现要点

调用的机器人接口：

```python
self.robot.audio_manager.get_system_volume()          # -> float [0.0, 1.0]
self.robot.audio_manager.set_system_volume(volume)    # 写入
```

代码结构（`src/main.py`，约 320 行）：

| 部分 | 说明 |
| --- | --- |
| `VolumeAgent.__init__` | 初始化 `BoosterRobot`、加载持久化状态、注册组件 |
| `on_agent_activated` / `on_agent_close` | 激活时刷新 UI；关闭时保存状态 |
| `setup_components` | 注册 4 个组件（状态、+、−、静音） |
| `on_volume_*_click` | 4 个点击回调，返回 Toast 文案 |
| `_read_volume` / `_apply_volume` | 读写音量，异常时返回 `None` 并记录日志 |
| `_refresh_ui` | 同步状态按钮文案与静音按钮状态 |
| `_load_state` / `_save_state` | 基于 `storage_manager` 的持久化 |

几个值得借鉴的写法：

1. **回调完成音量读写并返回提示** —— 实际响应时间取决于系统音频服务，应在目标机器人检查
2. **读写音量都包了 try/except** —— 音频子系统异常时降级到缓存值，不让 UI 崩掉
3. **改完组件必须调 `update_component`** —— 否则 App 上不会刷新
4. **中英双语 `LocaleString`** —— 文案统一 `{"en": ..., "zh": ...}`

---

## 已知限制

| 限制 | 说明 |
| --- | --- |
| 依赖音频子系统 | 音频服务异常时只能显示缓存值，无法真正调节 |
| 虚拟机器人上可能不可用 | `audio_manager` 的音量接口在仿真环境里不一定实现，真机为准 |
| 无音量滑块 | 当前只做按钮步进（±10%），没有连续拖动调节 |
| 无独立流音量调节 | 本 Agent 只修改全局系统音量，不提供各音频流的独立控制 |

---

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `src/main.py` | 源码（原始工程中位于 `src/main.py`） |
| `com.example.volumeagent-1.0.0.agent` | 构建产物，可直接安装 |
| `agent.json` | 从 `.agent` 包内提取的运行清单（包 ID、入口、平台、机型要求） |
| `shortcut_list.json` | 从包内提取的手柄快捷键定义（`A` / `B` / `Y`） |

> 此目录整理了源码与成品包，未附完整 `agent.toml` / `build.toml` 工程。配置以当前包内清单为准；重建前需取得原工程或按 SDK 模板补齐配置，不能假定直接打开目录即可构建。

---

## 来源

源码与安装包整理自：

- <https://github.com/witchlayline/Booster-K1-volume-adjustment>

版权归原作者所有。本仓库只做收集与使用说明补充，未修改功能代码。
