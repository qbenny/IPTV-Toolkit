# ==========================================
# ！！请在使用前将此文件重命名为 config.py ！！
# ==========================================

# 代理前缀
PROXY_PREFIX = "http://192.168.1.1:6688/udp/"

# URL 列表
LIONIXQ_M3U_URL = "https://raw.githubusercontent.com/LionixQ/Zhejiang_Telecom_IPTV/main/Zhejiang_Unicast/Zhejiang_Unicast.m3u"
REPORT_URL = "https://myepg.org/Zhejiang_Unicast/report"
EXTERNAL_MCAST_M3U_URL = "https://myepg.org/api/subscribe/multicast/m3u?udpxy=192.168.1.1:6688&logo=192.168.1.3:6688"

# IPTV 模拟机顶盒基础参数
LOGIN_MODE = "simple"  # 登录模式选择: "simple" (使用固定凭证) 或 "dynamic" (使用 DES 动态算密)

# 动态登录特有参数 (当 LOGIN_MODE = "dynamic" 时必填)
IPTV_IP = "xxx.xx.xx.xx"
IPTV_DES_KEY = "xxxxxxxx"

IPTV_BASE_URL = "http://xxx.xx.xx.xx:33200"
IPTV_USER_ID = ""
IPTV_AUTHENTICATOR = ""
IPTV_STBID = ""
IPTV_USER_TOKEN = ""

# IPTV 模拟机顶盒硬件/环境参数
IPTV_STB_TYPE = "EC6110T_zjzdx"
IPTV_STB_VERSION = "19.2.0-LZJD03.B012"
IPTV_SOFTWARE_VERSION = "19.2.0-LZJD03.B012"
IPTV_MAC = "00:00:00:00:00:00"
IPTV_AREA_ID = "xxx"
IPTV_USER_GROUP_ID = "8"
IPTV_TEMPLATE_NAME = "gdhdpublic"

# 统一请求头配置
HEADERS_COMMON = {
    "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.0.3; zh-cn; EC6106V6U_pub_20_zjzdx Build/IML74K) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Safari/534.30 HuaWei;Resolution(PAL,720P,1080i)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh;q=1.0, en;q=0.5",
    "Connection": "keep-alive",
}

# 【自定义分类拦截器】在这里定义你想要的强制分类，通杀本地与外部源
CUSTOM_CATEGORY_MAP = {
    "多彩文体4K": "付费高清",
    "都市剧场": "付费高清",
    "法治天地": "付费高清",
    "动漫秀场": "付费高清",
    "游戏风云": "付费高清",
    "乐游": "付费高清",
    "金色学堂": "付费高清",
    "生活时尚": "付费高清",
    "求索纪录": "付费高清",
}

# 【全局 tvg-id 修正字典】专门用于彻底根治由于一字之差导致的公共 EPG 节目单无法匹配问题
CUSTOM_TVG_ID_MAP = {
    "CCTV5体育高清": "CCTV5体育",
    "中央五套高清": "CCTV5体育",
    "CCTV5+体育赛事高清": "CCTV5+体育赛事",
    "CCTV5+高清": "CCTV5+体育赛事",
    "中央一套高清": "CCTV1综合",
}

# 请求超时时间 (秒)
HTTP_TIMEOUT = 10

# 文件输出路径
OUTPUT_NOFCC = "Zhejiang_Telecom_IPTV_NoFCC.m3u"
OUTPUT_FCC = "Zhejiang_Telecom_IPTV.m3u"

# M3U 文件头信息 (带 EPG 地址)
CLEAN_HEADER = '#EXTM3U x-tvg-url="https://myepg.org/EPG/Zhejiang_Unicast.xml.gz"'
