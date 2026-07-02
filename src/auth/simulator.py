"""
STB 模拟器主类 - 包含登录、心跳、点播播放地址解析等功能。
从 run_simulator.py 迁移。
"""
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

    def get_vod_info(self, vod_id: str) -> Optional[dict]:
        """获取点播节目的详细信息（名称、介绍、播放链接等）。"""
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播信息。")
            return None

        data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
        params = {"Action": "vodInfoById", "vodId": vod_id}
        try:
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=10)
            res_data = parse_epg_json(res.text)
            return res_data.get("result")
        except Exception as e:
            self.logger.error("拉取 VOD 详细信息时发生异常: %s", e)
            return None

    def get_vod_play_url(self, telecom_code_or_id: str) -> Optional[str]:
        """获取点播节目的单播 RTSP 播放地址。

        支持 "ep_id$telecom_code" 双fallback格式：先用ep_id直连，失败再用telecom_code走vodIdByCode转换。
        """
        if not self.state.is_authenticated:
            self.logger.error("未认证，无法获取点播播放地址。")
            return None

        # 解析双fallback格式
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
                params_code = {"Action": "vodIdByCode", "foreignSN": target_id, "contentType": "0"}
                try:
                    res = self.state.session.get(data_url, params=params_code, headers=self.config.headers, timeout=10)
                    res_data = parse_epg_json(res.text)
                    ret_id = res_data.get("result", {}).get("id")
                    if ret_id:
                        vod_id = str(ret_id)
                        self.logger.info("电信编码转换内部 ID 成功: %s -> %s", target_id, vod_id)
                    else:
                        self.logger.warning("转换内部 ID 失败，尝试直接使用原 ID 访问。")
                except Exception as e:
                    self.logger.error("调用 vodIdByCode 发生异常: %s", e)

            # 1. 模拟业务鉴权流程
            self.logger.info("正在发送点播鉴权请求 (Action=serviceAuth)...")
            params_auth = {"Action": "serviceAuth", "progId": vod_id, "contentType": "1"}
            try:
                res = self.state.session.get(data_url, params=params_auth, headers=self.config.headers, timeout=10)
                res_data = parse_epg_json(res.text)
                retcode = res_data.get("result", {}).get("retcode")
                self.logger.info("点播服务鉴权返回状态码: %s", retcode)
            except Exception as e:
                self.logger.error("点播服务鉴权时发生异常: %s", e)

            # 2. 模拟拉取节目详细信息与 RTSP 播放地址
            self.logger.info("正在获取 VOD 媒体播放地址 (Action=vodInfoById)...")
            result = self.get_vod_info(vod_id)
            if result and "mediaUrl" in result:
                return result["mediaUrl"]
            return None

        # 主ID解析
        self.logger.info("========== 开始获取 VOD 播放地址: %s ==========", primary_id)
        media_url = _resolve_one(primary_id)

        # 回退
        if not media_url and fallback_code:
            self.logger.info("========== 主ID解析失败，尝试用 telecomCode 回退: %s ==========", fallback_code)
            media_url = _resolve_one(fallback_code)

        if media_url:
            self.logger.info("成功解析出点播 RTSP 播放地址!")
            return media_url
        else:
            self.logger.error("所有解析路径均失败，未获取到有效的 mediaUrl。")
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
            res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=15)
            res_data = parse_epg_json(res.text)
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
                    "telecom_code": ep.get("telecomCode", ""),
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

    @staticmethod
    def _parse_channel_block(block: str) -> dict:
        """解析单个 CTCSetConfig('Channel', '...') 块的完整字段。

        Args:
            block: key="value",... 格式的原始字符串

        Returns:
            包含所有解析字段的字典
        """
        import json as _json

        kv_pairs = re.findall(r'(\w+)="([^"]*)"', block)
        ch_info = {k: v for k, v in kv_pairs}

        if "ChannelName" not in ch_info or "ChannelURL" not in ch_info:
            return {}

        # 原始值保存为 JSON
        raw_fields_json = _json.dumps(ch_info, ensure_ascii=False)

        # 解析 ChannelURL: igmp://...|rtsp://... 或 http://...
        play_url_raw = ch_info.get("ChannelURL", "")
        urls = play_url_raw.split("|")
        multicast_url = ""
        unicast_url_full = ""
        for u in urls:
            if u.startswith("igmp://"):
                multicast_url = u
            elif u.startswith("rtsp://") or u.startswith("http://"):
                unicast_url_full = u

        # 简化版单播 URL（去动态 token 参数，长期有效）
        unicast_url = unicast_url_full.split("?")[0] if unicast_url_full else ""

        def _int(key: str, default: int = 0) -> int:
            try:
                return int(ch_info.get(key, str(default)))
            except (ValueError, TypeError):
                return default

        return {
            "channel_id": ch_info.get("ChannelID", ""),
            "user_channel_id": ch_info.get("UserChannelID", ""),
            "name": ch_info.get("ChannelName", ""),
            "multicast_url": multicast_url,
            "unicast_url": unicast_url,
            "unicast_url_full": unicast_url_full,
            "timeshift_enabled": _int("TimeShift"),
            "timeshift_length": _int("TimeShiftLength"),
            "timeshift_url": ch_info.get("TimeShiftURL", ""),
            "is_hd": _int("IsHDChannel"),
            "channel_type": ch_info.get("ChannelType", ""),
            "channel_sdp": ch_info.get("ChannelSDP", ""),
            "channel_url_raw": play_url_raw,
            "channel_locked": _int("ChannelLocked"),
            "preview_enabled": _int("PreviewEnable"),
            "fcc_enabled": _int("FCCEnable"),
            "fcc_ip": ch_info.get("ChannelFCCIP", ""),
            "fcc_port": ch_info.get("ChannelFCCPort", ""),
            "fec_port": _int("ChannelFECPort"),
            "raw_fields_json": raw_fields_json,
        }

    @staticmethod
    def parse_channel_response(raw_text: str) -> list:
        """解析包含 CTCSetConfig 调用的原始响应文本为频道列表。

        可用于测试环境，传入样本 HTML/JS 直接解析频道数据。

        Args:
            raw_text: 包含 Authentication.CTCSetConfig('Channel',...) 的原始文本

        Returns:
            解析后的频道字典列表
        """
        channel_blocks = re.findall(
            r"Authentication\.CTCSetConfig\(\s*['\"]Channel['\"]\s*,\s*['\"](.+?)['\"]\s*\)",
            raw_text,
        )
        channels = []
        for block in channel_blocks:
            parsed = STBSimulator._parse_channel_block(block)
            if parsed:
                channels.append(parsed)
        return channels

    def get_channel_list(self) -> list:
        """获取机顶盒的直播频道列表（全字段版）。

        向 /EPG/jsp/getchannellistHWCTC.jsp 发送 POST 请求，
        拉取服务器返回的频道信息并解析所有字段。

        Returns:
            频道字典列表，每个字典包含 21 个字段
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
            "Lang": "1",
        }

        url = f"{self.state.epg_base_url}/EPG/jsp/getchannellistHWCTC.jsp"
        try:
            res = self.state.session.post(
                url,
                data=payload,
                headers={
                    **self.config.headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{self.state.epg_base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp",
                },
                timeout=15,
            )
            self._log_request("POST", url, res)

            if res.status_code != 200:
                self.logger.error("频道列表请求失败，HTTP 状态码: %d", res.status_code)
                return []

            channels = self.parse_channel_response(res.text)

            self.logger.info("成功拉取并解析出 %d 个频道！", len(channels))
            return channels

        except Exception as e:
            self.logger.error("获取频道列表时遭遇异常: %s", e, exc_info=True)
            return []
