import requests
import re



def fetch_local_channels_raw(session, base_url, user_id, stbid, user_token, headers, timeout=10):
    """
    第一步：获取本地机顶盒 IPTV 原始节目列表数据
    """
    channel_data = {
        "conntype": "4",
        "UserToken": user_token,
        "tempKey": "",
        "stbid": stbid[-6:],
        "SupportHD": "1",
        "UserID": user_id,
        "Lang": "1"
    }
    
    r4 = session.post(
        f"{base_url}/EPG/jsp/getchannellistHWCTC.jsp",
        data=channel_data,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout
    )
    
    return r4.text


def build_report_db(report_url, timeout=10):
    """
    第二步：获取 Report 测试页 (建立转正数据库)
    """
    report_db = {}
    try:
        report_resp = requests.get(report_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', report_resp.text, re.IGNORECASE | re.DOTALL)
        id_col, name_col, group_col = -1, -1, -1
        
        for row in rows:
            cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.IGNORECASE | re.DOTALL)
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            if not cells:
                continue
            
            if id_col == -1:
                for i, c in enumerate(cells):
                    if c == '频道ID':
                        id_col = i
                    elif c == '频道名称':
                        name_col = i
                    elif c == '频道分类':
                        group_col = i
                if id_col == -1 and cells[0] == '序号' and len(cells) >= 4:
                    id_col, name_col, group_col = 2, 1, 3
                continue
                    
            if id_col != -1 and name_col != -1 and len(cells) > max(id_col, name_col):
                cid = cells[id_col]
                cname = cells[name_col]
                cgrp = cells[group_col] if group_col != -1 and len(cells) > group_col else "自有"
                if cid.isdigit() and cname:
                    report_db[cid] = {'name': cname, 'group': cgrp}
                    
        print(f">>> [成功] Report 数据库建立完成，成功收录 {len(report_db)} 个 ChannelID。")
    except Exception as e:
        print(f"!!! 获取 Report 页面失败: {e}")
    return report_db

def build_m3u_db(m3u_url, timeout=10):
    """
    第三步：下载解析 M3U 模板 (提取 Logo 和 排序)
    """
    m3u_db = {}
    try:
        r_m3u = requests.get(m3u_url, timeout=timeout)
        r_m3u.raise_for_status()
        
        order_idx = 0
        for line in r_m3u.text.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF"):
                tvg_logo_match = re.search(r'tvg-logo="([^"]*)"', line)
                group_match = re.search(r'group-title="([^"]*)"', line)
                
                t_logo = tvg_logo_match.group(1).strip() if tvg_logo_match else ""
                t_group = group_match.group(1).strip() if group_match else ""
                display_name = line.split(',')[-1].strip()
                
                m3u_db[display_name] = {'logo': t_logo, 'group': t_group, 'order': order_idx}
                order_idx += 1
                
        print(f">>> [成功] 模板数据库建立完成，共收录 {order_idx} 个标准频道皮肤。")
    except Exception as e:
        print(f"!!! 下载 M3U 模板失败: {e}")
    return m3u_db

