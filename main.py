"""
main.py - 图书馆抢座（准点单发 + 捡漏策略）
"""

import sys
import time
import requests
import json
import os
import re
import urllib3
import atexit
import socket
import struct
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from crypto_core import generate_yStr
from requests.adapters import HTTPAdapter
from reqable_token import (
    decode_jwt_payload,
    find_reqable_capture_dir,
    format_exp,
    is_library_token_valid,
    jwt_exp,
    library_user_id,
    scan_reqable_for_token,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = 'user_data.json'
BEIJING_TZ = timezone(timedelta(hours=8))
TIME_OFFSET_SECONDS = 0.0


# ====== 工具函数 ======
def calibrated_time():
    return time.time() + TIME_OFFSET_SECONDS


def beijing_now():
    return datetime.fromtimestamp(calibrated_time(), BEIJING_TZ)


def format_beijing_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, BEIJING_TZ).strftime('%H:%M:%S.%f')[:-3]


def T():
    return beijing_now().strftime('%H:%M:%S.%f')[:-3]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "student_id": "202311100913",
            "password": "2017",
            "target_date": (beijing_now() + timedelta(days=1)).strftime('%Y-%m-%d'),
            "auto_update_target_date": True,
            "seat_code": "301011A",
            "snipe_time": "22:30:00",
            "start_hour": "08:00:00",
            "end_hour": "22:00:00",
            "fire_advance_ms": 0,
            "time_sync_enabled": True,
            "time_sync_servers": ["ntp.aliyun.com", "ntp.tencent.com", "ntp.ntsc.ac.cn", "time.windows.com"],
            "time_sync_timeout": 1.0,
            "time_sync_samples": 3,
            "snipe_cooldown": 3,
            "snipe_interval": 5.2,
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
        "fire_advance_ms": 0,
        "time_sync_enabled": True,
        "time_sync_servers": ["ntp.aliyun.com", "ntp.tencent.com", "ntp.ntsc.ac.cn", "time.windows.com"],
        "time_sync_timeout": 1.0,
        "time_sync_samples": 3,
        "snipe_cooldown": 3,
        "snipe_interval": 5.2,
        "snipe_max": 99999,
        "request_timeout": 2
    }
    changed = False
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
            changed = True
    for legacy_key in ("first_bullet_advance_ms", "bullet_gap_ms"):
        if legacy_key in config:
            del config[legacy_key]
            changed = True
    if float(config.get("snipe_interval", 5.2)) < 5.2:
        config["snipe_interval"] = 5.2
        changed = True
    if changed:
        save_config(config)
    return config


def auto_fix_date(config):
    if not config.get("auto_update_target_date", True):
        print(f"[{T()}] [*] 自动更新预约日期已关闭，预约日期按配置: {config.get('target_date')}")
        return config

    tomorrow = (beijing_now() + timedelta(days=1)).strftime('%Y-%m-%d')
    if config.get("target_date", "") != tomorrow:
        print(f"[{T()}] [*] 日期: {config.get('target_date')} -> {tomorrow}")
        config["target_date"] = tomorrow
        save_config(config)
    return config


# ====== 北京时间校准 ======
def query_ntp_offset(server, timeout):
    ntp_epoch_delta = 2208988800
    transmit_time = time.time() + ntp_epoch_delta
    transmit_seconds = int(transmit_time)
    transmit_fraction = int((transmit_time - transmit_seconds) * 2 ** 32)
    packet = bytearray(48)
    packet[0] = 0x1b
    struct.pack_into("!II", packet, 40, transmit_seconds, transmit_fraction)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t0 = time.time()
        sock.sendto(bytes(packet), (server, 123))
        data, _ = sock.recvfrom(48)
        t3 = time.time()
    if len(data) < 48:
        raise ValueError("NTP response too short")
    values = struct.unpack("!12I", data[:48])
    originate = values[6] + values[7] / 2 ** 32 - ntp_epoch_delta
    receive = values[8] + values[9] / 2 ** 32 - ntp_epoch_delta
    transmit = values[10] + values[11] / 2 ** 32 - ntp_epoch_delta
    if originate <= 0:
        originate = t0
    delay = (t3 - t0) - (transmit - receive)
    offset = ((receive - originate) + (transmit - t3)) / 2
    return offset, max(0, delay)


