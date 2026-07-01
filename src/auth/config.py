"""
STB 设备配置类 - 保存开机即固定不变的硬件和环境参数。
从 run_simulator.py 迁移。
"""
from src.utils.helpers import get_iptv_local_ip


class STBDeviceConfig:
    """静态设备配置类。"""

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
            # 真实 IP 模式：自动探测 IPTV 出网 IP
            self.real_ip_mode = True
            try:
                ip_address = get_iptv_local_ip()
                print(f">>> [IPTV IP] 真实 IP 模式，自动探测: {ip_address}")
            except Exception as e:
                print(f">>> [IPTV IP] 自动探测失败: {e}，使用占位 IP（登录时将重试）")
                ip_address = "0.0.0.0"
        else:
            self.real_ip_mode = False
            print(f">>> [IPTV IP] 固定 IP 模式: {ip_address}")
        self.ip_address = ip_address
        self.base_url = base_url        # 初始网关地址
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
