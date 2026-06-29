import logging
import re
import time
import ast
import json
import os
import socket
import sys
import subprocess
from urllib.parse import urlparse
import requests
from typing import Optional

def get_all_local_ips() -> list:
    """
    搜集本地所有网口配置的 IPv4 地址列表，兼容 Windows 与 Linux/OpenWrt
    """
    ips = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.check_output("ipconfig", shell=True).decode("gbk", errors="ignore")
            ips = re.findall(r"IPv4 地址[\.\s]*:\s*([0-9\.]+)", out)
            if not ips:
                ips = re.findall(r"IPv4 Address[\.\s]*:\s*([0-9\.]+)", out)
        else:
            out = subprocess.check_output("ip addr", shell=True).decode("utf-8", errors="ignore")
            ips = re.findall(r"inet\s+([0-9\.]+)/", out)
    except Exception:
        pass
    
    if not ips:
        try:
            hostname = socket.gethostname()
            _, _, ip_list = socket.gethostbyname_ex(hostname)
            ips.extend(ip_list)
        except Exception:
            pass
            
    cleaned_ips = []
    for ip in ips:
        if ip and ip.strip() and ip not in cleaned_ips and not ip.startswith("127."):
            cleaned_ips.append(ip.strip())
    return cleaned_ips

def get_iptv_local_ip(base_url: str) -> str:
    """
    动态获取机顶盒本地 IPTV IP。
    优先扫描本地所有网卡接口，寻找代表 IPTV 专网的 10.x.x.x 私网 IP 地址。
    如果没找到，则通过连接目标 EPG 网关的 UDP 端口，由操作系统路由表动态选定出网 IP。
    """
    try:
        local_ips = get_all_local_ips()
        
        # 1. 优先寻找 10.x.x.x 网段的真实 IPTV 网卡 IP
        for ip in local_ips:
            if ip.startswith("10."):
                return ip
                
        # 2. 回退机制：通过连接 EPG 服务器的 UDP Socket 检测操作系统路由出网口 IP
        parsed = urlparse(base_url)
        hostname = parsed.hostname or "218.71.130.66"
        port = parsed.port or 33200
        
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((socket.gethostbyname(hostname), port))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        logging.error("自动获取 IPTV 本地 IP 失败: %s，回退到空值", e)
        return ""

def load_stb_config() -> dict:
    """
    从本地 data/stb_config.json 读取机顶盒仿真认证参数，如果文件不存在则返回默认的参数。
    """
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    config_path = os.path.join(data_dir, "stb_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error("读取 stb_config.json 失败: %s", e)
    
    # 默认备用参数（占位符）
    return {
        "user_id": "",
        "stb_id": "",
        "mac_address": "",
        "ip_address": "",
        "base_url": "",
        "des_key": ""
    }

def parse_epg_json(text: str) -> dict:
    """
    用于解析 EPG 服务器非标准 JSON 格式 (例如单引号键值、被圆括号包裹等)
    """
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        cleaned = text.strip()
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = cleaned[1:-1].strip()
        return ast.literal_eval(cleaned)
    except Exception:
        return {}


# Attempt to import Cryptodome for DES
try:
    from Crypto.Cipher import DES
except ImportError:
    DES = None

# ==========================================
# 1. Configuration & State Classes
# ==========================================

class STBDeviceConfig:
    """
    静态设备配置类 (保存开机即固定不变的硬件和环境参数)
    """
    def __init__(
        self,
        user_id: str,
        stb_id: str,
        mac_address: str,
        ip_address: str,
        base_url: str,
        des_key: str = "00000000",
        stb_type: str = "EC6110T_zjzdx",
        stb_version: str = "19.2.0-LZJD03.B012",
        area_id: str = "304",
        user_group_id: str = "8",
        template_name: str = "gdhdpublic"
    ):
        self.user_id = user_id
        self.net_user_id = f"tv{user_id}@itv"
        self.stb_id = stb_id
        self.mac_address = mac_address  # 格式: "20:28:3E:AF:16:FC"
        if not ip_address:
            ip_address = get_iptv_local_ip(base_url)
            print(f">>> [STB Config] 动态探测并绑定出网口 IPTV IP: {ip_address}")
            logging.info("自动获取出网口 IPTV IP: %s", ip_address)
        else:
            print(f">>> [STB Config] 使用配置文件指定的静态 IP: {ip_address}")
        self.ip_address = ip_address
        self.base_url = base_url        # 初始网关地址，例如: http://218.71.130.66:33200
        self.des_key = des_key
        self.stb_type = stb_type
        self.stb_version = stb_version
        self.software_version = stb_version
        self.area_id = area_id
        self.user_group_id = user_group_id
        self.template_name = template_name
        
        # 统一请求头配置
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.0.3; zh-cn; EC6106V6U_pub_20_zjzdx Build/IML74K) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Safari/534.30 HuaWei;Resolution(PAL,720P,1080i)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh; q=1.0, en; q=0.5",
            "Connection": "keep-alive"
        }


