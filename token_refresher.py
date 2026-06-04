"""
token_refresher.py - Token 自动获取工具（通用版）

直接从 Reqable 抓包目录提取最新 Token，不依赖任何写死的路径。

使用方法：
  1. 打开 Reqable 和 微信一点通小程序
  2. 运行 python token_refresher.py
  3. 自动从 Reqable 抓包中提取 Token，更新 user_data.json

换电脑也能用 - 自动检测 Reqable 的安装路径和抓包目录。
"""

import os
import json
import time
from datetime import datetime

from reqable_token import (
    decode_jwt_payload,
    find_reqable_capture_dir,
    format_exp,
    is_library_token_valid,
    jwt_exp,
    library_user_id,
    scan_reqable_for_token,
)


# ====== 自动获取脚本所在目录 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_PATH = os.path.join(SCRIPT_DIR, 'user_data.json')


def scan_latest_token(capture_dir, expected_user_id=None):
    """扫描 Reqable 抓包目录，返回最新的图书馆 Token 和扫描结果"""
    print(f"  扫描抓包目录: {capture_dir}")

    result = scan_reqable_for_token(capture_dir, expected_user_id=expected_user_id)
    print(f"  抓包文件总数: {result['total_files']}")
    print(f"  已扫描最近: {result['scanned']} 个文件")
    print(f"  Token 候选: {result['token_candidates']} 个")
    print(f"  图书馆 JWT 候选: {result['library_candidates']} 个")

    if result["token"]:
        print(f"\n  [OK] 发现有效 Token! 文件: {result['source']}")
        print(f"  过期时间: {format_exp(result['exp'])}")
        print(f"  Token: {result['token'][:50]}...")
        return result["token"], result

    return None, result


def is_token_valid(token):
    """检查 Token 是否有效（剩余时间 > 5 分钟）"""
    return is_library_token_valid(token)


def print_token_info(token, label="当前 Token"):
    """打印 Token 信息"""
    if not token:
        print(f"  {label}: (空)")
        return
    payload = decode_jwt_payload(token)
    if payload:
        user_id = library_user_id(payload) or '?'
        exp = jwt_exp(payload)
        exp_str = datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S') if exp else '?'
        delta = exp - int(time.time()) if exp else 0
        status = "[OK] 有效" if delta > 0 else "[X] 已过期"
        print(f"  {label}: {status}")
        print(f"  用户ID: {user_id}")
        print(f"  过期时间: {exp_str} (剩余 {max(0, delta//3600)}h{max(0, (delta%3600)//60)}m)")
    else:
        print(f"  {label}: {token[:50]}...")


def main():
    print(f"{'='*60}")
    print(f" 图书馆抢座 - Token 自动获取工具")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 脚本目录: {SCRIPT_DIR}")
    print(f"{'='*60}")

    # 1. 检查 user_data.json
    if not os.path.exists(USER_DATA_PATH):
        print(f"[X] 找不到 {USER_DATA_PATH}")
        return

    # 2. 检查现有 Token
    with open(USER_DATA_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    current_token = cfg.get('token', '')
    student_id = str(cfg.get('student_id', ''))

    if is_library_token_valid(current_token, expected_user_id=student_id):
        print_token_info(current_token, "当前 Token")
        print(f"\n  Token 有效，无需刷新")
        print(f"  如需强制刷新，请先删除 user_data.json 中的 token 字段")
        print(f"  然后重新打开 一点通 小程序抓取新 Token")
        return

    # 3. 已有的 Token 过期/无效，从 Reqable 抓包提取
    print(f"\n  -- 当前 Token 无效，尝试从 Reqable 抓包提取 --")

    capture_dir = find_reqable_capture_dir()

    if not capture_dir:
        print(f"  [X] 找不到 Reqable 抓包目录")
        print(f"  -- 请按以下步骤操作 --")
        print(f"  1. 打开 Reqable (确保 HTTPS 抓包已开启)")
        print(f"  2. 打开微信 -> 一点通 小程序")
        print(f"  3. 小程序加载完成后，再次运行本脚本")
        return

    print(f"  Reqable 抓包目录: {capture_dir}")

    new_token, scan_result = scan_latest_token(capture_dir, expected_user_id=student_id)

    if new_token:
        print(f"\n  [OK] Token 获取成功!")
        cfg['token'] = new_token
        with open(USER_DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        print(f"  Token 已保存到 user_data.json")

        print_token_info(new_token, "新 Token")
        print(f"{'='*60}")

        # 询问是否启动抢座
        print(f"\n  Token 获取完成。你可以运行 main.py 开始抢座")
    else:
        print(f"\n  [X] 未从抓包中找到有效的图书馆 Token")
        best_expired = scan_result.get("best_expired")
        if best_expired:
            print(f"  最近匹配到的图书馆 Token 已过期: {format_exp(best_expired['exp'])}")
            print(f"  来源文件: {best_expired['source']}")
        if scan_result.get("mismatched_library_tokens"):
            print(f"  发现 {scan_result['mismatched_library_tokens']} 个其他账号的图书馆 Token")
        if scan_result.get("other_jwts"):
            print(f"  忽略 {scan_result['other_jwts']} 个非图书馆 JWT")
        print(f"  -- 原因排查 --")
        print(f"  1. Reqable 是否已打开且 HTTPS 抓包已启用?")
        print(f"  2. 微信一点通是否已经重新登录，而不是复用过期登录态?")
        print(f"  3. 在 Reqable 中搜索 tsg77.sdust.edu.cn，确认能看到新的 User/Login 或图书馆请求")
        print(f"  -- 手动方案 --")
        print(f"  在 Reqable 中搜索 tsg77.sdust.edu.cn，再找请求头 Token 或登录响应里的 token")
        print(f"  找到后复制到 user_data.json 的 token 字段")


if __name__ == '__main__':
    main()