def query_http_date_offset(url, timeout):
    started = time.time()
    response = requests.head(url, timeout=timeout, verify=False, allow_redirects=False)
    ended = time.time()
    date_header = response.headers.get("Date")
    if not date_header:
        raise ValueError("missing Date header")
    server_dt = parsedate_to_datetime(date_header)
    server_ts = server_dt.timestamp()
    midpoint = (started + ended) / 2
    return server_ts - midpoint, ended - started


def calibrate_beijing_time(config):
    global TIME_OFFSET_SECONDS
    if not config.get("time_sync_enabled", True):
        print(f"[{T()}] [*] 北京时间校准已关闭，使用本机时间")
        return 0.0

    timeout = float(config.get("time_sync_timeout", 1.0))
    samples_per_server = max(1, int(config.get("time_sync_samples", 3)))
    servers = config.get("time_sync_servers") or []
    candidates = []

    print(f"[{T()}] [*] 正在校准北京时间...")
    for server in servers:
        for _ in range(samples_per_server):
            try:
                offset, delay = query_ntp_offset(server, timeout)
                candidates.append(("NTP", server, offset, delay))
            except Exception:
                continue

    if not candidates:
        for url in ("https://www.baidu.com/", "https://www.qq.com/", "https://www.microsoft.com/"):
            try:
                offset, delay = query_http_date_offset(url, timeout)
                candidates.append(("HTTP", url, offset, delay))
            except Exception:
                continue

    if not candidates:
        print(f"[{T()}] [!] 北京时间校准失败，继续使用本机时间")
        return 0.0

    method, source, offset, delay = min(candidates, key=lambda item: item[3])
    TIME_OFFSET_SECONDS = offset
    print(
        f"[{T()}] [*] 北京时间校准完成: {method} {source} | "
        f"本机偏差 {offset * 1000:+.1f}ms | 延迟 {delay * 1000:.1f}ms"
    )
    return offset


# ====== Reqable 抓包 Token 提取 ======
def auto_get_token(config):
    existing = config.get("token", "")
    cfg_student_id = str(config.get("student_id", ""))

    if is_library_token_valid(existing, expected_user_id=cfg_student_id):
        payload = decode_jwt_payload(existing)
        exp = jwt_exp(payload) if payload else 0
        print(f"[{T()}] [*] 现有 Token 有效且账号匹配，复用 (exp: {format_exp(exp)})")
        return existing

    if existing:
        payload = decode_jwt_payload(existing)
        if payload:
            token_uid = library_user_id(payload)
            exp = jwt_exp(payload)
            if token_uid and token_uid != cfg_student_id:
                print(f"[{T()}] [*] 账号已变更 ({token_uid} -> {cfg_student_id})，重新抓取")
            elif token_uid:
                print(f"[{T()}] [!] 现有 Token 已过期或即将过期 (exp: {format_exp(exp)})")
            else:
                print(f"[{T()}] [!] 现有 Token 不是图书馆 Token，重新抓取")
        else:
            print(f"[{T()}] [!] 现有 Token 无法解析，重新抓取")

    print(f"[{T()}] [*] 尝试从 Reqable 抓包获取 Token...")
    capture_dir = find_reqable_capture_dir()
    if not capture_dir:
        print(f"[{T()}] [X] 找不到 Reqable 抓包目录")
        print(f"[{T()}] [X] 请确保：1. 打开 Reqable  2. 打开微信一点通  3. 小程序加载完成")
        return None

    scan_result = scan_reqable_for_token(capture_dir, expected_user_id=cfg_student_id)
    print(
        f"[{T()}] [*] Reqable扫描: 文件 {scan_result['scanned']}/{scan_result['total_files']} | "
        f"候选 {scan_result['token_candidates']} | 图书馆JWT {scan_result['library_candidates']}"
    )

    token = scan_result.get("token")
    if not token:
        print(f"[{T()}] [X] 未从抓包中找到有效的图书馆 Token")
        best_expired = scan_result.get("best_expired")
        if best_expired:
            print(f"[{T()}] [X] 最近匹配到的图书馆 Token 已过期: {format_exp(best_expired['exp'])}")
            print(f"[{T()}] [X] 来源文件: {best_expired['source']}")
        if scan_result.get("mismatched_library_tokens"):
            print(f"[{T()}] [X] 发现其他账号的图书馆 Token: {scan_result['mismatched_library_tokens']} 个")
        if scan_result.get("other_jwts"):
            print(f"[{T()}] [*] 已忽略非图书馆 JWT: {scan_result['other_jwts']} 个")
        print(f"[{T()}] [X] 请重新打开一点通并确认已经重新登录，再运行脚本")
        return None

    payload = decode_jwt_payload(token)
    if payload:
        token_uid = library_user_id(payload)
        if token_uid != cfg_student_id:
            print(f"[{T()}] [!] 警告: 抓到的 Token 用户({token_uid}) 与配置学号({cfg_student_id}) 不一致")
            print(f"[{T()}] [!] 请确保一点通登录的是正确的账号")
    print(f"[{T()}] [*] 抓包获取 Token 成功: {token[:40]}... (exp: {format_exp(scan_result.get('exp', 0))})")
    config["token"] = token
    with open('user_data.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    return token


# ====== 抢座核心 ======
def make_session():
    s = requests.Session()
    s.verify = False
    s.trust_env = False
    s.proxies.update({"http": None, "https": None})
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0, pool_block=False)
    s.mount('https://', adapter)
    return s


