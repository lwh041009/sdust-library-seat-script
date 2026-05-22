"""
token_refresher.py - Token 自动获取工具（通用版）

直接从 Reqable 抓包目录提取最新 Token，不依赖任何写死的路径。

使用方法：
  1. 打开 Reqable 和 微信一点通小程序
  2. 运行 python token_refresher.py
  3. 自动从 Reqable 抓包中提取 Token，更新 user_data.json

换电脑也能用 —— 自动检测 Reqable 的安装路径和抓包目录。
"""

import os
import sys
import json
import time
import re
import gzip
import base64
from datetime import datetime


# ====== 自动获取脚本所在目录 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_PATH = os.path.join(SCRIPT_DIR, 'user_data.json')


def find_reqable_capture_dir():
    """自动查找 Reqable 的抓包目录（支持多台电脑）"""
    # 常见 Reqable capture 目录可能的位置
    candidates = []

    # Windows: %APPDATA%/Reqable/capture
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        candidates.append(os.path.join(appdata, 'Reqable', 'capture'))

    # 也检查 LOCALAPPDATA
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        candidates.append(os.path.join(localappdata, 'Reqable', 'capture'))

    # 扫描 %USERPROFILE% 下的 AppData 目录
    userprofile = os.environ.get('USERPROFILE', '')
    if userprofile:
        for sub in ['AppData', 'AppData/Roaming', 'AppData/Local']:
            candidates.append(os.path.join(userprofile, sub, 'Reqable', 'capture'))

    # 首次尝试时，记录哪些路径实际存在
    for path in candidates:
        if os.path.isdir(path):
            return path

    # 如果上面都没找到，做一次广度搜索
    # 检查常见的驱动器
    for drive in ['C:', 'D:', 'E:']:
        base = os.path.join(drive + os.sep, 'Users')
        if os.path.isdir(base):
            for username in os.listdir(base):
                for sub in [r'AppData\Roaming', r'AppData\Local']:
                    p = os.path.join(base, username, sub, 'Reqable', 'capture')
                    if os.path.isdir(p):
                        return p

    return None


def decode_body(data):
    """尝试解码响应体（处理 gzip 压缩）"""
    try:
        # 纯 JSON
        text = data.decode('utf-8')
        return text
    except UnicodeDecodeError:
        pass

    try:
        # gzip 压缩
        decompressed = gzip.decompress(data)
        text = decompressed.decode('utf-8')
        return text
    except Exception:
        pass

    return None


def extract_token_from_json(text):
    """从 JSON 文本中提取 token 字段"""
    try:
        obj = json.loads(text)
        # token 可能在不同的层级
        for key in ['token', 'access_token', 'accessToken', 'Token']:
            val = obj.get(key)
            if val and len(str(val)) > 20:
                return val
            # 深入 data / result / biz_data
            if 'data' in obj and isinstance(obj['data'], dict):
                val = obj['data'].get(key)
                if val and len(str(val)) > 20:
                    return val
            if 'result' in obj and isinstance(obj['result'], dict):
                val = obj['result'].get(key)
                if val and len(str(val)) > 20:
                    return val
    except Exception:
        pass

    # 如果 JSON 解析失败，用正则找 token 字段
    # 有的接口返回 biz_data 里有 token
    match = re.search(r'"token"\s*:\s*"([^"]{20,})"', text)
    if match:
        return match.group(1)

    return None


