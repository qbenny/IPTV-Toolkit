import requests
import re
import random
from urllib.parse import urlparse
from Crypto.Cipher import DES

def _pad(text, block_size=8):
    return text + (block_size - len(text) % block_size) * chr(block_size - len(text) % block_size)

def login_dynamic(base_url, user_id, headers, stbid, mac, ip, des_key,
                  stb_type, stb_version, timeout=10):
    """
    DES 动态算密登录方式
    """
    session = requests.Session()
    
    # 步骤 1: 访问 AuthenticationURL 并处理重定向 host
    url1 = f"{base_url}/EPG/jsp/AuthenticationURL?UserID={user_id}&Action=Login"
    res1 = session.get(
        url1,
        headers={**headers, "X-Requested-With": "com.android.smart.terminal.iptv"},
        timeout=timeout
    )
    host = urlparse(res1.url).netloc
    if not host:
        host = urlparse(base_url).netloc
        
    final_base_url = f"http://{host}"

    # 步骤 2: 获取 EncryptToken
    url2 = f"{final_base_url}/EPG/jsp/authLoginHWCTC.jsp"
    res2 = session.post(
        url2,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": url1},
        data={"UserID": user_id, "VIP": ""},
        timeout=timeout
    )
    r_enc = re.search(r'EncryptToken \= \"(.+?)\";', res2.text)
    encrypt_token = r_enc.group(1) if r_enc else ""

    # 步骤 3: 动态算密并验证
    rand_str = ''.join(random.sample('123456789', 8))
    session_ref = f"{rand_str}${encrypt_token}${user_id}${stbid}${ip}${mac}$$CTC"
    
    # 算密
    cipher = DES.new(des_key.encode('utf-8'), DES.MODE_ECB)
    padded_ref = _pad(session_ref, DES.block_size)
    dynamic_auth = cipher.encrypt(padded_ref.encode('utf-8')).hex().upper()

    url3 = f"{final_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp"
    data3 = {
        'UserID': user_id,
        'SupportHD': '1',
        'Authenticator': dynamic_auth,
        'STBType': stb_type,
        'STBVersion': stb_version,
        'STBID': stbid,
        'userToken': encrypt_token,
        'mac': mac
    }
    res3 = session.post(
        url3,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": url2},
        data=data3,
        timeout=timeout
    )
    
    re_token = re.search(r'UserToken\" value\=\"(.+?)\"', res3.text, re.DOTALL)
    user_token = re_token.group(1) if re_token else ""
    
    if not user_token:
        raise ValueError("动态算密登录失败，未能获取有效的 UserToken")
        
    print(f"    >>> [成功] 动态算密登录成功！已获取全套通行凭证。")
    return session, final_base_url, user_token
