# 02 · 安装单个 Agent 到 K1

下面的 SSH / SCP 方式已于 2026-11-12 在另一台 Jetson ARM64 K1 上验证安装在线音乐 0.1.2。机器人安装器返回 `Install success!`，随后启动、搜索和二维码检查通过。这不等于所有固件、平台和电脑安装入口均已验证。

## 1. 下载成品安装包

| Agent | 直接下载 | 文件字节数 |
| --- | --- | --- |
| 本地音乐播放器 1.2.1 | [下载 .agent](https://raw.githubusercontent.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/main/agents/music-player/com.wenzhoujike.k1.music-1.2.1.agent) | 231563 |
| 在线音乐 0.1.2 | [下载 .agent](https://raw.githubusercontent.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/main/agents/online-music/com.wenzhoujike.k1.onlinemusic-0.1.2.agent) | 87492211 |
| 音量调节 1.0.0 | [下载 .agent](https://raw.githubusercontent.com/1210554414ysl-bit/Booster-K1-Wenzhou-Jike/main/agents/volume-adjustment/com.example.volumeagent-1.0.0.agent) | 以下载文件为准 |

保留 `.agent` 后缀，无需解压。不要将 GitHub 文件展示页另存为安装包，也不要安装 `.unsigned.agent`。下载整个仓库后构建是另一种开发流程，见 [开发说明](03-develop-agent.md)。

## 2. 传到机器人

机器人开机，连接网线或可达的局域网。先在 Booster App / Studio 中确认机器人的实际 IP。下面以 `192.168.10.102` 和默认用户名 `booster` 为例；账号、密码和 IP 以设备实际配置为准。

Windows 打开 PowerShell，进入保存安装包的目录。在线音乐示例：

```powershell
cd "$env:USERPROFILE\Downloads"
Get-Item .\com.wenzhoujike.k1.onlinemusic-0.1.2.agent | Select-Object Name,Length
Get-FileHash .\com.wenzhoujike.k1.onlinemusic-0.1.2.agent -Algorithm SHA256
scp .\com.wenzhoujike.k1.onlinemusic-0.1.2.agent booster@192.168.10.102:/home/booster/
```

在线包 SHA-256 应为：

```text
4d423d21a36e3324efb0285e3ec18a389f7e4aa057d636e37cae27700a663eb3
```

首次连接核对目标设备身份，按提示输入机器人 SSH 密码，密码输入时不显示字符。等待传输完成。

## 3. 调用机器人的安装器

```powershell
ssh booster@192.168.10.102
```

登录机器人后执行：

```bash
python3 /opt/booster/BoosterAgent/install_agent_client /home/booster/com.wenzhoujike.k1.onlinemusic-0.1.2.agent
```

看到 `Install success!` 才表示安装成功。如失败，保留完整的 `error_code` 和 `error`，不要只看上传是否完成。此安装器路径已经在当前两台 Jetson K1 上确认；不同系统若没有此路径，先查对应系统文档。

安装本地播放器或音量调节时，将 SCP 和安装命令中的文件名替换为对应 `.agent` 文件名。

## 4. 在手机上选择 Agent

手机连接机器人，在 Booster App 左上角的 Agent 列表选择「音乐播放器」「在线音乐」或「音量调节」。在线音乐打开「在线点歌」并登录网易云；本地音乐打开「歌单与进度」添加歌曲。

如果使用仓库的 `scripts/k1_agent_ctl.py` 管理运行状态，`start_agent` 只启动进程，还需 `set_agent` 激活；`restart` 子命令包含启动和激活。单纯安装成功不代表已成为当前 Agent。

## Studio 与源码构建

官方支持使用 Studio 构建和运行 Agent。当前文档未验证 Studio 各版本对单个 `.agent` 的导入入口，因此不提供猜测的菜单步骤。安装成品可以采用上面的已验证方式。

本地音乐从源码构建见 [本地播放器说明](../agents/music-player/README.md#构建与资源)；在线音乐必须按 [在线音乐说明](../agents/online-music/README.md#构建和检查) 准备依赖、打包并签名，普通 Studio 构建不能替代该流程。

升级需要相同包 ID、兼容版本和一致签名证书。遇到签名错误先核对证书，不要直接卸载；卸载或降级前备份歌曲、设置和账号数据。详见 [排错](04-faq.md) 和 [版本管理](05-versioning.md)。
