# 把原版 Kokomi 水表功能接到 AstrBot

这份说明针对你指定的 [SangonomiyaKoko/Kokomi_Bot](https://github.com/SangonomiyaKoko/Kokomi_Bot)。本插件同时提供自己的 WG 官方数据查询，以及原版 Kokomi V4 的独立进程桥接。两者的账号绑定互相独立。

桥接的作用是把 `wws ...` 命令交给你自己安装、配置并获得数据接口授权的原版 Kokomi，再把其文字或图片发回 AstrBot。因此它保留原版实际可用的命令能力，包括原版自己的近期历史、服务器/全服榜、个人排名、公会相关查询等。具体有哪些命令、当前哪些数据可用，以你的原版 `wws help` 和数据服务授权为准；这份适配器不会凭空取得上游数据库、授权账号或已关闭的接口。

## 1. 先选对原版

2026-10-28 核对的 upstream `main` 是正在重构的 V5：`app/main.py` 直接返回测试消息，命令解析区被注释。不要把它当作已实现的完整 V4，也不要把 `USE_MOCK=true` 的示例数据当成真实战绩。

公开的 [4.1.9 Release](https://github.com/SangonomiyaKoko/Kokomi_Bot/releases/tag/4.1.9) 没有打包文件，它对应的源码树只有 README 和演示入口。公开的 [4.1.1 Release](https://github.com/SangonomiyaKoko/Kokomi_Bot/releases/tag/4.1.1) 则列有 `Kokomi_Bot.zip`，约 370 MB；也可以使用你已经获得的、能独立运行的合法 V4 安装包。

这次已经核实 V4 的官方演示 [run_bot.py](https://github.com/SangonomiyaKoko/Kokomi_Bot/blob/4.1.9/run_bot.py) 和 [V4 安装说明](https://github.com/SangonomiyaKoko/Kokomi_Bot/blob/main/README_OLD.md) 的调用约定，尚未拿你的上游账号实际登录和验证数据服务。桥接的自动化测试使用替代 dispatcher，只验证传输、身份和图片处理。

## 2. 先让原版能独立查表

1. 在浏览器打开上面的 4.1.1 Release 链接。
2. 展开页面下面的 `Assets`。
3. 下载 `Kokomi_Bot.zip`，请确认名称和来源。
4. 下载完成后，在“下载”文件夹里右键这个压缩包，选择“全部解压缩”。不要在压缩包预览窗口里运行文件。
5. 把解压后的原版包文件夹放到固定位置，例如 `D:\WoWS\Kokomi_Bot`。
6. 进入此文件夹，寻找 `command_select.py`。这就是稍后 `--kokomi-root` 应指向的目录。如果它还套在一层同名目录里，继续进入，直到能看见这个文件。
7. 按原版 V4 说明安装它自己的 Python 和依赖。原版历史文档的开发版本是 Python 3.8.10；如果你使用其他版本，需要先验证原版本身能运行。不要强行把原版依赖装到 AstrBot 的环境里。
8. 打开原版自己的 `config.py`，找到 `API_URL`、`API_TYPE`、`API_USERNAME`、`API_PASSWORD`。向原版维护者取得你有权使用的数据接口和凭据，再填写。它们不是 WG application_id，也不是 AstrBot 模型密钥。
9. 同一配置中把 `RETURN_PIC_TYPE` 改成 `'file'`；桥接需要读取原版生成的文件图片。不要设成 `'base64'`。
10. `BOT_PLATFORM` 与稍后插件中的 `kokomi_platform` 保持一致，默认使用 `'qq_bot'`。原版还列出 `discord`、`qq_group`、`qq_guild`。
11. 如果原版需要设置管理员，直接按原版配置设置实际用户 ID。本适配器不把消息中声明的 `is_admin` 或角色字段传给原版。
12. 按原版说明用 `run_bot.py` 测试 `wws help`，然后测试一条有数据权限的查询。能得到真实结果后，再往下接桥。

如果原版单独运行就提示鉴权失败、服务不可用或字体缺失，请先解决原版这一层。桥接不能修复上游授权或补齐缺失的原版美术资源。

## 3. Windows 启动桥接

以下例子假设：原版在 `D:\WoWS\Kokomi_Bot`；本插件在 `D:\AstrBot\data\plugins\astrbot_plugin_wows_captain`。请把路径替换成你电脑的实际路径。

1. 打开你平时能成功运行原版 Kokomi 的 PowerShell/终端窗口。沿用原版的 Python 环境。
2. 输入下面这一行，安装桥接用到的网络库：

   ```powershell
   python -m pip install "aiohttp>=3.10,<4"
   ```

3. 在这个窗口生成一串随机密钥：

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. 复制它。不要发到群里、贴到 GitHub，或提交到仓库文件。
5. 在当前窗口设置环境变量；把下面等号右边的整段示例替换成刚生成的密钥：

   ```powershell
   $env:WOWS_KOKOMI_BRIDGE_TOKEN = '把这里替换成随机密钥'
   ```

6. 输入启动命令，实际路径必须用英文双引号包住：

   ```powershell
   python "D:\AstrBot\data\plugins\astrbot_plugin_wows_captain\bridge\kokomi_server.py" --kokomi-root "D:\WoWS\Kokomi_Bot"
   ```

7. 看到监听 `127.0.0.1:19090` 的提示后，让这个窗口保持运行。关闭窗口就会停止桥接。
8. 桥接只默认监听本机；不要在公网路由器上开放 19090 端口。

如果原版使用旧 Python，请检查网络库有没有适配该版本的发行包。桥接代码使用 Python 3.8 可解析的语法与标准 API，但未经你的具体原版安装环境验证。推荐先沿用原版能工作的环境，不要为了桥接盲目升级整个原版。

## 4. 给 AstrBot 读同一串密钥

1. 密钥要同时存在于“启动桥接的进程”和“启动 AstrBot 的进程”。只在桥接窗口设置一次，AstrBot 不会自动知道。
2. 如果 AstrBot 由 PowerShell 手动启动，在它的启动窗口也执行同样的 `$env:WOWS_KOKOMI_BRIDGE_TOKEN = '你的密钥'`，然后从这个窗口启动 AstrBot。
3. 如果用启动器，先完全退出 AstrBot 和启动器。在 Windows 搜索“编辑账户的环境变量”，打开后点“用户变量”下的“新建”，变量名填 `WOWS_KOKOMI_BRIDGE_TOKEN`，变量值填你的随机密钥，连续点确定；重新启动启动器和 AstrBot。
4. 在 AstrBot 管理面板打开本插件配置。
5. 把 `kokomi_bridge_url` 填为 `http://127.0.0.1:19090`。
6. 把 `kokomi_token_env` 保持为 `WOWS_KOKOMI_BRIDGE_TOKEN`。这个字段填环境变量名，不能填密钥本身。
7. `kokomi_platform` 保持与原版一致，默认 `qq_bot`。
8. 保存配置，再重载插件。
9. 在机器人聊天里发送 `/wws help`。如平台把 `/` 当作特殊前缀，也可以发送 `wws help`；以插件主安装指南的消息前缀配置为准。
10. 返回原版帮助以后，用原版命令绑定账号，再查询。不要把 `/wows bind` 的本地绑定当成原版 Kokomi 的绑定。

## 5. Docker / Linux

桥接仍在 Kokomi 自己的环境中运行：

```bash
export WOWS_KOKOMI_BRIDGE_TOKEN='你的至少24字符随机密钥'
python /实际插件路径/bridge/kokomi_server.py --kokomi-root /实际原版路径/Kokomi_Bot
```

如果 AstrBot 在 Docker 而桥接在宿主机，容器中的 `127.0.0.1` 指容器自身。Windows Docker Desktop 通常使用 `http://host.docker.internal:19090` 访问宿主机，同时桥接需以 `--host 0.0.0.0` 监听可达地址，并用系统防火墙只允许本机/指定容器网络。Linux 建议把桥接和 AstrBot 放到同一受控 Docker 内部网络，通过桥接服务名访问；不得直接暴露公网端口。

给 AstrBot 容器加入 `WOWS_KOKOMI_BRIDGE_TOKEN` 环境变量后，重建/重新创建容器使其生效。容器内不需要安装原版 Kokomi 的依赖，原版在自己的进程中运行。跨机器连接请使用 HTTPS 或受控私网，避免把 Bearer 密钥放在公开 HTTP 连接上。

## 6. 验证与排错

- **提示没配置桥接地址或密钥**：检查 `kokomi_bridge_url`，以及启动 AstrBot 的进程是否真的读到了 `WOWS_KOKOMI_BRIDGE_TOKEN`；保存环境变量后必须重新启动相关进程。
- **提示密钥不一致**：桥接窗口与 AstrBot 窗口必须使用同一串密钥。复制时不要多加空格。
- **无法连接**：桥接窗口是否仍在；端口是否为 19090；Docker 是否错误地填了容器自己的 `127.0.0.1`。
- **没有 command_select.py**：下载错了 V5 源码，或 `--kokomi-root` 指错层级。请选择原版 V4 包内含该文件的目录。
- **无法加载 dispatcher**：先在原版环境运行原版测试；缺少哪个原版依赖就按它的说明补齐。桥接不会自动安装数百 MB 原版资源。
- **原引擎返回 HTTP 502**：桥接已收到请求，但原版自身失败。检查原版数据接口授权、网络、字体、资源路径。桥接不会把含密码的异常全文转发到群聊。
- **图片返回 HTTP 400**：确认原版 `RETURN_PIC_TYPE='file'`，并且生成文件位于 `--kokomi-root` 目录内部，大小不超过 12 MB。本适配器不允许把任意外部文件作为图片传出。
- **帮助正常、某项查表失败**：帮助通过只能说明 dispatcher 能响应；不能证明外部数据服务已授权。继续测试真实账号/战绩/榜单并检查上游响应。
- **两套绑定不同步**：原版 `wws bind ...` 与本插件 `/wows bind ...` 使用不同的存储，分别绑定即可。

## 7. 许可与验证边界

原版 Kokomi 当前 README 标明 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)；使用它的代码和资源时，遵守其对应版本随附的许可和授权条件。该桥接使用用户已经安装的原版，仓库没有复制其代码、图片、字体、数据库或凭据。

本项目已验证桥接的 HTTP 身份验证、原命令参数传输、用户 ID 保留、角色字段丢弃、文件访问边界、图片验证和一次性提交行为。原版账号授权、上游历史数据、排行榜服务和各个原版命令，仍需在你的环境中用合法账号逐项验证。不能把桥接测试通过写成“已经验证所有 Kokomi 功能”。