def warm_up(session):
    """提前建立同一个 Session 的 HTTPS 连接"""
    try:
        session.get("https://tsg77.sdust.edu.cn/", timeout=3, verify=False)
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
        "Connection": "keep-alive"
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
def enable_high_resolution_timer():
    """Windows 下提高 sleep 精度，失败不影响主流程。"""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        winmm = ctypes.WinDLL('winmm')
        if winmm.timeBeginPeriod(1) == 0:
            atexit.register(lambda: winmm.timeEndPeriod(1))
    except Exception:
        pass


def make_deadline(target_timestamp):
    return time.perf_counter() + max(0, target_timestamp - calibrated_time())


def sleep_until_deadline(deadline):
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.25:
            time.sleep(remaining - 0.2)
        elif remaining > 0.02:
            time.sleep(remaining / 2)
        else:
            pass


def sleep_until(target_timestamp):
    """按本地墙钟时间触发，最后一小段用 perf_counter 抗系统时间抖动。"""
    sleep_until_deadline(make_deadline(target_timestamp))


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
    deadline = make_deadline(target_timestamp)
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0.25:
            break
        if is_cancel_pressed():
            return False
        time.sleep(min(remaining - 0.2, 1))
    sleep_until_deadline(deadline)
    return True


def sleep_seconds_with_cancel(seconds):
    target = time.perf_counter() + max(0, seconds)
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return True
        if is_cancel_pressed():
            return False
        time.sleep(min(remaining, 0.2))


def parse_retry_seconds(msg):
    match = re.search(r'(\d+(?:\.\d+)?)\s*秒后', msg or '')
    if match:
        return float(match.group(1))
    return None


def calc_next_retry_wait(msg, fallback_interval):
    retry_seconds = parse_retry_seconds(msg)
    if retry_seconds is None:
        return fallback_interval
    return max(fallback_interval, retry_seconds + 0.3)


def fire_once(label, session, token, uid, raw_payload, request_timeout, planned_ts, snipe_ts):
    sleep_until(planned_ts)
    sent_at = calibrated_time()
    started = time.perf_counter()
    ok, msg = do_book(session, token, uid, raw_payload, request_timeout)
    latency = time.perf_counter() - started
    short_msg = msg[:40] + "..." if len(msg) > 40 else msg
    elapsed = sent_at - snipe_ts
    status = "OK" if ok else "X"
    sent_text = format_beijing_timestamp(sent_at)
    print(f"[{T()}] [{status}] {label}: 发出 {sent_text} ({elapsed:+.3f}s), 响应 {latency:.3f}s | {short_msg}")
    return ok, msg


def get_snipe_datetime(target_date, snipe_time):
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    snipe_clock = datetime.strptime(snipe_time, "%H:%M:%S").time()
    return datetime.combine(target_day - timedelta(days=1), snipe_clock, tzinfo=BEIJING_TZ)