class STBRuntimeState:
    """
    动态运行状态类 (保存整个生命周期中随交互改变的 Token、Cookie 和心跳时间戳)
    """
    def __init__(self):
        self.session: requests.Session = requests.Session()  # 自动管理 JSESSIONID Cookie
        self.epg_base_url: str = ""                         # 重定向后的真正 EPG 主机地址
        self.user_token: Optional[str] = None               # 阶段二验证成功后的正式通行 Token
        self.channels: list = []                            # 缓存获取到的频道列表数据库
        self.is_authenticated: bool = False                 # 认证通过标志
        
        # 心跳控制
        self.heartbeat_interval: int = 600                  # 心跳间隔，与真实机顶盒 TVMSHeartbitInterval 一致
        self.last_heartbeat_time: float = 0.0               # 上一次心跳成功的时间戳
        self.heartbeat_fail_count: int = 0                  # 连续心跳失败次数
        
        self.vis_base_url: Optional[str] = None             # VIS VOD 服务器地址，登录后从 configUrl.min.js 解析
        self.operator: Optional[str] = None                # 运营商: "telecom" 或 "unicom"，从 EPG 页面提取

    def update_heartbeat_timer(self):
        self.last_heartbeat_time = time.time()
        self.heartbeat_fail_count = 0

    def clear_auth_state(self):
        self.encrypt_token = None
        self.user_token = None
        self.is_authenticated = False
        self.session.cookies.clear()


# ==========================================
# 2. Simulator Logic
# ==========================================

