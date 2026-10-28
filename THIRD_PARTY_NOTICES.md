# 第三方来源、许可与数据

- **AstrBot**：https://github.com/AstrBotDevs/AstrBot ，框架采用 AGPL-3.0-or-later。本仓库通过其插件接口加载，没有复制或捆绑 AstrBot 框架。接口核对源版本 4.29.3，提交 `abf683071952375ec36baa4cad6b6f3d9c9bb03f`。
- **Kokomi_Bot**：作者 SangonomiyaKoko 及原项目贡献者，https://github.com/SangonomiyaKoko/Kokomi_Bot ，原 README 标注 CC BY-NC 4.0。本仓库包含独立编写的适配器；不包含上游实现、字体、模型、图片或后端账号。原版由使用者另行取得，按其许可和服务规则运行。上游接入签名来自其公开文档和 V4 tag 4.1.9 的 run_bot.py。
- **浩舰**：https://iwarship.net/ 。运行时查询站点实际公开给当前会话的数据，注明来源与版本；本仓库不分发真实全量舰船数据库、网站页面或会话 Cookie。站点字段和路径可能变化，适配器失败会提示核对官网。
- **Wargaming**：https://developers.wargaming.net/ ，使用公开 WoWS API，须使用自己的 application_id 并遵守开发者计划条款；不分发应用密钥或读取未授权私密资料。
- **知识来源**：详见 knowledge 中的来源元数据。战术推演为原创分析；赛事事实与教学摘要引用其原始来源，不提供整篇文章、完整字幕或付费课程。原作者的姓名/频道和赛事品牌不表示他们为本项目背书。
- **依赖库**：aiohttp（Apache-2.0/MIT）、Pillow（HPND），由 pip 按各自发行包许可安装，不捆绑其二进制。

项目自有代码的 MIT 许可不替代第三方作品、账号服务或数据库本身的许可。
