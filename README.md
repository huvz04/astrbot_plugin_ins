# Instagram 订阅推送 · astrbot_plugin_ins

订阅 Instagram 账号，将新增内容自动发到执行订阅命令的 AstrBot 群聊或私聊。
基于 Instaloader 4.15 系列，Python 3.10+、AstrBot 4.16+。

## 支持范围

| 内容 | 行为 |
| --- | --- |
| 帖子、轮播图、视频 | 默认开启；发送正文、原链接及各张图片/视频 |
| Reels | 默认独立检查；与帖子按媒体 ID 去重 |
| Story | 默认开启，需要登录；获取当前仍可见的图片/视频 |
| 精选动态 Highlights | 默认开启，需要登录；与 Story 去重，同一素材不会重复推送 |
| 被标记的帖子 | 可在配置中开启，默认关闭 |
| 私密账号 | 仅限登录账号本来有权查看的内容 |

不包含直播录制、私信、已删除或已过期的 Story、互动贴纸完整交互、评论更新提醒。
这是新增内容监控，不是完整历史备份；已有帖子的编辑不视为新帖。
Instagram 的登录验证、限流和接口变化可能导致抓取失败，不能保证所有账号长期可用。

## 安装

推荐在 AstrBot 的「插件管理 → 从链接安装」中填写：

```text
https://github.com/huvz04/astrbot_plugin_ins
```

也可以手动安装：

1. 在 AstrBot 插件管理中使用“上传/本地安装”（如果当前版本提供），选择发布的 ZIP。
2. 或将 ZIP 中的文件解压到 `AstrBot/data/plugins/astrbot_plugin_ins/`，确保 `main.py`、
   `metadata.yaml`、`_conf_schema.json` 直接位于该目录下，而非再套一层同名目录。
3. 在 AstrBot 使用的 Python 环境安装 `requirements.txt` 中的依赖；插件管理通常会自动安装。
4. 重启或重载插件，在插件配置面板设置登录会话和代理。

源码与更新：[huvz04/astrbot_plugin_ins](https://github.com/huvz04/astrbot_plugin_ins)。

## 登录配置（建议先完成）

Story 和精选需要登录，公开帖子也可能被 Instagram 要求登录。
插件不接收聊天中的密码、Cookie 或验证码。使用自己生成的 Instaloader 会话文件：

```sh
python -m pip install "instaloader>=4.15,<5"
python -m instaloader --login YOUR_LOGIN_USERNAME --sessionfile session-ins
```

按终端提示登录；如 Instagram 要求验证，在浏览器中完成后再试。
也可在能正常登录的电脑生成文件，再复制到 AstrBot 主机。
在插件配置填写：

- `login_username`：生成会话的登录账号名，不是监控目标。
- `session_file`：该文件在 AstrBot 主机或容器内的绝对路径，例如 `/AstrBot/data/session-ins`。
- `proxy`：按需填写 HTTP 代理，如 `http://127.0.0.1:7890`。容器需填其可访问的代理地址。

生成会话的命令需自行具备访问 Instagram 的网络；插件代理配置只影响插件进程内的抓取。
会话文件包含登录凭证，使用 Instaloader 的序列化格式，只导入自己生成的可信文件。
不要提交到 GitHub、上传到群里或附在插件安装包内。修改登录/代理配置后重载插件。

## 使用

先把操作人的 ID 配置为 **AstrBot 管理员**，然后在目标群或私聊发送：

```text
/ins add instagram_username
/ins check
/ins list
```

也接受 `@username` 或账号主页链接。`add`、`remove`、`check` 需要 AstrBot 管理员权限，
并非仅有 QQ 群管理员身份即可。`/ins help` 显示帮助。

- 首次成功获取每类内容时只建立基线，不发旧内容。失败的类别不会被误记为已初始化。
- 后续每 15 分钟左右检查一次，按发布时间顺序发送尚未见过的项目。
- 每个账号、每个会话每轮最多处理 10 条，后续轮次继续处理积压。
- `/ins remove username` 只取消当前会话订阅，并移除该会话对应的去重和待发记录。
- `/ins list` 查看最近检查情况、失败原因与退避时间。运行状态在重载后重新建立。
- `/ins check` 手动触发当前会话的检查；已有检查或处于失败退避时不会强行重复请求。
- `enabled=false` 暂停后台检查，手动检查仍可使用。

## 发送与可靠性

使用 AstrBot 的 `unified_msg_origin` 记录目标，通过 `Context.send_message` 主动发送。
文本与每个媒体分别发送，兼容不能混发视频的消息适配器。适配器必须支持主动发送和对应媒体类型；
没有在所有平台做实机验证。
QQ 官方机器人适配器不支持这里使用的主动发送接口；QQ 场景需使用支持主动消息的 OneBot/NapCat 等适配器。

图片/视频先下载到本地，默认单文件不超过 100 MB。AstrBot 与 NapCat 等若分开部署，
需要适配器支持传输本地文件，或将媒体目录以相同绝对路径共享；否则可关闭 `send_media` 仅发正文和链接。
下载文件保存于 AstrBot 插件数据目录的 `astrbot_plugin_ins/media/`，检查后清理超过 24 小时的缓存。

订阅、去重记录、待发内容和每个媒体的发送进度保存在 `state.sqlite3`，重启后继续。
失败发送不会标记成功，其他会话仍独立处理；下次抓到同一项目时会更新待发媒体的临时 URL。
如果发送成功瞬间进程崩溃、尚未写入状态，重启可能重复这一条消息，无法保证严格“恰好一次”。
平台适配器若内部吞掉发送错误，也可能无法识别失败。

超大文件、已过期 Story 的失效下载地址等可能持续留在队列。
每条发送失败的内容会单独延后重试（1 小时起，最多 6 小时），为后续新内容留出推送名额；该状态重启后保留。
可提高 `max_media_mb`，或临时关闭 `send_media` 并手动检查，使已发出原链接的项目完成。

## 扫描边界

默认每类帖子/Reels/被标记帖子检查前 30 项，精选检查前 30 个集合及其内部项目；
当前 Story 检查全部可见项目。不会遇到第一条旧帖就停止，因此置顶帖不会直接挡住后续新帖。
如果两轮之间发布数超过扫描上限，或停机超过 Story 有效期，可能遗漏内容。
精选项目以素材 ID 去重，不单独通知同一素材被重新归入另一个精选集合。
扩大扫描范围后，之前未扫描到的旧内容可能被当作新发现推送。
首次某类接口长期失败后恢复时才建立该类基线，因此恢复时已存在的内容不会补推。

失败检查指数退避，最多 6 小时；未配置登录导致 Story/精选不可用只提示，不阻止公开内容的正常检查。
新加订阅时先运行 `/ins check`，确认 `/ins list` 没有抓取错误，再等待新内容。

## 开发验证

```sh
python -m unittest discover -s tests -v
```

包含离线队列/重启/去重测试，以及模拟 Instagram 和 AstrBot 的故障测试。
实际 Instagram 登录抓取和真实群推送需使用部署环境完成验收。

## 参考

- [AstrBot 主动消息开发接口](https://docs.astrbot.app/dev/star/resources/send-message.html)
- [Instaloader Python API](https://instaloader.github.io/as-module.html)
- [Instaloader 内容类型与登录要求](https://instaloader.github.io/module/instaloader.html)
- [parserURL：多平台链接解析插件](https://github.com/Ishning/astrbot_plugin_parserURL)，用于参考功能边界；本插件独立实现账号订阅。

未复制上述插件的实现代码；Instaloader 依赖沿用其自身许可证。
