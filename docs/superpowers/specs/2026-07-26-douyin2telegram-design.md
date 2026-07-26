# douyin2telegram 设计文档

日期：2026-07-26

## 目标

把自己抖音账号的点赞（喜欢）视频定期自动同步到 Telegram 频道：频道里出现**完整无水印视频文件**（图集以相册形式发送）。首次运行全量补齐历史点赞，之后增量同步新点赞。

## 关键决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 内容形式 | 完整视频文件（图集发相册） | 观看体验最好、可永久保存；超限作品降级为链接卡片 |
| 运行环境 | 本机 Mac + launchd 定时 | 住宅 IP 对抖音风控最友好；Cloudflare/GitHub Actions 数据中心 IP 风控风险高 |
| 存量处理 | 全量补齐 + 增量同步，统一队列 | 首次运行全部入库为 pending，每 tick 限量处理，几天内自然补完 |
| 抖音侧 | f2（Johnserf-Seed/f2） | 官方支持抖音 `like` 模式，内置 a_bogus 签名，持续维护，可作 Python 库调用 |
| 备选 | Evil0ctal/Douyin_TikTok_Download_API | f2 失效时的备胎，同样支持"获取用户主页喜欢作品数据" |
| 已排除 | DouK-Downloader | 签名算法已停止维护，需自备加密参数 |
| Telegram 侧 | Telethon + Bot Token（MTProto） | 上传上限 2GB，绕开 Bot API 50MB 限制 |
| 依赖管理 | venv + pip + requirements.txt | 用户偏好；依赖仅 f2、telethon |
| 同步频率 | 每小时一次（可配置） | launchd 定时 + 开机补跑 |

## 总体架构与数据流

单一 Python 项目，launchd 每小时唤起一次 `main.py`，每次运行为一个"同步 tick"：

```
launchd(每小时) → main.py
  ① 增量拉取：f2 拉喜欢列表（新→旧翻页），遇到连续 3 条已入库作品即停
     → 新作品写入 SQLite(pending)
  ② 处理队列：取一批 pending（按点赞顺序旧→新，默认 20 条/tick），逐条：
     f2 下载无水印视频/图集 → Telethon 上传频道 → 标记 uploaded → 删本地文件
  ③ 异常上报：Cookie 失效/连续失败 → Bot 私聊告警
```

## 模块划分

| 模块 | 职责 |
|---|---|
| `config.py` | 读取 `config.yaml`：抖音 Cookie、用户主页链接、Bot Token、频道 ID、批量/限速参数 |
| `state.py` | SQLite 状态库：`works` 表（aweme_id 主键、类型 video/images、标题、作者、状态、时间戳）；提供去重、取队列、改状态 |
| `fetcher.py` | 封装 f2 `DouyinHandler`，分页拉喜欢列表元数据 |
| `downloader.py` | 调 f2 下载无水印文件到临时目录 |
| `uploader.py` | Telethon 上传：视频 `send_file`（supports_streaming + 缩略图 + 时长宽高元数据），图集发相册；caption 统一为「标题 / 作者 / 原链接」 |
| `notifier.py` | 通过 Bot 私聊发告警与摘要 |
| `main.py` | 编排 + 文件锁（防止上一 tick 未结束又被唤起） |

### works 表状态机

`pending → uploaded`（正常路径）；`pending → failed`（重试 3 次仍失败）；`pending → skipped_oversize`（>2GB 或地址失效，已降级发链接卡片）。failed 可手动重置为 pending 重试。

## 关键策略

- **增量停止条件**：喜欢列表按点赞时间倒序分页；从头翻页直到遇到**连续 3 条**已入库作品才停，防止中途取消点赞导致单条判停漏数据。
- **限速**：每条处理间隔随机 5~15 秒 + f2 内置延时；单 tick 上限 20 条（可配置）。
- **发送顺序**：按点赞时间正序发送，频道顺序与点赞顺序一致。
- **图文点赞**：图集帖用 Telegram 相册（sendMediaGroup 语义）发送，不跳过。

## 错误处理

- **Cookie 失效/风控**（接口返回空或异常）：立即终止本 tick，Bot 私聊告警"请更新 Cookie"，写入 6 小时冷却标记，冷却期内不重试。
- **单条下载/上传失败**：重试 3 次后标记 failed 并跳过，不阻塞队列；告警附失败清单。
- **超 2GB / 下载地址失效**：降级为封面 + 原链接卡片，标记 skipped_oversize。
- **磁盘**：上传成功即删本地文件，仅承担临时占用。

## 部署与一次性准备

- Python 3.12，venv + pip + requirements.txt（依赖：f2、telethon）。
- launchd plist：每小时触发；Mac 关机错过的排程开机后补跑。
- 用户一次性操作：① BotFather 建 Bot 并拉进频道当管理员；② 浏览器登录 douyin.com 复制 Cookie 写入配置；③ `launchctl load` 装载排程。
- Cookie 过期后需手动更新（程序检测失效并私聊提醒）。

## 测试

- 单元测试：状态机流转、增量停止逻辑（连续 3 条判停）。
- 联调：真实小批量（限 2 条）跑通"拉取→下载→上传"全链路后再放开队列。

## 已知风险

- f2 依赖抖音网页接口逆向，抖音改版可能短暂失效，需等上游更新；备选 Evil0ctal 项目。
- 喜欢列表为私密数据，必须使用本人登录 Cookie；Cookie 泄露等于账号泄露，配置文件不入 git。