def fetch_extra_channels(external_mcast_m3u_url, timeout=10):
    """
    第四步：下载补充组播列表 (查漏补缺)
    """
    extra_channels = []
    try:
        resp = requests.get(external_mcast_m3u_url, timeout=timeout)
        resp.raise_for_status()
        ext_lines = resp.text.splitlines()
        extinf_buf = None
        
        ext_idx = 0
        for line in ext_lines:
            line = line.strip()
            if line.startswith("#EXTINF"):
                extinf_buf = line
            elif not line.startswith("#") and extinf_buf:
                ip_match = re.search(r'(?:udp|rtp|igmp)[/:]+/?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line, re.IGNORECASE)
                if not ip_match:
                    ip_match = re.search(r'(2(?:2[4-9]|3[0-9])\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    
                if ip_match:
                    tvg_id = re.search(r'tvg-id="([^"]*)"', extinf_buf)
                    tvg_name = re.search(r'tvg-name="([^"]*)"', extinf_buf)
                    tvg_logo = re.search(r'tvg-logo="([^"]*)"', extinf_buf)
                    group = re.search(r'group-title="([^"]*)"', extinf_buf)
                    disp_name = extinf_buf.split(',')[-1].strip()
                    
                    extra_channels.append({
                        'mcast': ip_match.group(1),
                        'tvg_id': tvg_id.group(1) if tvg_id else disp_name,
                        'tvg_name': tvg_name.group(1) if tvg_name else disp_name,
                        'tvg_logo': tvg_logo.group(1) if tvg_logo else "",
                        'group_title': group.group(1) if group else "自有",
                        'display_name': disp_name,
                        'catchup': "",
                        'order': 9999000 + ext_idx # 确保排在本地频道之后
                    })
                    ext_idx += 1
                extinf_buf = None
                
        print(f">>> [成功] 外部补充列表抓取完成，共提取 {len(extra_channels)} 个候选组播源。")
    except Exception as e:
        print(f"!!! 获取补充列表失败: {e}")
    return extra_channels

def assemble_channels(raw_channel_text, report_db, m3u_db):
    """
    第五步：核心装配（组装本地数据）
    """
    local_channels = []
    processed_ids = set()
    local_mcast_ips = set()
    
    fcc_ip_match = re.search(r'FCCIP\s*=\s*["\']([^"\']+)["\']', raw_channel_text, re.IGNORECASE)
    fcc_port_match = re.search(r'FCCPort\s*=\s*["\']([^"\']+)["\']', raw_channel_text, re.IGNORECASE)
    global_fcc_ip = fcc_ip_match.group(1) if fcc_ip_match else ""
    global_fcc_port = fcc_port_match.group(1) if fcc_port_match else ""
    fcc_query = f"fcc={global_fcc_ip}:{global_fcc_port}" if (global_fcc_ip and global_fcc_port) else ""
    
    for match in re.finditer(r"CTCSetConfig\('Channel',\s*'([^']+)'\)", raw_channel_text, re.IGNORECASE):
        ch_str = match.group(1)
        
        def get_val(key):
            m = re.search(f'{key}="([^"]*)"', ch_str, re.IGNORECASE)
            return m.group(1) if m else ""
    
        channel_id = get_val("ChannelID")
        channel_name = get_val("ChannelName")
        channel_url = get_val("ChannelURL")
        ts_val = get_val("TimeShift")
        ts_len_val = get_val("TimeShiftLength")
        
        if not channel_name or not channel_url:
            continue
            
        cid_key = channel_id if channel_id else channel_name
        if cid_key in processed_ids:
            continue
        processed_ids.add(cid_key)
    
        mcast_ip_port = ""
        rtsp_url = ""
        for u in channel_url.split('|'):
            if u.startswith("igmp://") or u.startswith("udp://") or u.startswith("rtp://"):
                mcast_ip_port = u.split("://")[-1]
            elif u.startswith("rtsp://"):
                rtsp_url = u
    
        if mcast_ip_port:
            local_mcast_ips.add(mcast_ip_port)
    
        clean_rtsp = ""
        if rtsp_url and ts_val != "0" and ts_len_val != "0":
            smil_index = rtsp_url.find('.smil')
            if smil_index != -1:
                clean_rtsp = rtsp_url[:smil_index + 5]
    
        ch_info = {
            'tvg_id': channel_id if channel_id else channel_name,
            'tvg_name': channel_name,
            'display_name': channel_name,
            'tvg_logo': "",
            'group_title': "自有",
            'order': 999999,
            'mcast': mcast_ip_port,
            'catchup': clean_rtsp
        }
        
        # 1. 查 Report 库，按 ID 绝对匹配转正
        if channel_id and channel_id in report_db:
            std_name = report_db[channel_id]['name']
            std_group = report_db[channel_id]['group']
            ch_info['tvg_id'] = std_name
            ch_info['tvg_name'] = std_name
            ch_info['display_name'] = std_name
            if std_group and std_group != "自有":
                ch_info['group_title'] = std_group
                
        # 2. 查 M3U 模板，按名称绝对匹配挑衣服
        db_info = m3u_db.get(ch_info['display_name'])
        if db_info:
            ch_info['tvg_logo'] = db_info['logo']
            ch_info['order'] = db_info['order']
            if db_info['group'] and db_info['group'] != "自有":
                ch_info['group_title'] = db_info['group']
    
        local_channels.append(ch_info)
        
    return local_channels, local_mcast_ips, fcc_query

def merge_extra_channels(local_channels, extra_channels, local_mcast_ips):
    """
    第六步：比对并合入缺失的外部频道
    """
    append_count = 0
    for ext_ch in extra_channels:
        if ext_ch['mcast'] and ext_ch['mcast'] not in local_mcast_ips:
            local_channels.append(ext_ch)
            append_count += 1
    print(f">>> [成功] 追加了 {append_count} 个本地未包含的组播频道。")
    return local_channels

def apply_custom_category(channels, custom_category_map):
    """
    第七步：全局自定义分类拦截器 (统一覆盖)
    """
    intercept_count = 0
    for ch in channels:
        if ch['display_name'] in custom_category_map:
            ch['group_title'] = custom_category_map[ch['display_name']]
            intercept_count += 1
    print(f">>> [成功] 已强制重写 {intercept_count} 个频道的分类。")
    return channels

def apply_custom_tvg_id(channels, custom_tvg_id_map):
    """
    第八步：全局 tvg-id 修正中间件 (一票改写权)
    """
    tvgid_count = 0
    for ch in channels:
        # 全维度扫描：防止因未转正等原因漏掉匹配
        candidates = [ch['tvg_id'].strip(), ch['display_name'].strip(), ch['tvg_name'].strip()]
        for c in candidates:
            if c and c in custom_tvg_id_map:
                ch['tvg_id'] = custom_tvg_id_map[c]
                tvgid_count += 1
                break
                
    print(f">>> [成功] 已强力校正 {tvgid_count} 个频道的 tvg-id。")
    return channels

def generate_m3u_lines(channels, proxy_prefix, fcc_query, clean_header):
    """
    第九步：根据最终频道数据生成 M3U 的行内容列表
    """
    m3u_nofcc_lines = [clean_header]
    m3u_fcc_lines = [clean_header]
    
    for ch in channels:
        logo_attr = f' tvg-logo="{ch["tvg_logo"]}"' if ch["tvg_logo"] else ' tvg-logo=""'
        extinf_base = f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-name="{ch["tvg_name"]}"{logo_attr} group-title="{ch["group_title"]}"'
        
        if ch['catchup']:
            catchup_str = f' catchup="default" catchup-source="{ch["catchup"]}?playseek=${{(b)yyyyMMddHHmmss}}-${{(e)yyyyMMddHHmmss}}"'
            extinf_line = f'{extinf_base}{catchup_str},{ch["display_name"]}'
        else:
            extinf_line = f'{extinf_base},{ch["display_name"]}'
            
        my_live_url_nofcc = f"{proxy_prefix}{ch['mcast']}"
        my_live_url_fcc = f"{my_live_url_nofcc}?{fcc_query}" if fcc_query else my_live_url_nofcc
        
        m3u_nofcc_lines.append(extinf_line)
        m3u_nofcc_lines.append(my_live_url_nofcc)
        
        m3u_fcc_lines.append(extinf_line)
        m3u_fcc_lines.append(my_live_url_fcc)
        
    return m3u_nofcc_lines, m3u_fcc_lines