if __name__ == "__main__":
    enable_high_resolution_timer()

    config = ensure_config_defaults(load_config())
    calibrate_beijing_time(config)
    config = auto_fix_date(config)

    SEAT_CODE = config["seat_code"]
    ROOM_CODE = SEAT_CODE[:3]
    SNIPE_TIME = config["snipe_time"]
    TARGET_START = f"{config['target_date']}T{config['start_hour']}.000Z"
    TARGET_END = f"{config['target_date']}T{config['end_hour']}.000Z"

    FIRE_ADVANCE_MS = int(config.get("fire_advance_ms", 0))
    SNIPE_COOLDOWN = float(config.get("snipe_cooldown", 3))
    SNIPE_INTERVAL = max(5.2, float(config.get("snipe_interval", 5.2)))
    SNIPE_MAX = int(config.get("snipe_max", 99999))
    REQUEST_TIMEOUT = float(config.get("request_timeout", 2))

    snipe_dt = get_snipe_datetime(config["target_date"], SNIPE_TIME)
    snipe_ts = snipe_dt.timestamp()
    snipe_text = snipe_dt.strftime("%Y-%m-%d %H:%M:%S")
    fire_ts = snipe_ts - (FIRE_ADVANCE_MS / 1000)

    print(
        f"[{T()}] 目标: {SEAT_CODE} | 预约日期: {config['target_date']} | "
        f"时段: {config['start_hour']}-{config['end_hour']} | 开抢: {snipe_text} | "
        f"准点单发(提前{FIRE_ADVANCE_MS}ms)"
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
    session = make_session()
    warm_up(session)
    print(f"[{T()}] 连接预热完成")

    # === 倒计时到整点 ===
    now_ts = calibrated_time()
    wait_seconds = snipe_ts - now_ts

    if wait_seconds < 0:
        print(f"[{T()}] [!] 开抢时间 {snipe_text} 已过，立即执行并进入捡漏策略")
        snipe_ts = now_ts
        fire_ts = now_ts
        wait_seconds = 0

    if wait_seconds > 0:
        print(f"[{T()}] 倒计时 {wait_seconds:.0f}s 至 {snipe_text}（回车可取消）...")
        if not sleep_until_with_cancel(fire_ts):
            print(f"[{T()}] [*] 已取消")
            exit()

    # === 准点单发 ===
    print(f"[{T()}] 开抢！准点单发")
    done = False
    ok, last_msg = fire_once("首发", session, token, uid, raw, REQUEST_TIMEOUT, fire_ts, snipe_ts)
    if ok:
        done = True

    # === 捡漏 ===
    if not done:
        wait_after_first = max(SNIPE_COOLDOWN, calc_next_retry_wait(last_msg, SNIPE_INTERVAL))
        print(f"[{T()}] 冷静 {wait_after_first:.1f}s 后进入捡漏...")
        if not sleep_seconds_with_cancel(wait_after_first):
            print(f"[{T()}] [*] 已取消")
            exit()

        print(f"[{T()}] [X] 进入捡漏模式 ({SNIPE_INTERVAL}s/次，上限{SNIPE_MAX}次，回车取消)...")
        next_wait = 0
        for i in range(SNIPE_MAX):
            if is_cancel_pressed():
                print(f"[{T()}] [*] 已取消捡漏")
                break

            if next_wait > 0 and not sleep_seconds_with_cancel(next_wait):
                print(f"[{T()}] [*] 已取消捡漏")
                break

            sent_at = calibrated_time()
            started = time.perf_counter()
            ok, msg = do_book(session, token, uid, raw, REQUEST_TIMEOUT)
            latency = time.perf_counter() - started
            short_msg = msg[:40] + "..." if len(msg) > 40 else msg
            status = "OK" if ok else "X"
            sent_text = format_beijing_timestamp(sent_at)
            print(f"[{T()}] [{status}] 捡漏{i+1}: 发出 {sent_text}, 响应 {latency:.3f}s | {short_msg}")
            if ok:
                done = True
                print(f"[{T()}] [OK] 捡漏成功！")
                break
            next_wait = calc_next_retry_wait(msg, SNIPE_INTERVAL)
            if next_wait > SNIPE_INTERVAL:
                print(f"[{T()}] [*] 触发频控提示，下次捡漏等待 {next_wait:.1f}s")

    if done:
        print(f"[{T()}] [OK] 抢座成功！")
    else:
        print(f"[{T()}] [X] {SNIPE_MAX}次捡漏未成功，手动试试吧")
