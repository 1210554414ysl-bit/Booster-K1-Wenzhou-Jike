# 03 · 开发与构建 K1 Agent

本章说明本仓库的实际开发入口。SDK 接口与配置语法以 [官方快速上手](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/quick-start/) 和 [配置说明](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/configuration-files-and-options/) 为准。

## 目录和运行配置

```text
agents/<agent-name>/
├── agent.toml          # 包 ID、版本、显示名称、入口、快捷键
├── build.toml          # 平台、依赖、构建配置
├── src/                # Python 源码
├── res/                # 官方工具收录的项目资源
└── README.md           # 使用、构建、验证范围
```

`agent.json` 是运行清单，当前目录中的清单供对照；发布时必须与包内清单一致。修改功能后同步版本、构建产物和文档，保持包 ID 稳定。

## 各 Agent 的构建方式

| Agent | 方式 |
| --- | --- |
| 本地音乐播放器 | 在其目录执行 `python scripts/build_panel.py`，生成 `res/music_panel.zip`，再用 Studio 构建和签名 |
| 在线音乐 | 按其 README 准备 ARM64 Node、Python wheels、npm 依赖，再运行 `scripts/build_release.py`，最后用原证书签名 |
| 音量调节 | 本仓库整理原项目源码及成品包，未提供完整的 `agent.toml` / `build.toml` 工程；重建前应取回原工程或按 SDK 模板补齐配置 |

在线音乐的 `resources/` 由自定义打包脚本收录。官方构建工具与自定义打包脚本对资源目录的处理不同，不能假设把文件放在项目任意位置都会进入安装包。

## 平台与依赖

`build.toml` 的 `[platform].supports` 声明允许的平台。现有在线包只声明 `real_jetson`，其运行时和二进制依赖为 Linux ARM64 / Python 3.10 准备。扩展平台需要核对启动器、Python ABI、Node 和系统库，并重新验证；只添加平台名不足以完成移植。

`requirements.min_api_level` 是最低声明，`compile_api_level` 表示构建 SDK 的 API。使用新接口时检查实际 SDK 支持，不能仅凭固件或 Studio 的版本号推算兼容性。

## 回调、播放与状态

- 点击回调尽快返回，耗时下载、解码和等待由后台任务处理，避免锁与 SDK 回调相互等待。
- 本地播放器用单个工作线程管理 FFmpeg 音频进程；界面状态由独立更新线程发布。
- 在线播放器在复用播放逻辑的基础上增加平台解析和 FFmpeg 流进度处理。
- 存储通过 `storage_manager` 获取，避免把某台机器人的绝对目录或 IP 写死到功能代码中。
- 框架的线程与组件接口以当前 SDK 为准。参考已有代码时，也要核对其目标 API 和验证范围。

## 签名与更新

Studio 可以使用其配置的调试证书；直接调用构建工具时需要提供签名配置。在线音乐使用独立证书，后续同 ID 升级须沿用该证书。本地音乐 1.2.1 沿用 1.2.0 的 SDK 调试证书。

私钥、PKCS#12 文件和密码保存在仓库之外。重新从源码构建并换用自己的签名，不保证能覆盖安装同 ID 的已发布包。详见 [官方签名说明](https://docs.booster.tech/zh-CN/docs/developer-guide/agent-development/configuration-and-release/signing-and-certificates/)。

## 验证与发布

至少记录代码提交、包版本、签名验证结果、包内容、目标平台和测试结果。模拟检查、真机安装、实际音频输出、手机 App 操作分别记录。未测试的项目标记待验证。

安装真机时使用最终签名 `.agent`，并核对下载文件的 SHA-256。发现问题先复现并保留回归检查，再做修复。版本保存与回退见 [版本管理](05-versioning.md)。
