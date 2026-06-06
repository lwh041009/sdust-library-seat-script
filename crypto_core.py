# crypto_core.py
import time
import json
import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, AES
from Crypto.Util.Padding import pad

# ---------------- 配置区 ----------------
# 从小程序源码中提取的 RSA 公钥
RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAujYRr588K81myBk+meKCd3yp1MbFP421B0nfaRSpvHCNyN9ccT/tlUrPYysYtF7C8AySHDhEwbFIEaCmAs5r5oyNZBU86KvtkEn3gYXbeOmy8AFapsWBWtTowz5s1JJfFzP/4tbx45Y7ssGUhfxa7VBPz3DIPvLEb2D6YiNR8aTB0BUehT+KmBXt3PN/3pyxBK014whMvWeTouX5ffdDqsc2Zu7uVhYIPu3rFiGT3e1r38B1t+nQTF+u3PinKY3DLdkbphrCrdXNo6xivyyUknEBuDLEYFmH9XQj4ksY1ptnuOhR6+dTiLAvQF9toMs7XS1tsQhtZPtUi3YJUJPrdQIDAQAB
-----END PUBLIC KEY-----"""

# 从 app.js 中提取的 AES 密钥 (用于账号密码登录)
# 注意：AES 必须是 bytes 类型
AES_KEY = b"YDT64760YDT64760YDT64760YDT64760"  # 32位，AES-256
AES_IV = b"YDT60900YDT60900"  # 16位
RSA_KEY = RSA.importKey(RSA_PUBLIC_KEY)
RSA_CIPHER = PKCS1_v1_5.new(RSA_KEY)


# ----------------------------------------

def generate_yStr(payload_dict, timestamp=None):
    """
    核心加密逻辑：将请求体字典转换为带动态时间戳的 RSA 密文 (yStr)

    :param payload_dict: dict, 原始请求参数 (不包含 yOp)
    :return: tuple(str, int), 返回 (Base64密文字符串, 注入的秒级时间戳)
    """
    # 1. 动态注入时间戳 (防重放机制)
    current_time_sec = int(time.time() if timestamp is None else timestamp)
    payload_dict['yOp'] = current_time_sec

    # 2. 序列化为紧凑的 JSON 字符串
    json_str = json.dumps(payload_dict, separators=(',', ':'))

    # 3. 执行加密并进行 Base64 编码
    msg_bytes = json_str.encode('utf-8')
    encrypted_bytes = RSA_CIPHER.encrypt(msg_bytes)
    base64_str = base64.b64encode(encrypted_bytes).decode('utf-8')

    return base64_str, current_time_sec


def encrypt_password(password_str):
    """
    用于登录接口的 AES 密码加密

    :param password_str: str, 你的明文密码
    :return: str, 经过 AES-CBC 加密并 Base64 编码的密文
    """
    # 1. 创建 AES-CBC 加密对象
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)

    # 2. 对密码进行 PKCS7 填充 (AES 加密块必须是 16 的倍数)
    padded_password = pad(password_str.encode('utf-8'), AES.block_size)

    # 3. 加密并转为 Base64
    encrypted_bytes = cipher.encrypt(padded_password)
    encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')

    return encrypted_base64


# ==================== 底层单元测试 ====================
if __name__ == "__main__":
    print("------- 正在测试底层加密引擎 -------")

    # 1. 测试 RSA (预约接口加密)
    test_payload = {"test": "123"}
    yStr, ts = generate_yStr(test_payload)
    print(f"[RSA 测试] 时间戳: {ts}")
    print(f"[RSA 测试] yStr密文: {yStr[:50]}...")

    # 2. 测试 AES (登录接口密码加密)
    # 提示：切勿将真实密码写在公开平台的代码里！
    test_pwd = "my_secret_password"
    aes_cipher = encrypt_password(test_pwd)
    print(f"[AES 测试] 明文密码: {test_pwd}")
    print(f"[AES 测试] 加密结果: {aes_cipher}")

    print("------------------------------------")