class STBSimulator:
    """
    机顶盒模拟器主类
    """
    def __init__(self, config: STBDeviceConfig):
        self.config = config
        self.state = STBRuntimeState()
        
        # 配置日志输出格式
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger("STBSimulator")
        self.logger.info("机顶盒网络模拟器已就绪。设备账号: %s", self.config.user_id)

    def _log_request(self, method: str, url: str, response: requests.Response):
        self.logger.info(">>> 发送 %s 请求: %s", method, url)
        self.logger.info("<<< 收到响应: HTTP %d", response.status_code)
        snippet = response.text[:400].replace('\r', '').replace('\n', ' ')
        #self.logger.info("<<< 响应体片段: %s...", snippet)
        self.logger.info("-" * 60)

    def _pad(self, text: str, block_size: int = 8) -> bytes:
        pad_len = block_size - (len(text) % block_size)
        return (text + pad_len * chr(pad_len)).encode('utf-8')

    def _generate_auth_signature(self) -> str:
        """
        根据抓包动态算密逻辑，使用 DES-ECB 和 key="00000000" 计算明文摘要
        """
        if DES is None:
            raise ImportError("未检测到 Crypto.Cipher 加密模块！请安装 pycryptodome 库（pip install pycryptodome）以进行动态签名计算。")

        # 随机串
        rand_str = "99999"
        
        # 拼接格式: {rand_str}${encrypt_token}${user_id}${stbid}${ip}${mac}$$CTC
        session_ref = (
            f"{rand_str}$"
            f"{self.state.encrypt_token}$"
            f"{self.config.user_id}$"
            f"{self.config.stb_id}$"
            f"{self.config.ip_address}$"
            f"{self.config.mac_address}$$CTC"
        )
        
        # 填充和加密
        padded_data = self._pad(session_ref, DES.block_size)
        cipher = DES.new(self.config.des_key.encode('utf-8'), DES.MODE_ECB)
        encrypted_bytes = cipher.encrypt(padded_data)
        
        auth_signature = encrypted_bytes.hex().upper()
        self.logger.info("动态密文签名 (Authenticator) 已计算生成成功。")
        return auth_signature

    def _resolve_vis_domain(self) -> Optional[str]:
        """登录后从 configUrl.min.js 解析 VIS VOD 服务器地址。

        机顶盒架构：EPG 页面的 <script> 块中定义 var operator = "telecom"，
        然后加载 configUrl.min.js，其中根据 operator 选择 VIS 服务器：
          telecom → 115.233.200.60:58000
          unicom  → 124.160.41.2:8095
        VIS API 完整路径为 http://{visEpgIp}/epg/
        """
        operator = self.state.operator or "telecom"

        try:
            url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/js/configUrl.min.js"
            r = self.state.session.get(url, headers=self.config.headers, timeout=10)
            if r.status_code == 200:
                m = re.search(
                    r'visEpgIp\s*=\s*["\'][^"\']*["\'].*?\?\s*["\']([^"\']+)["\']\s*:\s*["\']([^"\']+)["\']',
                    r.text)
                if m:
                    unicom_ip = m.group(1)   # "124.160.41.2:8095"
                    telecom_ip = m.group(2)  # "115.233.200.60:58000"
                    vis_ip = unicom_ip if operator == "unicom" else telecom_ip
                    vis_base_url = f"http://{vis_ip}/epg/"
                    self.logger.info("VIS 服务器地址解析成功 (%s 线路): %s", operator, vis_base_url)
                    print(f">>> [VIS Domain] Resolved ({operator}): {vis_base_url}")
                    return vis_base_url
                else:
                    self.logger.warning("VIS 服务器地址正则匹配失败")
                    print(">>> [VIS Domain] Regex match failed in configUrl.min.js")
            else:
                self.logger.warning("configUrl.min.js 获取失败 HTTP %d", r.status_code)
                print(f">>> [VIS Domain] configUrl.min.js HTTP {r.status_code}")
        except Exception as e:
            self.logger.warning("VIS 服务器地址解析失败: %s", e)
            print(f">>> [VIS Domain] Resolution error: {e}")
        return None

    def login(self) -> bool:
        """
        执行完整的 IPTV 开机认证流
        """
        self.logger.info("========== 启动登录握手时序 ==========")
        try:
            # 1. 访问网关引导 (自动追踪可能存在的 302 重定向)
            url1 = f"{self.config.base_url}/EPG/jsp/AuthenticationURL?UserID={self.config.user_id}&Action=Login&FCCSupport=1"
            res1 = self.state.session.get(url1, headers=self.config.headers, timeout=10)
            self._log_request("GET", url1, res1)
            
            parsed_url = urlparse(res1.url)
            self.state.epg_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

            # 2. 访问 authLoginHWCTC.jsp 提取临时 EncryptToken
            url2 = f"{self.state.epg_base_url}/EPG/jsp/authLoginHWCTC.jsp?UserID={self.config.user_id}&SampleId="
            res2 = self.state.session.post(
                url2, 
                data={"UserID": self.config.user_id, "VIP": ""}, 
                headers={**self.config.headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": res1.url},
                timeout=10
            )
            self._log_request("POST", url2, res2)

            token_match = re.search(r'EncryptToken\s*=\s*["\'](.+?)["\']', res2.text)
            if not token_match:
                token_match = re.search(r'[\'"]userToken[\'"]\s*,\s*[\'"](.+?)[\'"]', res2.text)
                
            if not token_match:
                self.logger.error("响应正文中未提取到动态 EncryptToken 变量！登录中止。")
                return False

            self.state.encrypt_token = token_match.group(1)
            self.logger.info("第一阶段临时 Token (EncryptToken): %s", self.state.encrypt_token)

            # 2.5 从 authLoginHWCTC.jsp 响应中提取运营商类型
            op_match = re.search(r'var\s+operator\s*=\s*["\'](\w+)["\']', res2.text)
            if op_match:
                self.state.operator = op_match.group(1)
                self.logger.info("运营商类型: %s", self.state.operator)

            # 3. 动态算密并发送 ValidAuthentication 校验
            authenticator = self._generate_auth_signature()
            valid_payload = {
                "UserID": self.config.user_id,
                "Lang": "1",
                "SupportHD": "1",
                "NetUserID": self.config.net_user_id,
                "Authenticator": authenticator,
                "STBType": self.config.stb_type,
                "STBVersion": self.config.stb_version,
                "conntype": "4",
                "STBID": self.config.stb_id,
                "templateName": self.config.template_name,
                "areaId": self.config.area_id,
                "userToken": self.state.encrypt_token,
                "userGroupId": self.config.user_group_id,
                "productPackageId": "-1",
                "mac": self.config.mac_address,
                "UserField": "2",
                "SoftwareVersion": self.config.software_version,
                "IsSmartStb": "0",
                "desktopId": "",
                "stbmaker": "",
                "VIP": ""
            }

            url3 = f"{self.state.epg_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp"
            res3 = self.state.session.post(
                url3,
                data=valid_payload,
                headers={**self.config.headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": url2},
                timeout=10
            )
            self._log_request("POST", url3, res3)

            # 4. 解析正式的 UserToken
            final_token_match = re.search(r'[\'"]UserToken[\'"]\s*,\s*[\'"](.+?)[\'"]', res3.text)
            if not final_token_match:
                # 兼容性匹配 input 隐藏域
                final_token_match = re.search(r'name="UserToken"\s+value="(.+?)"', res3.text)

            if not final_token_match:
                self.logger.error("EPG 服务器响应失败或验证凭据错误，未获取到最终 UserToken。")
                return False

            self.state.user_token = final_token_match.group(1)
            self.state.is_authenticated = True
            self.state.update_heartbeat_timer()
            self.logger.info("========== 模拟机顶盒上线成功 ==========")
            self.logger.info("正式通行 Token (UserToken): %s", self.state.user_token)
            
            # 5. 解析 VIS VOD 服务器地址
            self.state.vis_base_url = self._resolve_vis_domain()
            if self.state.vis_base_url:
                self.logger.info("VIS VOD 服务器: %s", self.state.vis_base_url)
            else:
                self.logger.warning("VIS VOD 服务器地址未获取到")
            
            return True

        except Exception as e:
            self.logger.error("认证执行期间遭遇异常: %s", e, exc_info=True)
            self.state.clear_auth_state()
            return False

    def keep_alive(self):
        """
        心跳上报维持逻辑。
        真实机顶盒 GetHeartBit 响应体格式示例：
          [HeartBit]
          UserValid=true
          NextCallInterval=900
        本方法会解析 UserValid 字段：若为 false 则立即清除认证状态触发重登录。
        同时动态适配 NextCallInterval（服务器下发的推荐心跳间隔）。
        """
        if not self.state.is_authenticated:
            self.logger.warning("机顶盒当前处于离线状态，暂不发送心跳包。")
            return

        current_time = time.time()
        if current_time - self.state.last_heartbeat_time < self.state.heartbeat_interval:
            return

        self.logger.info("心跳时间窗口到达，上报机顶盒状态...")
        heartbeat_url = f"{self.state.epg_base_url}/EPG/jsp/GetHeartBit"
        params = {
            "UserStatus": "1",
            "ChannelVer": time.strftime("%Y%m%d%H%M%S"),
            "STBID": self.config.stb_id,
            "STBType": self.config.stb_type,
            "Version": self.config.software_version
        }

        try:
            res = self.state.session.get(
                heartbeat_url,
                params=params,
                headers=self.config.headers,
                timeout=10
            )
            self._log_request("GET (心跳包)", heartbeat_url, res)

            if res.status_code == 200:
                # 解析响应体中的 UserValid 和 NextCallInterval 字段
                user_valid_match = re.search(r'UserValid\s*=\s*(true|false)', res.text, re.IGNORECASE)
                if user_valid_match:
                    user_valid = user_valid_match.group(1).lower() == 'true'
                    if not user_valid:
                        self.logger.warning("服务器返回 UserValid=false！会话 Token 已失效，清除认证状态以触发重登录。")
                        self.state.clear_auth_state()
                        return

                interval_match = re.search(r'NextCallInterval\s*=\s*(\d+)', res.text)
                if interval_match:
                    server_interval = int(interval_match.group(1))
                    if server_interval > 0 and server_interval != self.state.heartbeat_interval:
                        self.logger.info("服务器下发推荐心跳间隔: %d 秒 (之前: %d 秒)，动态适配。",
                                         server_interval, self.state.heartbeat_interval)
                        self.state.heartbeat_interval = server_interval

                self.state.update_heartbeat_timer()
                self.logger.info("会话心跳刷新成功。状态维持中...")
            else:
                raise ValueError(f"HTTP {res.status_code}")

        except Exception as e:
            self.state.heartbeat_fail_count += 1
            self.logger.error("心跳发送失败: %s，失败计数: %d", e, self.state.heartbeat_fail_count)
            if self.state.heartbeat_fail_count >= 3:
                self.logger.error("心跳连续失败达 3 次！认定当前会话离线，清空动态 Token。")
                self.state.clear_auth_state()

    def get_channel_list(self) -> list:
        """
        获取机顶盒的直播频道列表
        该方法向 /EPG/jsp/getchannellistHWCTC.jsp 发送 POST 请求，
        拉取服务器返回的频道信息并解析出 IGMP 和 RTSP 地址。
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取频道列表。")
            return []

        self.logger.info("========== 开始拉取频道列表 ==========")
        stbid_sub = self.config.stb_id[6:12] if len(self.config.stb_id) >= 12 else "990060"
        
        payload = {
            "conntype": "4",
            "UserToken": self.state.user_token,
            "tempKey": "92FFB4697440F8091240BEEDBD935E9E",
            "stbid": stbid_sub,
            "SupportHD": "1",
            "UserID": self.config.user_id,
            "Lang": "1"
        }

        url = f"{self.state.epg_base_url}/EPG/jsp/getchannellistHWCTC.jsp"
        try:
            res = self.state.session.post(
                url,
                data=payload,
                headers={**self.config.headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": f"{self.state.epg_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp"},
                timeout=15
            )
            self._log_request("POST", url, res)

            if res.status_code != 200:
                self.logger.error("频道列表请求失败，HTTP 状态码: %d", res.status_code)
                return []

            channel_blocks = re.findall(r"Authentication\.CTCSetConfig\(\s*['\"]Channel['\"]\s*,\s*['\"](.+?)['\"]\s*\)", res.text)
            
            channels = []
            for block in channel_blocks:
                kv_pairs = re.findall(r'(\w+)="([^"]*)"', block)
                ch_info = {k: v for k, v in kv_pairs}
                
                if "ChannelName" in ch_info and "ChannelURL" in ch_info:
                    play_url_raw = ch_info.get("ChannelURL", "")
                    urls = play_url_raw.split('|')
                    multicast_url = ""
                    unicast_url = ""
                    for u in urls:
                        if u.startswith("igmp://"):
                            multicast_url = u
                        elif u.startswith("rtsp://") or u.startswith("http://"):
                            unicast_url = u
                            
                    channel_data = {
                        "channel_id": ch_info.get("ChannelID", ""),
                        "name": ch_info.get("ChannelName", ""),
                        "user_channel_id": ch_info.get("UserChannelID", ""),
                        "multicast_url": multicast_url,
                        "unicast_url": unicast_url,
                        "raw_url": play_url_raw
                    }
                    channels.append(channel_data)

            self.state.channels = channels
            self.logger.info("成功拉取并解析出 %d 个频道！", len(channels))
            return channels

        except Exception as e:
            self.logger.error("获取频道列表时遭遇异常: %s", e, exc_info=True)
            return []

    def get_play_url(self, name_or_id: str) -> Optional[dict]:
        """
        根据频道名称或频道 ID 查询播放地址 (组播 IGMP 与 单播 RTSP)
        """
        if not self.state.channels:
            self.logger.warning("当前未加载频道列表，自动尝试拉取...")
            self.get_channel_list()

        for ch in self.state.channels:
            if ch["name"] == name_or_id or ch["channel_id"] == name_or_id or ch["user_channel_id"] == name_or_id:
                self.logger.info("找到频道 [%s] 播放地址: 组播=%s, 单播=%s", ch["name"], ch["multicast_url"], ch["unicast_url"])
                return {
                    "name": ch["name"],
                    "channel_id": ch["channel_id"],
                    "user_channel_id": ch["user_channel_id"],
                    "multicast_url": ch["multicast_url"],
                    "unicast_url": ch["unicast_url"]
                }
        self.logger.warning("未找到匹配频道 [%s] 的播放地址", name_or_id)
        return None

    def _parse_epg_json(self, text: str) -> dict:
        """
        用于解析 EPG 服务器非标准 JSON 格式 (例如单引号键值、被圆括号包裹等)
        """
        return parse_epg_json(text)

    def get_vod_list(self, category_id: str, length: int = 10) -> list:
        """
        拉取点播 (VOD) 节目列表
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播节目列表。")
            return []

        self.logger.info("========== 开始拉取分类 [%s] VOD 列表 ==========", category_id)
        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {
            "Action": "columnListAndVodList",
            "categoryId": category_id,
            "posterflag": "1",
            "displayflag": "0",
            "posteridx": "0",
            "columnStart": "0",
            "columnLength": "4",
            "vodLength": str(length)
        }
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=15)
            res_data = self._parse_epg_json(res.text)
            result = res_data.get("result", [])
            media_list = []
            if isinstance(result, list):
                for r in result:
                    media_list.extend(r.get("mediaList", []))
            elif isinstance(result, dict):
                media_list = result.get("mediaList", [])
            
            cleaned_list = []
            for item in media_list:
                cleaned_list.append({
                    "id": str(item.get("id", "")),
                    "name": item.get("name", ""),
                    "telecom_code": item.get("telecomCode", ""),
                    "type": str(item.get("type", ""))
                })
            self.logger.info("成功拉取并解析出 %d 个点播节目！", len(cleaned_list))
            return cleaned_list
        except Exception as e:
            self.logger.error("获取点播列表失败: %s", e)
            return []

    def get_vod_info(self, vod_id: str) -> Optional[dict]:
        """
        获取点播 (VOD) 节目的详细信息 (包含名称、介绍、播放链接等)
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播信息。")
            return None

        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {
            "Action": "vodInfoById",
            "vodId": vod_id
        }
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=10)
            res_data = self._parse_epg_json(res.text)
            return res_data.get("result")
        except Exception as e:
            self.logger.error("拉取 VOD 详细信息时发生异常: %s", e)
            return None

    def get_vod_play_url(self, telecom_code_or_id: str) -> Optional[str]:
        """
        获取点播 (VOD) 节目的单播 RTSP 播放地址
        支持 "ep_id$telecom_code" 双fallback格式：先用ep_id直连，失败再用telecom_code走vodIdByCode转换
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播播放地址。")
            return None

        # Parse dual-fallback format: "ep_id$telecom_code"
        primary_id = telecom_code_or_id
        fallback_code = None
        if "$" in telecom_code_or_id:
            parts = telecom_code_or_id.split("$", 1)
            primary_id = parts[0]
            fallback_code = parts[1] if len(parts) > 1 and parts[1] else None

        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"

        def _resolve_one(target_id: str) -> Optional[str]:
            vod_id = target_id
            if not target_id.isdigit():
                self.logger.info("检测到 telecomCode 格式，正在转换内部 vodId...")
                params_code = {
                    "Action": "vodIdByCode",
                    "foreignSN": target_id,
                    "contentType": "0"
                }
                try:
                    res = self.state.session.get(data_url, params=params_code, headers=self.config.headers, timeout=10)
                    res_data = self._parse_epg_json(res.text)
                    ret_id = res_data.get("result", {}).get("id")
                    if ret_id:
                        vod_id = str(ret_id)
                        self.logger.info("电信编码转换内部 ID 成功: %s -> %s", target_id, vod_id)
                    else:
                        self.logger.warning("转换内部 ID 失败，尝试直接使用原 ID 访问。")
                except Exception as e:
                    self.logger.error("调用 vodIdByCode 发生异常: %s", e)

            # 1. 模拟业务鉴权流程 (Action=serviceAuth)
            self.logger.info("正在发送点播鉴权请求 (Action=serviceAuth)...")
            params_auth = {
                "Action": "serviceAuth",
                "progId": vod_id,
                "contentType": "1"
            }
            try:
                res = self.state.session.get(data_url, params=params_auth, headers=self.config.headers, timeout=10)
                res_data = self._parse_epg_json(res.text)
                retcode = res_data.get("result", {}).get("retcode")
                self.logger.info("点播服务鉴权返回状态码: %s", retcode)
            except Exception as e:
                self.logger.error("点播服务鉴权时发生异常: %s", e)

            # 2. 模拟拉取节目详细信息与 RTSP 播放地址流程 (Action=vodInfoById)
            self.logger.info("正在获取 VOD 媒体播放地址 (Action=vodInfoById)...")
            result = self.get_vod_info(vod_id)
            if result and "mediaUrl" in result:
                return result["mediaUrl"]
            return None

        # Try primary ID first
        self.logger.info("========== 开始获取 VOD 播放地址: %s ==========", primary_id)
        media_url = _resolve_one(primary_id)

        # Fallback to telecom_code if primary fails
        if not media_url and fallback_code:
            self.logger.info("========== 主ID解析失败，尝试用 telecomCode 回退: %s ==========", fallback_code)
            media_url = _resolve_one(fallback_code)

        if media_url:
            self.logger.info("成功解析出点播 RTSP 播放地址!")
            return media_url
        else:
            self.logger.error("所有解析路径均失败，未获取到有效的 mediaUrl。")
            return None

    def get_tvod_program_list(self, channel_id: str, date_str: Optional[str] = None) -> list:
        """
        拉取频道历史回看 (TVOD) 节目单列表
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取回看节目单。")
            return []

        if not date_str:
            date_str = time.strftime("%Y%m%d")

        self.logger.info("========== 开始拉取频道 [%s] 在日期 [%s] 的回看节目单 ==========", channel_id, date_str)
        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {
            "Action": "channelProgramList",
            "channelId": channel_id,
            "date": date_str
        }
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=15)
            res_data = self._parse_epg_json(res.text)
            result = res_data.get("result", [])
            program_list = []
            for item in result:
                program_list.append({
                    "program_id": str(item.get("proID", "")),
                    "name": item.get("name", ""),
                    "day": item.get("day", ""),
                    "begin_time": item.get("beginTime", ""),
                    "end_time": item.get("endTime", "")
                })
            self.logger.info("成功拉取并解析出 %d 个回看节目！", len(program_list))
            return program_list
        except Exception as e:
            self.logger.error("获取回看节目单发生异常: %s", e)
            return []

    def get_tvod_play_url(self, channel_id: str, program_id: str) -> Optional[str]:
        """
        获取回看 (TVOD) 节目的 RTSP 播放地址
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取回看播放地址。")
            return None

        self.logger.info("========== 开始获取回看播放地址 (Channel: %s, Program: %s) ==========", channel_id, program_id)
        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {
            "Action": "programInfo",
            "channelId": channel_id,
            "programId": program_id
        }
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=10)
            res_data = self._parse_epg_json(res.text)
            media_url = res_data.get("result", {}).get("mediaUrl")
            if media_url:
                self.logger.info("成功获取回看 (TVOD) 播放地址!")
                return media_url
            else:
                self.logger.error("服务器未返回有效的 mediaUrl，回看地址获取失败。")
                return None
        except Exception as e:
            self.logger.error("获取回看详细信息时发生异常: %s", e)
            return None

    def get_series_info(self, series_id: str) -> Optional[dict]:
        """
        拉取电视剧 (Series) 的集数及剧集列表信息
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取电视剧信息。")
            return None

        self.logger.info("========== 开始拉取电视剧 [%s] 剧集信息 ==========", series_id)
        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {
            "Action": "seriesInfoById",
            "seriseId": series_id,
            "posterflag": "2",
            "displayflag": "1",
            "posteridx": "1"
        }
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=15)
            res_data = self._parse_epg_json(res.text)
            result = res_data.get("result", {})
            if not result:
                self.logger.error("电视剧信息拉取失败，返回结果为空。")
                return None
                
            episode_list = result.get("episodeList", [])
            episodes = []
            for ep in episode_list:
                episodes.append({
                    "id": str(ep.get("id", "")),
                    "num": str(ep.get("num", "")),
                    "telecom_code": ep.get("telecomCode", "")
                })
            
            series_info = {
                "id": series_id,
                "name": result.get("name", ""),
                "introduce": result.get("introduce", ""),
                "episode_count": result.get("episodeCount", len(episodes)),
                "episodes": episodes
            }
            self.logger.info("成功拉取并解析电视剧 [%s]，共 %d 集！", series_info["name"], len(episodes))
            return series_info
        except Exception as e:
            self.logger.error("获取电视剧剧集信息失败: %s", e)
            return None




# ==========================================
# 3. 入口测试程序
# ==========================================

if __name__ == "__main__":
    # 从配置文件中加载机顶盒凭证（若不存在，使用默认值）
    config_data = load_stb_config()
    test_config = STBDeviceConfig(
        user_id=config_data.get("user_id"),
        stb_id=config_data.get("stb_id"),
        mac_address=config_data.get("mac_address"),
        ip_address=config_data.get("ip_address"),
        base_url=config_data.get("base_url"),
        des_key=config_data.get("des_key")
    )

    # 实例化机顶盒模拟器
    simulator = STBSimulator(config=test_config)

    # 1. 执行登录握手动作
    login_success = simulator.login()
    
    # 2. 如果登录成功，执行业务流与心跳检测
    if login_success:
        # 1. 获取直播频道列表与播放地址
        channels = simulator.get_channel_list()
        if channels:
            print("\n" + "="*70)
            print(f"成功获取频道数据，总频道数: {len(channels)} 个。前 3 个频道信息预览:")
            for ch in channels[:3]:
                print(f"频道 #{ch['user_channel_id']} [{ch['name']}] (ID: {ch['channel_id']}):")
                print(f"  - 组播地址 (IGMP): {ch['multicast_url']}")
                print(f"  - 单播地址 (RTSP): {ch['unicast_url'][:100]}...")
            print("="*70 + "\n")
            
            # 测试直播播放地址获取
            simulator.get_play_url("浙江卫视高清")
            print("="*70 + "\n")

        # 2. 测试点播 (VOD) 列表与播放地址拉取
        # 拉取分类 'catauto25210' 下的点播列表
        vod_list = simulator.get_vod_list("catauto25210", length=5)
        if vod_list:
            print("\n" + "="*70)
            print(f"成功获取 VOD 点播节目列表 (分类 catauto25210)，节目数: {len(vod_list)}:")
            for v in vod_list:
                print(f"  - [{v['name']}] (ID: {v['id']}, telecom_code: {v['telecom_code']})")
            
            # 使用前面已验证的免费节目 '上海滩之生死较量' 的真实 ID '101164792' 进行播放地址解析测试
            free_vod_id = "101164792"
            vod_play_url = simulator.get_vod_play_url(free_vod_id)
            if vod_play_url:
                print(f"\n成功解析 VOD 视频播放地址:")
                print(f"  RTSP URL: {vod_play_url}")
            print("="*70 + "\n")

        # 3. 测试回看 (TVOD) 节目单与播放地址拉取
        if channels:
            # 找到浙江卫视进行回看测试
            zj_channel = None
            for ch in channels:
                if "浙江卫视" in ch['name']:
                    zj_channel = ch
                    break
            
            if not zj_channel:
                zj_channel = channels[0]

            tvod_programs = simulator.get_tvod_program_list(zj_channel['channel_id'])
            if tvod_programs:
                print("\n" + "="*70)
                print(f"成功拉取频道 [{zj_channel['name']}] 的回看节目单，回看项目数: {len(tvod_programs)}。前 3 个节目:")
                for p in tvod_programs[:3]:
                    print(f"  - [{p['name']}] (ID: {p['program_id']}, 时间: {p['day']} {p['begin_time']}-{p['end_time']})")
                
                # 获取第一个节目回看播放地址
                target_p = tvod_programs[0]
                tvod_play_url = simulator.get_tvod_play_url(zj_channel['channel_id'], target_p['program_id'])
                if tvod_play_url:
                    print(f"\n成功解析 TVOD 回看播放地址:")
                    print(f"  RTSP URL: {tvod_play_url}")
                print("="*70 + "\n")

        print("\n" + "="*50)
        print("开始模拟心跳维持进程 (每 600 秒发送一次心跳，与真实机顶盒一致)...")
        print("按 Ctrl+C 可随时退出测试")
        print("="*50 + "\n")
        
        try:
            # 连续进行 3 次心跳循环测试
            for loop in range(1, 4):
                time.sleep(1)  # 主线程轮询休眠粒度为 1 秒
                simulator.keep_alive()
            
            print("\n测试轮询正常结束。模拟心跳流与业务流执行完毕。")
            
        except KeyboardInterrupt:
            print("\n测试由用户主动中止退出。")
    else:
        print("\n[错误] 开机握手流认证失败，请检查网络配置或服务器可达性！")
