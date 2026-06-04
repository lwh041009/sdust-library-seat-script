"""
login.py - 自动登录获取 Token
"""
import time
import json
import base64
import requests
from crypto_core import encrypt_password


def try_login(student_id: str, password: str, timeout: int = 5) -> str | None:
    """
    登录图书馆系统，返回 Token

    尝试多个已知的登录端点，成功后返回 token 字符串，失败返回 None
    """
    headers = {
        "Host": "tsg77.sdust.edu.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20.1781",
        "Content-Type": "application/json",
        "xweb_xhr": "1",
    }

    encrypted_pwd = encrypt_password(password)

    # 尝试多个可能的登录接口
    login_urls = [
        "https://tsg77.sdust.edu.cn/User/Login",
        "https://tsg77.sdust.edu.cn/api/User/Login",
        "https://tsg77.sdust.edu.cn/Auth/Login",
    ]

    payloads = [
        {"userId": student_id, "password": encrypted_pwd},
        {"userId": student_id, "pwd": encrypted_pwd},
        {"username": student_id, "password": encrypted_pwd},
        {"account": student_id, "password": encrypted_pwd},
    ]

    for url in login_urls:
        for payload in payloads:
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout, proxies={"http": None, "https": None})
                data = resp.json()
                # 尝试多种返回格式中提取 token
                token = (
                    data.get("token")
                    or data.get("data", {}).get("token")
                    or data.get("result", {}).get("token")
                    or data.get("Token")
                )
                if token and len(token) > 20:
                    print(f"[登录] 成功获取 Token ({url})")
                    return token
            except Exception:
                continue

    print("[登录] 所有接口尝试均失败，请检查账号密码或网络")
    return None


def get_or_refresh_token(config: dict, force_refresh: bool = False) -> str | None:
    """
    获取有效 Token：现有 Token 未过期则复用，否则重新登录

    :param config: user_data.json 的配置字典
    :param force_refresh: 是否强制重新登录
    :return: token 字符串，失败返回 None
    """
    # 如果现有 Token 存在且不强制刷新，检查是否过期
    existing_token = config.get("token", "")
    if existing_token and not force_refresh:
        try:
            # 简单解码 JWT 看 exp
            parts = existing_token.split(".")
            if len(parts) == 3:
                payload_b64 = parts[1]
                # 补 base64 padding
                pad = 4 - len(payload_b64) % 4
                if pad != 4:
                    payload_b64 += "=" * pad
                import base64 as b64
                payload_json = b64.urlsafe_b64decode(payload_b64)
                payload = json.loads(payload_json)
                exp = payload.get("exp", 0)
                if exp > time.time() + 300:  # 剩余 >5 分钟算有效
                    print(f"[Token] 现有 Token 仍有效 (exp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))})")
                    return existing_token
                else:
                    print(f"[Token] 现有 Token 即将过期或已过期，重新登录")
        except Exception:
            print("[Token] 解码失败，重新登录")

    # 需要重新登录
    student_id = config.get("student_id", "")
    password = config.get("password", "")

    if not student_id or not password:
        print("[登录] 缺少账号密码，请在 user_data.json 中设置 student_id 和 password")
        return None

    new_token = try_login(student_id, password)
    if new_token:
        # 保存回配置文件
        config["token"] = new_token
        config_path = "user_data.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print(f"[Token] 已自动更新到 {config_path}")
        return new_token

    return None


# 单元测试
if __name__ == "__main__":
    # 测试用，读取配置
    config_path = "user_data.json"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    token = get_or_refresh_token(cfg, force_refresh=True)
    if token:
        print(f"Token: {token[:50]}...")
    else:
        print("获取 Token 失败")
