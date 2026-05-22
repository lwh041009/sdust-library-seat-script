"""
main.py - 图书馆抢座（两发策略 + 精确计时）
"""

import sys
import time
import requests
import json
import os
import re
import gzip
import base64
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from crypto_core import generate_yStr
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = 'user_data.json'


# ====== 工具函数 ======
def T():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "student_id": "202311100913",
            "password": "2017",
            "target_date": (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
            "auto_update_target_date": True,
            "seat_code": "301011A",
            "snipe_time": "22:30:00",
            "start_hour": "08:00:00",
            "end_hour": "22:00:00",
            "first_bullet_advance_ms": 800,
            "bullet_gap_ms": 150,
            "snipe_cooldown": 3,
            "snipe_interval": 0.8,
            "snipe_max": 99999,
            "request_timeout": 2
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print(f"[{T()}] [*] 已生成配置文件模板: {CONFIG_PATH}")
        print(f"[{T()}] [*] 请先填写 student_id 和 password，然后运行本脚本")
        exit()

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def ensure_config_defaults(config):
    defaults = {
        "auto_update_target_date": True,
        "first_bullet_advance_ms": 800,
        "bullet_gap_ms": 150,
        "snipe_cooldown": 3,
        "snipe_interval": 0.8,
        "snipe_max": 99999,
        "request_timeout": 2
    }
    changed = False
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        save_config(config)
    return config


def auto_fix_date(config):
    if not config.get("auto_update_target_date", True):
        print(f"[{T()}] [*] 自动更新预约日期已关闭，预约日期按配置: {config.get('target_date')}")
        return config

    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    if config.get("target_date", "") != tomorrow:
        print(f"[{T()}] [*] 日期: {config.get('target_date')} -> {tomorrow}")
        config["target_date"] = tomorrow
        save_config(config)
    return config


def decode_jwt_payload(token):
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def is_jwt_valid(token):
    if not token or len(token) < 100 or not token.startswith('eyJ'):
        return False
    payload = decode_jwt_payload(token)
    if not payload:
        return False
    now = int(time.time())

    # 兼容不同字段名和毫秒级时间戳
    exp = payload.get('exp') or payload.get('expiration') or payload.get('expire') or 0
    if isinstance(exp, (int, float)):
        if exp > 100000000000:  # 毫秒级
            exp = exp // 1000
    else:
        exp = 0

    is_valid = now < exp - 300
    if not is_valid:
        print(f"[{T()}] [!] Token 已过期 (exp={exp}, now={now})")
        print(f"[{T()}] [!] Token payload: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return is_valid


# ====== Reqable 抓包 Token 提取 ======
def find_reqable_capture_dir():
    candidates = []
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        candidates.append(os.path.join(appdata, 'Reqable', 'capture'))
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        candidates.append(os.path.join(localappdata, 'Reqable', 'capture'))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def decode_body(data):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    try:
        return gzip.decompress(data).decode('utf-8')
    except Exception:
        return None


def extract_token_from_text(text):
    try:
        obj = json.loads(text)
        for key in ['token', 'access_token', 'accessToken', 'Token']:
            val = obj.get(key)
            if val and len(str(val)) > 20:
                return val
            if 'data' in obj and isinstance(obj['data'], dict):
                val = obj['data'].get(key)
                if val and len(str(val)) > 20:
                    return val
    except Exception:
        pass
    jwt_match = re.search(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', text)
    if jwt_match:
        return jwt_match.group(0)
    return None


def fetch_token_from_reqable(capture_dir):
    body_files = []
    for f in os.listdir(capture_dir):
        if f.endswith('.reqable') and 'res-raw-body' in f:
            fp = os.path.join(capture_dir, f)
            body_files.append((os.path.getmtime(fp), fp, f))
    if not body_files:
        return None
    body_files.sort(reverse=True)
    for mtime, fp, fname in body_files[:200]:
        size = os.path.getsize(fp)
        if size == 0 or size > 500000:
            continue
        try:
            data = open(fp, 'rb').read()
            text = decode_body(data)
            if text is None:
                continue
            token = extract_token_from_text(text)
            if token:
                if token.startswith('eyJ') or (isinstance(decode_jwt_payload(token), dict) and decode_jwt_payload(token).get('exp')):
                    return token
                try:
                    if isinstance(json.loads(text), dict):
                        obj = json.loads(text)
                        for key in ['token', 'Token']:
                            val = obj.get(key) or obj.get('data', {}).get(key)
                            if val and len(str(val)) > 20:
                                return val
                except Exception:
                    pass
        except Exception:
            continue
    return None


def auto_get_token(config):
    existing = config.get("token", "")
    cfg_student_id = config.get("student_id", "")
    if is_jwt_valid(existing):
        payload = decode_jwt_payload(existing)
        if payload:
            token_uid = str(payload.get("userId", ""))
            if token_uid == cfg_student_id:
                print(f"[{T()}] [*] 现有 Token 有效且账号匹配，复用")
                return existing
            else:
                print(f"[{T()}] [*] 账号已变更 ({token_uid} -> {cfg_student_id})，重新抓取")

    print(f"[{T()}] [*] 尝试从 Reqable 抓包获取 Token...")
    capture_dir = find_reqable_capture_dir()
    if not capture_dir:
        print(f"[{T()}] [X] 找不到 Reqable 抓包目录")
        print(f"[{T()}] [X] 请确保：1. 打开 Reqable  2. 打开微信一点通  3. 小程序加载完成")
        return None
    token = fetch_token_from_reqable(capture_dir)
    if not token:
        print(f"[{T()}] [X] 未从抓包中找到有效 Token")
        print(f"[{T()}] [X] 请确保一点通小程序已成功加载")
        return None
    payload = decode_jwt_payload(token)
    if payload:
        token_uid = str(payload.get("userId", ""))
        if token_uid != cfg_student_id:
            print(f"[{T()}] [!] 警告: 抓到的 Token 用户({token_uid}) 与配置学号({cfg_student_id}) 不一致")
            print(f"[{T()}] [!] 请确保一点通登录的是正确的账号")
    print(f"[{T()}] [*] 抓包获取 Token 成功: {token[:40]}...")
    config["token"] = token
    with open('user_data.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    return token


# ====== 抢座核心 ======
def make_session():
    s = requests.Session()
    s.verify = False
    s.proxies.update({"http": None, "https": None})
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0, pool_block=False)
    s.mount('https://', adapter)
    return s


def warm_up(session):
    """提前建立 SSL 连接"""
    try:
        import socket, ssl
        sock = socket.create_connection(("tsg77.sdust.edu.cn", 443), timeout=3)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        ssock = context.wrap_socket(sock, server_hostname="tsg77.sdust.edu.cn")
        ssock.close()
    except Exception:
        pass


def do_book(session, token, user_id, raw_payload, request_timeout):
    yStr, ts = generate_yStr(raw_payload.copy())
    headers = {
        "Host": "tsg77.sdust.edu.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20.1781",
        "UserId": user_id,
        "Token": token,
        "Content-Type": "application/json",
        "OperateTime": str(ts),
        "xweb_xhr": "1",
        "Connection": "close"
    }
    try:
        r = session.post("https://tsg77.sdust.edu.cn/Order/OrderSeat",
                         headers=headers, json={"yStr": yStr},
                         timeout=request_timeout, verify=False)
        data = r.json()
        msg = data.get('message', '')
        code = data.get('code')
        ok = (code == 0) or (msg and "已有预约" in msg)
        return ok, msg
    except Exception as e:
        return False, str(e)


def extract_uid(token):
    payload = decode_jwt_payload(token)
    if payload:
        return payload.get("userId")
    return None


# ====== 精确计时器 ======
def sleep_until(target_timestamp):
    """busy-wait 精确到 ~1ms"""
    now = time.time()
    diff = target_timestamp - now
    if diff > 0.2:
        time.sleep(diff - 0.2)
    while time.time() < target_timestamp:
        pass


def is_cancel_pressed():
    if sys.platform == 'win32':
        import msvcrt
        return msvcrt.kbhit() and msvcrt.getch() in (b'\r', b'\n')

    import select
    if select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
        return True
    return False


def sleep_until_with_cancel(target_timestamp):
    while True:
        remaining = target_timestamp - time.time()
        if remaining <= 0.25:
            break
        if is_cancel_pressed():
            return False
        time.sleep(min(remaining - 0.2, 1))
    sleep_until(target_timestamp)
    return True


def fire_bullet(label, session, token, uid, raw_payload, request_timeout, planned_ts, snipe_ts):
    sleep_until(planned_ts)
    sent_at = time.time()
    ok, msg = do_book(session, token, uid, raw_payload, request_timeout)
    short_msg = msg[:40] + "..." if len(msg) > 40 else msg
    elapsed = sent_at - snipe_ts
    status = "OK" if ok else "X"
    print(f"[{T()}] [{status}] {label}({elapsed:+.3f}s): {short_msg}")
    return ok, msg


def get_snipe_datetime(target_date, snipe_time):
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    snipe_clock = datetime.strptime(snipe_time, "%H:%M:%S").time()
    return datetime.combine(target_day - timedelta(days=1), snipe_clock)


# ====== 入口 ======
config = ensure_config_defaults(load_config())
config = auto_fix_date(config)

SEAT_CODE = config["seat_code"]
ROOM_CODE = SEAT_CODE[:3]
SNIPE_TIME = config["snipe_time"]
TARGET_START = f"{config['target_date']}T{config['start_hour']}.000Z"
TARGET_END = f"{config['target_date']}T{config['end_hour']}.000Z"

# 策略参数
FIRST_BULLET_ADVANCE_MS = int(config.get("first_bullet_advance_ms", 800))
BULLET_GAP_MS = int(config.get("bullet_gap_ms", 150))
SNIPE_COOLDOWN = float(config.get("snipe_cooldown", 3))
SNIPE_INTERVAL = float(config.get("snipe_interval", 0.8))
SNIPE_MAX = int(config.get("snipe_max", 99999))
REQUEST_TIMEOUT = float(config.get("request_timeout", 2))

if __name__ == "__main__":
    snipe_dt = get_snipe_datetime(config["target_date"], SNIPE_TIME)
    snipe_ts = snipe_dt.timestamp()
    snipe_text = snipe_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"[{T()}] 目标: {SEAT_CODE} | 预约日期: {config['target_date']} | "
        f"时段: {config['start_hour']}-{config['end_hour']} | 开抢: {snipe_text} | "
        f"2发连射(提前{FIRST_BULLET_ADVANCE_MS}ms, 间隔{BULLET_GAP_MS}ms)"
    )

    # === Token ===
    token = auto_get_token(config)
    if not token:
        exit()
    uid = extract_uid(token)
    if not uid:
        print(f"[{T()}] [X] 无法提取用户ID")
        exit()
    print(f"[{T()}] Token就绪 | 用户: {uid}")

    # === 预约参数 ===
    raw = {"roomCode": ROOM_CODE, "seatCode": SEAT_CODE,
           "dtStart": TARGET_START, "dtEnd": TARGET_END, "remark": ""}

    # === 预热 ===
    sessions = [make_session(), make_session()]
    for s in sessions:
        warm_up(s)
    print(f"[{T()}] 连接预热完成")

    # === 倒计时到整点 ===
    now_ts = time.time()
    wait_seconds = snipe_ts - now_ts

    if wait_seconds < 0:
        print(f"[{T()}] [!] 开抢时间 {snipe_text} 已过，立即执行并进入捡漏策略")
        snipe_ts = now_ts
        wait_seconds = 0

    if wait_seconds > 0:
        print(f"[{T()}] 倒计时 {wait_seconds:.0f}s 至 {snipe_text}（回车可取消）...")
        first_fire_ts = snipe_ts - (FIRST_BULLET_ADVANCE_MS / 1000)
        if not sleep_until_with_cancel(first_fire_ts):
            print(f"[{T()}] [*] 已取消")
            exit()

    # === 开火！第一发（提前发出，让请求在服务器队列里排队）===
    print(f"[{T()}] 开抢！2发先后发射 (提前{FIRST_BULLET_ADVANCE_MS}ms)")
    done = False

    first_fire_ts = snipe_ts - (FIRST_BULLET_ADVANCE_MS / 1000)
    second_fire_ts = first_fire_ts + (BULLET_GAP_MS / 1000)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fire_bullet, "第一发", sessions[0], token, uid, raw, REQUEST_TIMEOUT, first_fire_ts, snipe_ts),
            executor.submit(fire_bullet, "第二发", sessions[1], token, uid, raw, REQUEST_TIMEOUT, second_fire_ts, snipe_ts),
        ]
        for future in as_completed(futures):
            ok, _ = future.result()
            if ok:
                done = True

    # === 捡漏 ===
    if not done:
        print(f"[{T()}] 冷静 {SNIPE_COOLDOWN}s 后进入捡漏...")
        time.sleep(SNIPE_COOLDOWN)

        print(f"[{T()}] [X] 进入捡漏模式 ({SNIPE_INTERVAL}s/次，上限{SNIPE_MAX}次，回车取消)...")
        for i in range(SNIPE_MAX):
            if is_cancel_pressed():
                print(f"[{T()}] [*] 已取消捡漏")
                break

            time.sleep(SNIPE_INTERVAL)
            ok, msg = do_book(sessions[0], token, uid, raw, REQUEST_TIMEOUT)
            short_msg = msg[:40] + "..." if len(msg) > 40 else msg
            status = "OK" if ok else "X"
            print(f"[{T()}] [{status}] 捡漏{i+1}: {short_msg}")
            if ok:
                done = True
                print(f"[{T()}] [OK] 捡漏成功！")
                break

    if done:
        print(f"[{T()}] [OK] 抢座成功！")
    else:
        print(f"[{T()}] [X] {SNIPE_MAX}次捡漏未成功，手动试试吧")
