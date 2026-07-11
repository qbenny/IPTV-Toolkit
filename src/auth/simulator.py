"""
STB 模拟器主类 - 包含登录、心跳、点播播放地址解析等功能。
从 run_simulator.py 迁移。
"""
import json
import random
import re
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from src.auth.config import STBDeviceConfig
from src.auth.state import STBRuntimeState
from src.utils.helpers import parse_epg_json, get_iptv_local_ip
from src.utils.logger import logger as _project_logger

# Attempt to import Cryptodome for DES
try:
    from Crypto.Cipher import DES
except ImportError:
    DES = None


class STBSimulator:
    """机顶盒模拟器主类。"""

    def __init__(self, config: STBDeviceConfig):
        self.config = config
        self.state = STBRuntimeState()

        self.logger = _project_logger
        self.logger.info("机顶盒网络模拟器已就绪。设备账号: %s", self.config.user_id)

        # 重登录回调（由 main.py 注入 login_sim），供 _session_request 顶号自愈使用
        self.login_func = None

    def _log_request(self, method: str, url: str, response: requests.Response):
        """记录请求日志。"""
        self.logger.info(">>> 发送 %s 请求: %s", method, url)
        self.logger.info("<<< 收到响应: HTTP %d", response.status_code)
        self.logger.info("-" * 60)

    def _pad(self, text: str, block_size: int = 8) -> bytes:
        """DES 填充。"""
        pad_len = block_size - (len(text) % block_size)
        return (text + pad_len * chr(pad_len)).encode("utf-8")

    def _generate_auth_signature(self) -> str:
        """根据抓包动态算密逻辑，使用 DES-ECB 计算明文摘要。"""
        if DES is None:
            raise ImportError(
                "未检测到 Crypto.Cipher 加密模块！请安装 pycryptodome 库（pip install pycryptodome）以进行动态签名计算。"
            )

        rand_str = str(random.randint(10000, 99999))

        session_ref = (
            f"{rand_str}$"
            f"{self.state.encrypt_token}$"
            f"{self.config.user_id}$"
            f"{self.config.stb_id}$"
            f"{self.config.ip_address}$"
            f"{self.config.mac_address}$$CTC"
        )

        padded_data = self._pad(session_ref, DES.block_size)
        cipher = DES.new(self.config.des_key.encode("utf-8"), DES.MODE_ECB)
        encrypted_bytes = cipher.encrypt(padded_data)

        auth_signature = encrypted_bytes.hex().upper()
        self.logger.info("动态密文签名 (Authenticator) 已计算生成成功。")
        return auth_signature

    def _resolve_vis_domain(self) -> Optional[str]:
        """登录后从 configUrl.min.js 解析 VIS VOD 服务器地址。"""
        operator = self.state.operator or "telecom"

        try:
            url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/js/configUrl.min.js"
            r = self.state.session.get(url, headers=self.config.headers, timeout=10)
            if r.status_code == 200:
                m = re.search(
                    r'visEpgIp\s*=\s*["\'][^"\']*["\'].*?\?\s*["\']([^"\']+)["\']\s*:\s*["\']([^"\']+)["\']',
                    r.text,
                )
                if m:
                    unicom_ip = m.group(1)
                    telecom_ip = m.group(2)
                    vis_ip = unicom_ip if operator == "unicom" else telecom_ip
                    vis_base_url = f"http://{vis_ip}/epg/"
                    self.logger.info("VIS 服务器地址解析成功 (%s 线路): %s", operator, vis_base_url)
                    return vis_base_url
                else:
                    self.logger.warning("VIS 服务器地址正则匹配失败")
            else:
                self.logger.warning("configUrl.min.js 获取失败 HTTP %d", r.status_code)
        except Exception as e:
            self.logger.warning("VIS 服务器地址解析失败: %s", e)
        return None

    def login(self) -> bool:
        """执行完整的 IPTV 开机认证流。"""
        self.logger.info("========== 启动登录握手时序 ==========")
        try:
            # 1. 访问网关引导
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
                timeout=10,
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

            # 2.5 提取运营商类型
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
                "VIP": "",
            }

            url3 = f"{self.state.epg_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp"
            res3 = self.state.session.post(
                url3,
                data=valid_payload,
                headers={**self.config.headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": url2},
                timeout=10,
            )
            self._log_request("POST", url3, res3)

            # 4. 解析正式的 UserToken
            final_token_match = re.search(r'[\'"]UserToken[\'"]\s*,\s*[\'"](.+?)[\'"]', res3.text)
            if not final_token_match:
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
        """心跳上报维持逻辑。"""
        if not self.state.is_authenticated:
            self.logger.warning("机顶盒当前处于离线状态，暂不发送心跳包。")
            return

        current_time = time.time()
        if current_time - self.state.last_heartbeat_time < self.state.heartbeat_interval:
            return

        self.logger.info("心跳时间窗口到达，上报机顶盒状态...")

        # 真实 IP 模式下，检测出网 IP 是否发生变更
        if self.config.real_ip_mode:
            try:
                current_ip = get_iptv_local_ip()
                if current_ip != self.config.ip_address:
                    self.logger.warning(
                        "IPTV 出网 IP 变更: %s -> %s，清除认证状态以触发重登录",
                        self.config.ip_address, current_ip,
                    )
                    self.config.ip_address = current_ip
                    self.state.clear_auth_state()
                    return
            except Exception as e:
                self.logger.warning("IP 变动检测失败: %s，跳过本次心跳", e)
                return

        heartbeat_url = f"{self.state.epg_base_url}/EPG/jsp/GetHeartBit"
        params = {
            "UserStatus": "1",
            "ChannelVer": time.strftime("%Y%m%d%H%M%S"),
            "STBID": self.config.stb_id,
            "STBType": self.config.stb_type,
            "Version": self.config.software_version,
        }

        try:
            res = self.state.session.get(heartbeat_url, params=params, headers=self.config.headers, timeout=10)
            self._log_request("GET (心跳包)", heartbeat_url, res)

            if res.status_code == 200:
                user_valid_match = re.search(r'UserValid\s*=\s*(true|false)', res.text, re.IGNORECASE)
                if user_valid_match:
                    user_valid = user_valid_match.group(1).lower() == "true"
                    if not user_valid:
                        self.logger.warning("服务器返回 UserValid=false！会话 Token 已失效，清除认证状态。")
                        self.state.clear_auth_state()
                        return

                interval_match = re.search(r'NextCallInterval\s*=\s*(\d+)', res.text)
                if interval_match:
                    server_interval = int(interval_match.group(1))
                    # 限制最大间隔为 600 秒，防止 Session 过期
                    if server_interval > 600:
                        self.logger.warning("服务器下发间隔 %d 秒超过上限 600 秒，强制限制为 600 秒", server_interval)
                        server_interval = 600
                    if server_interval > 0 and server_interval != self.state.heartbeat_interval:
                        self.logger.info("服务器下发推荐心跳间隔: %d 秒 (之前: %d 秒)", server_interval, self.state.heartbeat_interval)
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

    def _session_request(self, method: str, url: str, timeout: int = 15, **kwargs) -> "requests.Response":
        """带登录壳页（resignon）自愈的底层请求方法。

        若响应是门户登录壳页（被顶号/会话失效），清认证状态 + 重登录，重试一次。
        返回 requests.Response。并发安全（ensure_authenticated 内置锁）。
        """
        from src.auth.heartbeat import ensure_authenticated

        res = self.state.session.request(method, url, timeout=timeout, **kwargs)
        if "resignon" in res.text and self.login_func:
            self.logger.warning("[STB] 会话失效(收到登录壳页)，清状态重登录重试: %s", url)
            self.state.clear_auth_state()
            ensure_authenticated(self, self.login_func)
            if self.state.is_authenticated:
                res = self.state.session.request(method, url, timeout=timeout, **kwargs)
                if "resignon" in res.text:
                    self.logger.error("[STB] 重登录后仍为登录壳页，放弃: %s", url)
            else:
                self.logger.error("[STB] 检测到登录壳页但重登录失败，放弃: %s", url)
        return res

    def _session_get_json(self, url: str, params: dict, timeout: int = 15) -> dict:
        """带登录壳页自愈的 JSON 请求；返回解析后的 dict，失败返回空 dict。"""
        res = self._session_request("GET", url, timeout=timeout, params=params,
                                    headers=self.config.headers)
        return parse_epg_json(res.text)

    def get_vod_id_by_code(self, item_code: str) -> Optional[str]:
        """通过 contentCode 解析 vod_id（Action=vodIdByCode）；被顶号时自动重登录重试。"""
        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {"Action": "vodIdByCode", "foreignSN": item_code, "contentType": "0"}
        data = self._session_get_json(data_url, params, timeout=10)
        vod_id = data.get("result", {}).get("id") if data else None
        if not vod_id:
            # 非壳页（壳页已在底座重试并记日志）：多为 contentCode 无效/未授权
            self.logger.warning("[STB] vodIdByCode 未返回 vod_id（非壳页，可能 contentCode 无效）: %s", item_code)
        return str(vod_id) if vod_id else None

    def get_vod_info(self, vod_id: str) -> Optional[dict]:
        """获取点播节目的详细信息（名称、介绍、播放链接等）。"""
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播信息。")
            return None

        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {"Action": "vodInfoById", "vodId": vod_id}
        data = self._session_get_json(data_url, params, timeout=10)
        return data.get("result") if data else None

    def get_vod_play_url(self, vod_id: str) -> Optional[str]:
        """获取点播节目的单播 RTSP 播放地址。"""
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播播放地址。")
            return None

        self.logger.info("========== 开始获取 VOD 播放地址: %s ==========", vod_id)
        self.logger.info("正在获取 VOD 媒体播放地址 (Action=vodInfoById)...")
        result = self.get_vod_info(vod_id)
        if result and "mediaUrl" in result:
            self.logger.info("成功解析出点播 RTSP 播放地址!")
            return result["mediaUrl"]

        self.logger.error("解析失败，未获取到有效的 mediaUrl。")
        return None

    def get_series_info(self, series_id: str) -> Optional[dict]:
        """拉取电视剧的集数及剧集列表信息。"""
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
            "posteridx": "1",
        }
        try:
            res_data = self._session_get_json(data_url, params, timeout=15)
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
                })

            series_info = {
                "id": series_id,
                "name": result.get("name", ""),
                "introduce": result.get("introduce", ""),
                "episode_count": result.get("episodeCount", len(episodes)),
                "episodes": episodes,
            }
            self.logger.info("成功拉取并解析电视剧 [%s]，共 %d 集！", series_info["name"], len(episodes))
            return series_info
        except Exception as e:
            self.logger.error("获取电视剧剧集信息失败: %s", e)
            return None

    def get_channel_list(self) -> list:
        """获取机顶盒的直播频道列表。

        该方法向 /EPG/jsp/getchannellistHWCTC.jsp 发送 POST 请求，
        拉取服务器返回的频道信息并解析出完整字段（20+ 个）。
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
            res = self._session_request(
                "POST", url,
                data=payload,
                headers={
                    **self.config.headers, 
                    "Content-Type": "application/x-www-form-urlencoded", 
                    "Referer": f"{self.state.epg_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp"
                },
                timeout=15
            )
            self._log_request("POST", url, res)

            if res.status_code != 200:
                self.logger.error("频道列表请求失败，HTTP 状态码: %d", res.status_code)
                return []

            channel_blocks = re.findall(
                r"Authentication\.CTCSetConfig\(\s*['\"]Channel['\"]\s*,\s*['\"](.+?)['\"]\s*\)", 
                res.text
            )
            
            channels = []
            for block in channel_blocks:
                kv_pairs = re.findall(r'(\w+)="([^"]*)"', block)
                ch_info = {k: v for k, v in kv_pairs}
                
                if "ChannelName" in ch_info and "ChannelURL" in ch_info:
                    play_url_raw = ch_info.get("ChannelURL", "")
                    urls = play_url_raw.split('|')
                    multicast_url = ""
                    unicast_url_full = ""
                    unicast_url = ""
                    for u in urls:
                        if u.startswith("igmp://"):
                            multicast_url = u
                        elif u.startswith("rtsp://") or u.startswith("http://"):
                            unicast_url_full = u
                            if '?' in u:
                                unicast_url = u.split('?', 1)[0]
                            else:
                                unicast_url = u
                            
                    def to_int(val, default=0):
                        try:
                            return int(val) if val else default
                        except ValueError:
                            return default

                    channel_data = {
                        "channel_id": ch_info.get("ChannelID", ""),
                        "user_channel_id": ch_info.get("UserChannelID", ""),
                        "name": ch_info.get("ChannelName", ""),
                        "multicast_url": multicast_url,
                        "unicast_url": unicast_url,
                        "unicast_url_full": unicast_url_full,
                        "timeshift_enabled": to_int(ch_info.get("TimeShift", "0")),
                        "timeshift_length": to_int(ch_info.get("TimeShiftLength", "0")),
                        "timeshift_url": ch_info.get("TimeShiftURL", ""),
                        "is_hd": to_int(ch_info.get("IsHDChannel", "0")),
                        "channel_type": ch_info.get("ChannelType", ""),
                        "channel_sdp": ch_info.get("ChannelSDP", ""),
                        "channel_url_raw": play_url_raw,
                        "channel_locked": to_int(ch_info.get("ChannelLocked", "0")),
                        "preview_enabled": to_int(ch_info.get("PreviewEnable", "0")),
                        "fcc_enabled": to_int(ch_info.get("FCCEnable", "0")),
                        "fcc_ip": ch_info.get("ChannelFCCIP", ""),
                        "fcc_port": ch_info.get("ChannelFCCPort", ""),
                        "fec_port": ch_info.get("ChannelFECPort", ""),
                        "raw_fields_json": json.dumps(ch_info, ensure_ascii=False)
                    }
                    channels.append(channel_data)

            self.logger.info("成功拉取并解析出 %d 个频道！", len(channels))
            return channels

        except Exception as e:
            self.logger.error("获取频道列表时遭遇异常: %s", e, exc_info=True)
            return []
