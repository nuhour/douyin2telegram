# douyin2telegram

自动把你在抖音点赞过的作品（视频 / 图文相册）同步搬运到指定的 Telegram 频道，按点赞顺序从旧到新逐条投递，Cookie 失效或作品处理失败时会私聊告警。

## 环境要求

- macOS 本机长期运行（通过 launchd 定时调度）
- Python 3.10+（本机使用 `/opt/homebrew/bin/python3`，版本 3.10.19）

## 环境搭建

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

后续所有命令均使用 `venv/bin/python`，无需手动激活虚拟环境。

项目实际路径（部署脚本 `scripts/com.douyin2telegram.sync.plist` 中已写死此路径）：

```
/Volumes/sn570/MacintoshHD整理/dev/pycharm/projects/douyin2telegram
```

## 一次性准备

首次使用前需要完成以下四步配置，之后无需重复。

### ① 申请 Telegram API 凭证

访问 [my.telegram.org](https://my.telegram.org)，登录后进入 "API development tools"，创建一个应用，得到 `api_id` 和 `api_hash`。

### ② 创建 Bot 并配置频道

1. 在 Telegram 中找 [@BotFather](https://t.me/BotFather)，用 `/newbot` 创建一个 Bot，记下返回的 `bot_token`。
2. 把这个 Bot 拉进你的目标频道，并设为**管理员**（需要发消息权限）。
3. 给 Bot 私聊发一次 `/start`（否则 Bot 无法主动给你发告警消息）。
4. 找 [@userinfobot](https://t.me/userinfobot) 查询你自己的 `chat_id`，用于接收告警。

### ③ 获取抖音 Cookie

浏览器登录 [www.douyin.com](https://www.douyin.com)，打开开发者工具的 Network 面板，随便找一个请求，复制其请求头中完整的 `Cookie` 字段值。

### ④ 写配置文件

```bash
cp config.example.yaml config.yaml
```

按注释填写 `config.yaml` 中的各项：抖音 Cookie 与你的主页链接、Telegram 的 `api_id`/`api_hash`/`bot_token`/`channel`/`alert_chat_id`，以及同步相关的限速参数。

**注意：`config.yaml` 含敏感信息（Cookie、Bot token 等），已被 `.gitignore` 排除，切勿提交或分享给他人。**

## 联调

准备工作完成后，先小范围验证再放量运行。

### 1. 核验字段假设

```bash
venv/bin/python scripts/inspect_aweme.py <你点赞过的任一作品链接>
```

这个脚本会打印真实作品详情的所有字段名与类型，以及 `extract_media` 的解析结果，用来核验代码中对 `aweme_id`、`desc`、`nickname`、`video_play_addr`、`images` 等键名的假设是否与抖音接口实际返回一致。

如果发现键名不一致，只需要修改 `d2t/downloader.py` 的 `extract_media`/`_first_url` 和 `d2t/fetcher.py` 的 `_normalize`，改完后重跑一遍测试确认没有破坏其他逻辑：

```bash
venv/bin/python -m pytest tests/
```

### 2. 小批量试跑

```bash
venv/bin/python main.py --limit 2
```

核验点：频道里应出现 2 条你最早点赞的作品（含文案 caption），私聊没有收到告警消息，`data/state.db` 中对应记录的状态变为 `uploaded`。

### 3. 放量跑满一个 batch

确认无误后，不带 `--limit` 参数完整跑一次：

```bash
venv/bin/python main.py
```

观察发送节奏（限速间隔）和投递顺序是否符合预期。

## 部署（launchd 定时任务）

联调通过后，把 plist 模板复制到 launchd 的用户级目录并加载：

```bash
cp scripts/com.douyin2telegram.sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.douyin2telegram.sync.plist
```

该配置会在加载时立即执行一次（`RunAtLoad`），此后每小时（`StartInterval` 3600 秒）自动拉起一个新进程执行一次同步 tick。

如需卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.douyin2telegram.sync.plist
```

## 日常运维

- **Cookie 失效**：抖音接口出错（Cookie 过期、风控等）时程序会自动进入冷却期，并通过 Bot 私聊给你发送告警。收到告警后，按上文「获取抖音 Cookie」重新登录复制 Cookie，更新 `config.yaml` 即可，**不需要重启任何服务**——launchd 每小时会拉起全新进程，自动读取最新配置。
- **重试失败作品**：单条作品连续失败达到重试上限后会被标记为 `failed` 并停止自动重试，同时私聊告警。确认问题解决后执行：

  ```bash
  venv/bin/python main.py --reset-failed
  ```

  会把所有 `failed` 状态的作品重置回 `pending`，下次 tick 会重新尝试。

- **日志位置**：`data/launchd.log`（标准输出）与 `data/launchd.err.log`（标准错误）。
- **查看同步状态**：状态数据存放在 SQLite 中，可直接查询：

  ```bash
  sqlite3 data/state.db "SELECT aweme_id, status, retries, error FROM works ORDER BY sort_key DESC LIMIT 20;"
  ```
