# 02 · 安装 Agent 到机器人

> **目标**：把本仓库的 `.agent` 包装到 K1 上，并在 App 里用起来。
> **官方章节**：[快速上手 · 步骤 5](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/) ・ [使用 Agents](https://docs.booster.tech/zh-CN/docs/product-manual/k1/basic-operations/agents/)

---

## 0. 安装前检查

| 检查项 | 要求 |
| --- | --- |
| 固件版本 | ≥ 1.7.0 |
| `boosteros` | 固件 1.7.0 需手动 `pip install boosteros`；≥ 1.8.0 已预置 |
| 机器人型号 | 包内 `requirements.models` 必须包含你的型号（本仓库的包为 `Booster K1`） |
| 平台 | 教育版 / 专业版走 `real_jetson`，极客版走 `real_qcom` |

检查方法见 **[01-setup.md](01-setup.md)**。

---

## 方式 A：用 Booster Studio 一键安装（开发者推荐）

适合自己有源码 / 工程目录，需要反复改、反复调试的场景。

1. 打开 Booster Studio，**连接机器人**（真机或虚拟机器人）
2. 打开对应的 Agent 工程目录（本仓库即 `agents/volume-adjustment/` 所在工程）
3. 在 Studio 中执行「安装 / 运行到机器人」
4. 观察 Studio 输出面板，确认安装完成、Agent 已启动

> Studio 的按钮位置会随版本变化，以你本地版本的实际界面为准；不确定时对照官方 [调试机器人 · 连接真机](https://docs.booster.tech/zh-CN/docs/product-manual/booster-studio/debug-robot/connect-robot/) 一节。

---

## 方式 B：用 `.agent` 文件分发安装（使用者推荐）

适合「别人已经打好包，你只需要装上去」的场景。`.agent` 是自包含的安装包，不需要你懂 Python。

1. 从本仓库下载需要的包，例如：

   ```
   agents/volume-adjustment/com.example.volumeagent-1.0.0.agent
   ```

2. 在 Booster Studio 中连接机器人，选择安装该 `.agent` 文件
3. 等待安装完成

> `.agent` 包名规则是 `包名-版本号.agent`，**同名同类包再次安装即为升级**，不需要先卸载。

---

## 方式 C：从 Agent Store 安装（官方 / 三方上架包）

1. 手机打开 Booster App，连接机器人
2. 点击左上角 Agent 名称 → 「探索更多 - 从 Agent Store 安装」
3. 在 [Agent Store](https://agentstore.booster.tech/) 中搜索并安装

> 本仓库的 Agent 属于**内部/自研**，默认不上架 Agent Store，请用方式 A 或 B。

---

## 装好之后：在 App 里用起来

1. 手机 App 连接机器人
2. **点击左上角的 Agent 名称**，进入 Agent 切换界面
3. 选择对应的 Agent（例如「音量调节」）
4. 进入后即可看到该 Agent 声明的按钮，点击或用手柄快捷键操作

> 部分 Agent 会自带手柄快捷键，切换后直接按键即可，不需要打开 App。

---

## 更新与删除

- **更新**：用新版本的 `.agent` 覆盖安装即可（版本号变化会识别为升级）
- **删除**：在 App 的 Agent 列表中**长按**该 Agent → 「删除」

---

## 安装失败怎么办

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 安装直接被拒绝 | 固件 < 1.7.0 | 升级固件 |
| 装上了但启动就退出 | 缺 `boosteros`（1.7.0 固件） | SSH 登录机器人 `pip install boosteros` |
| 装上了但启动报依赖 / 平台不匹配 | 包的 `platforms` 不含当前平台 | 确认是教育/专业版还是极客版，用对应平台的包重装 |
| App 里看不到该 Agent | 未切换到该 Agent / 安装未完成 | 回到 App 左上角 Agent 列表确认；重装 |

更多见 **[04-faq.md](04-faq.md)**。