def scan_latest_token(capture_dir):
    """扫描 Reqable 抓包目录，返回最新的 Token 和来源文件名"""
    print(f"  扫描抓包目录: {capture_dir}")

    # 获取所有 res-raw-body 文件
    body_files = []
    for f in os.listdir(capture_dir):
        if f.endswith('.reqable') and 'res-raw-body' in f:
            fp = os.path.join(capture_dir, f)
            body_files.append((os.path.getmtime(fp), fp, f))

    if not body_files:
        print(f"  ❌ 抓包目录中没有响应体文件")
        return None

    body_files.sort(reverse=True)  # 最新的排前面

    print(f"  找到 {len(body_files)} 个响应体文件")

    # 只扫描最新的 200 个文件（避免卡死）
    scan_limit = min(200, len(body_files))
    print(f"  扫描最近的 {scan_limit} 个文件...")

    found_token = None
    found_file = None

    for mtime, fp, fname in body_files[:scan_limit]:
        size = os.path.getsize(fp)
        if size == 0:
            continue

        try:
            data = open(fp, 'rb').read()
            text = decode_body(data)
            if text is None:
                continue

            token = extract_token_from_json(text)
            if token:
                found_token = token
                found_file = fname
                tm = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n  ✅ 发现 Token! 文件: {fname} ({tm})")
                print(f"  Token: {token[:50]}...")
                break

            # 也检查是否包含 JWT
            jwt_match = re.search(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', text)
            if jwt_match:
                found_token = jwt_match.group(0)
                found_file = fname
                tm = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n  ✅ 发现 JWT Token! 文件: {fname} ({tm})")
                print(f"  Token: {found_token[:50]}...")
                break

        except Exception:
            continue

    return found_token


def decode_jwt_payload(token):
    """解码 JWT payload"""
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


def is_token_valid(token):
    """检查 Token 是否有效（剩余时间 > 5 分钟）"""
    if not token or len(token) < 20:
        return False
    payload = decode_jwt_payload(token)
    if payload:
        now = int(time.time())
        exp = payload.get('exp', 0)
        return now < exp - 300
    return True  # 不是 JWT 就默认有效


def print_token_info(token, label="当前 Token"):
    """打印 Token 信息"""
    if not token:
        print(f"  {label}: (空)")
        return
    payload = decode_jwt_payload(token)
    if payload:
        user_id = payload.get('userId', '?')
        exp = payload.get('exp', 0)
        exp_str = datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S') if exp else '?'
        delta = exp - int(time.time()) if exp else 0
        status = "✅ 有效" if delta > 0 else "❌ 已过期"
        print(f"  {label}: {status}")
        print(f"  用户ID: {user_id}")
        print(f"  过期时间: {exp_str} (剩余 {max(0, delta//3600)}h{max(0, (delta%3600)//60)}m)")
    else:
        print(f"  {label}: {token[:50]}...")


def main():
    print(f"{'='*60}")
    print(f" 🪑 图书馆抢座 - Token 自动获取工具")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 脚本目录: {SCRIPT_DIR}")
    print(f"{'='*60}")

    # 1. 检查 user_data.json
    if not os.path.exists(USER_DATA_PATH):
        print(f"❌ 找不到 {USER_DATA_PATH}")
        return

    # 2. 检查现有 Token
    with open(USER_DATA_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    current_token = cfg.get('token', '')

    if is_token_valid(current_token):
        print_token_info(current_token, "当前 Token")
        print(f"\n  Token 有效，无需刷新")
        print(f"  如需强制刷新，请先删除 user_data.json 中的 token 字段")
        print(f"  然后重新打开 一点通 小程序抓取新 Token")
        return

    # 3. 已有的 Token 过期/无效，从 Reqable 抓包提取
    print(f"\n  ── 当前 Token 无效，尝试从 Reqable 抓包提取 ──")

    capture_dir = find_reqable_capture_dir()

    if not capture_dir:
        print(f"  ❌ 找不到 Reqable 抓包目录")
        print(f"  ── 请按以下步骤操作 ──")
        print(f"  1. 打开 Reqable (确保 HTTPS 抓包已开启)")
        print(f"  2. 打开微信 → 一点通 小程序")
        print(f"  3. 小程序加载完成后，再次运行本脚本")
        return

    print(f"  Reqable 抓包目录: {capture_dir}")

    new_token = scan_latest_token(capture_dir)

    if new_token:
        print(f"\n  ✅ Token 获取成功!")
        cfg['token'] = new_token
        with open(USER_DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        print(f"  Token 已保存到 user_data.json")

        print_token_info(new_token, "新 Token")
        print(f"{'='*60}")

        # 询问是否启动抢座
        print(f"\n  Token 获取完成。你可以运行 main.py 开始抢座")
    else:
        print(f"\n  ❌ 未从抓包中找到 Token")
        print(f"  ── 原因排查 ──")
        print(f"  1. Reqable 是否已打开且 HTTPS 抓包已启用?")
        print(f"  2. 微信一点通小程序是否已成功加载?")
        print(f"  3. 如果小程序刚打开，等几秒再试")
        print(f"  ── 手动方案 ──")
        print(f"  打开一点通小程序后，在 Reqable 中搜索 token")
        print(f"  找到后复制到 user_data.json 的 token 字段")


if __name__ == '__main__':
    main()
