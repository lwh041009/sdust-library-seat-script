# 图书馆抢座脚本

一个用于山东科技大学图书馆座位预约的 Python 脚本。脚本通过 Reqable 抓包自动获取一点通小程序里的 Token，并在指定开抢时间自动提交预约请求。

> 仅供学习和个人自动化使用。请遵守学校图书馆相关规定，不要高频请求或影响系统正常运行。

## 功能

- 自动读取 `user_data.json` 配置
- 通过 Reqable 抓包结果自动提取 Token
- Token 有效时自动复用
- 支持自动把预约日期更新为明天
- 支持关闭日期自动更新，手动指定预约日期
- 支持设置座位号、预约时段、开抢时间
- 支持提前发射、双请求连发、失败后自动捡漏

## 文件说明

```text
main.py              主程序，负责读取配置、获取 Token、定时抢座
token_refresher.py   单独刷新 Token 的工具
crypto_core.py       加密相关逻辑
user_data.json       个人配置文件，不要上传真实内容
启动.bat             Windows 一键启动脚本（可自行设置）
```

## 环境要求

- Windows
- Python 3.10 或更高版本
- Reqable 抓包工具
- 微信一点通小程序

安装依赖：

```bash
pip install requests urllib3 pycryptodome
```

## 使用前准备

1. 安装并打开 Reqable。
2. 确保 Reqable 已开启 HTTPS 抓包能力。
3. 打开微信。
4. 打开一点通小程序，让小程序正常加载。
5. 保持 Reqable 打开，然后运行脚本。

脚本会自动扫描 Reqable 的抓包目录，从最近的响应体中提取 Token，并写入 `user_data.json`。

## 配置说明

首次运行如果没有 `user_data.json`，脚本会自动生成模板。你也可以手动创建：

```json
{
    "token": "",
    "student_id": "你的学号",
    "password": "你的密码",
    "target_date": "2026-05-23",
    "auto_update_target_date": true,
    "seat_code": "301010A",
    "snipe_time": "22:30:00",
    "start_hour": "09:00:00",
    "end_hour": "22:00:00",
    "first_bullet_advance_ms": 800,
    "bullet_gap_ms": 150,
    "snipe_cooldown": 3,
    "snipe_interval": 0.8,
    "snipe_max": 99999,
    "request_timeout": 2
}
```

配置项含义：

| 字段 | 说明 |
| --- | --- |
| `token` | 一点通小程序 Token，可留空，脚本会尝试从 Reqable 抓包中自动获取 |
| `student_id` | 学号，用于校验 Token 是否属于当前账号 |
| `password` | 密码，当前主流程主要依赖抓包 Token |
| `target_date` | 要预约的日期，格式为 `YYYY-MM-DD` |
| `auto_update_target_date` | 是否自动把 `target_date` 改成明天 |
| `seat_code` | 座位号，例如 `301010A` |
| `snipe_time` | 开抢时间，例如 `22:30:00` |
| `start_hour` | 预约开始时间 |
| `end_hour` | 预约结束时间 |
| `first_bullet_advance_ms` | 第一发提前多少毫秒发送 |
| `bullet_gap_ms` | 两发之间的间隔 |
| `snipe_cooldown` | 首轮失败后，等待几秒进入捡漏 |
| `snipe_interval` | 捡漏请求间隔 |
| `snipe_max` | 最大捡漏次数 |
| `request_timeout` | 单次请求超时时间，单位秒 |

## 日期和开抢时间逻辑

如果 `auto_update_target_date` 为 `true`，脚本每次运行会自动把 `target_date` 改成明天。

如果你想手动指定日期，把它改成 `false`：

```json
"auto_update_target_date": false
```

脚本会按照配置里的 `target_date` 预约。

开抢时间会根据预约日期自动推导：

```text
实际开抢时间 = target_date 的前一天 + snipe_time
```

例如：

```json
"target_date": "2026-05-23",
"snipe_time": "22:30:00"
```

实际开抢时间就是：

```text
2026-05-22 22:30:00
```

如果运行时已经过了实际开抢时间，脚本会立即执行并进入捡漏策略。

## 运行方法

方式一：双击运行

```text
启动.bat
```

方式二：命令行运行

```bash
python main.py
```

单独刷新 Token：

```bash
python token_refresher.py
```

## 推荐使用流程

1. 打开 Reqable。
2. 打开微信一点通小程序，等待加载完成。
3. 检查 `user_data.json` 中的座位号、预约日期和预约时间。
4. 运行 `python main.py` 或双击 `启动.bat`。
5. 如果 Token 有效，脚本会直接复用。
6. 如果 Token 无效，脚本会尝试从 Reqable 抓包记录中自动提取新 Token。
7. 到达开抢时间后，脚本会自动提交预约。

## 常见问题

### 找不到 Reqable 抓包目录

请确认：

- Reqable 已经打开
- Reqable 正常开启抓包
- 微信一点通小程序已经打开并加载完成
- 当前电脑用户有 Reqable 抓包目录

### 未从抓包中找到 Token

可以尝试：

- 重新打开一点通小程序
- 在 Reqable 中确认能看到一点通相关请求
- 等待几秒后重新运行脚本
- 删除旧的 `token` 后重新抓取

### 脚本还在等待 22:30

检查 `target_date` 和 `snipe_time`。脚本的实际开抢时间是 `target_date` 前一天的 `snipe_time`。

例如预约 `2026-05-24`，开抢时间就是 `2026-05-23 22:30:00`。

