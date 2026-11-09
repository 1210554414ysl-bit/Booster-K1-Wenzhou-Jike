# 04 · 常见问题排查

按「现象 → 原因 → 处理」查。没解决的话，最后有获取日志和支持的方式。

---

## 安装 / 启动

### 安装 Agent 直接失败

**原因**：固件低于 1.7.0，Python Agent 不支持。

**处理**：升级固件到 1.7.0 以上。查看当前版本：[K1 产品手册 → 查看版本](https://docs.booster.tech/zh-CN/docs/product-manual/k1/firmware-version/check-version/)。

---

### 装上了，但一启动就退出 / 报 `No module named 'boosteros'`

**原因**：1.7.0 固件**没有内置** `boosteros`，而 K1 上的 Agent 基本都要用 `BoosterRobot`。

**处理**：SSH 登录机器人后执行：

```bash
pip install boosteros
```

固件 ≥ 1.8.0 已预置，不会出现这个问题。

---

### 同一份包，教育版能装、极客版装不上（或反过来）

**原因**：两类机器是**不同平台**：教育版 / 专业版是 `real_jetson`，**极客版是 `real_qcom`**。

**处理**：构建时 `build.toml` 的 `platform.supports` 两个平台都要包含，分别出包。

```toml
[platform]
supports = ["real_jetson", "real_qcom"]
```

---

### App 里看不到刚装的 Agent

1. 确认安装过程真的完成了（看 Studio 输出面板）
2. 在 App 里点击**左上角 Agent 名称** → 进入 Agent 切换界面
3. 仍然没有：重新安装一次；包名与已安装的同名包冲突时，先删再装

---

## 运行 / 交互

### 点了按钮没反应，或点了几下后 App 卡住

**原因**：几乎都是**回调阻塞了主线程**。框架的所有 Python 回调都在主线程串行执行，框架会等回调返回；单个回调耗时过长会阻塞后续回调，并让 App 卡顿。

**处理**：

- 回调里不要做长耗时 IO、`sleep`、`join`、`future wait`
- 不要等待另一个 Python 回调或后续 App 操作
- 耗时任务丢到自己的后台线程 / 进程，回调只负责启动任务然后立刻返回
- 必须同步等待时，设置**明确的短超时**

参考：[API 组成与使用 · 线程模型](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/api/overview/)

---

### 手柄快捷键不生效

逐条排查：

1. **快捷键是否在 `agent.toml` 注册过**，且代码里的 `shortcut_id` 拼写一致
2. **是否用了保留键**：`BACK`、`START`、`LS`、`RS` 不可用
3. **是否用了不支持的键**：`CENTER`、`LEFT_UP`、`LEFT_DOWN`、`RIGHT_UP`、`RIGHT_DOWN`
4. 按键名拼写是否正确（参考官方[按键列表](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/api/joystick-and-shortcuts/basic-types/#joystickkey)）
5. 部分快捷键是**组合键**（如 `L2 + R2 + A`），注意顺序与同时按下
6. 确认当前**活动 Agent** 就是你的 Agent（App 左上角确认）

---

### 音量调节 Agent：提示「读取音量失败」或「音量调节不可用」

**原因**：音频子系统调用异常。`main.py` 里对 `get_system_volume()` / `set_system_volume()` 都做了异常捕获，失败时会回退到缓存值而不是崩溃。

**处理**：

1. 确认机器人音频服务正常（其他系统音是否正常）
2. 重启机器人音频相关服务后重试
3. 查看 Agent 日志中的 `get_system_volume failed` / `set_system_volume(...) failed` 具体异常
4. 注意：该 Agent 读取的是 `robot.audio_manager`，仅在 **K1 真机**上有意义；虚拟机器人上音量接口可能不可用

---

### 静音后取消静音，音量不是原来的值

**原因**：Agent 会持久化「最后一个非零音量」到 `volume_state.txt`。

**处理**：如果这个文件损坏或版本迁移，会退回默认值 50%。删除 Agent 存储目录下的 `volume_state.txt` 可重置。

---

## 构建 / 发布

### 构建出来的包提示签名相关错误

**原因**：未配置 `[sign]` 时使用通用 debug 证书；某些场景要求正式证书。

**处理**：配置专属密钥库，见官方 [签名与证书](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/signing-and-certificates/)。密钥库与密码**不要提交到仓库**。

---

### 改了代码但机器人上行为没变

**原因**：安装的是旧包，或版本号没变导致没触发升级。

**处理**：提升 `agent.toml` 里的 `version`，重新构建并重新安装。

---

## 获取日志与支持

- **Agent 日志**：代码里的 `self.logger` 输出，在 Booster Studio 的输出 / 日志面板查看
- **系统日志**：参考 [K1 故障排查与支持 · 日志获取](https://docs.booster.tech/zh-CN/docs/product-manual/k1/troubleshooting/restore-factory-settings/)
- **恢复出厂设置**：同上（**谨慎**，会清除机器人上的自定义内容）
- **官方支持**：<software_support@booster.tech>

---

## 还是不行？

带上这些信息再问，能省一大半时间：

1. 固件版本号（App / Studio 里查看）
2. 机器人型号（K1 教育版 / 专业版 / 极客版）
3. 包名与版本（`.agent` 文件名）
4. 报错完整日志（不要截图截一半）
5. 复现步骤：点哪个按钮、第几次出现
