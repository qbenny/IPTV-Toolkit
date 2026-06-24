import os
import config
import m3u_utils
import auth_handler

def run():
    # 第一步：获取本地机顶盒 IPTV 原始节目列表
    print(">>> [步骤 1] 开始模拟机顶盒获取本地频道列表...")
    session, final_base_url, user_token = auth_handler.login(
        login_mode=config.LOGIN_MODE,
        base_url=config.IPTV_BASE_URL,
        user_id=config.IPTV_USER_ID,
        headers=config.HEADERS_COMMON,
        stbid=config.IPTV_STBID,
        mac=config.IPTV_MAC,
        authenticator=config.IPTV_AUTHENTICATOR,
        user_token=config.IPTV_USER_TOKEN,
        ip=config.IPTV_IP,
        des_key=config.IPTV_DES_KEY,
        stb_type=config.IPTV_STB_TYPE,
        stb_version=config.IPTV_STB_VERSION,
        software_version=config.IPTV_SOFTWARE_VERSION,
        area_id=config.IPTV_AREA_ID,
        user_group_id=config.IPTV_USER_GROUP_ID,
        template_name=config.IPTV_TEMPLATE_NAME,
        timeout=config.HTTP_TIMEOUT
    )
    raw_channel_text = m3u_utils.fetch_local_channels_raw(
        session=session,
        base_url=final_base_url,
        user_id=config.IPTV_USER_ID,
        stbid=config.IPTV_STBID,
        user_token=user_token,
        headers=config.HEADERS_COMMON,
        timeout=config.HTTP_TIMEOUT
    )
    local_channel_count = len(m3u_utils.re.findall(r"CTCSetConfig\('Channel'", raw_channel_text, m3u_utils.re.IGNORECASE))
    print(f">>> [成功] 本地底层数据抓取完毕，共发现 {local_channel_count} 个频道块。")

    # 第二步：获取 Report 测试页 (建立转正数据库)
    print(">>> [步骤 2] 获取 Report 测试页，建立官方转正数据库...")
    report_db = m3u_utils.build_report_db(
        report_url=config.REPORT_URL,
        timeout=config.HTTP_TIMEOUT
    )

    # 第三步：下载解析 M3U 模板 (提取 Logo 和 排序)
    print(">>> [步骤 3] 下载 LionixQ 模板，建立 Logo与排序 数据库...")
    m3u_db = m3u_utils.build_m3u_db(
        m3u_url=config.LIONIXQ_M3U_URL,
        timeout=config.HTTP_TIMEOUT
    )

    # 第四步：下载补充组播列表 (查漏补缺)
    print(">>> [步骤 4] 下载外部补充组播列表，提取备用频道...")
    extra_channels = m3u_utils.fetch_extra_channels(
        external_mcast_m3u_url=config.EXTERNAL_MCAST_M3U_URL,
        timeout=config.HTTP_TIMEOUT
    )

    # 第五步：核心装配（组装本地数据）
    print(">>> [步骤 5] 核心装配启动：执行 100% 精确 of ID -> 模板 级联替换...")
    local_channels, local_mcast_ips, fcc_query = m3u_utils.assemble_channels(
        raw_channel_text=raw_channel_text,
        report_db=report_db,
        m3u_db=m3u_db
    )

    # 第六步：比对并合入缺失的外部频道
    print(">>> [步骤 6] 对比补充列表，合入本地缺失的组播频道...")
    local_channels = m3u_utils.merge_extra_channels(
        local_channels=local_channels,
        extra_channels=extra_channels,
        local_mcast_ips=local_mcast_ips
    )

    # 第七步：全局自定义分类拦截器 (统一覆盖)
    print(">>> [步骤 7] 启动全局拦截器：自定义分类覆盖...")
    local_channels = m3u_utils.apply_custom_category(
        channels=local_channels,
        custom_category_map=config.CUSTOM_CATEGORY_MAP
    )

    # 第八步：全局 tvg-id 修正中间件 (一票改写权)
    print(">>> [步骤 8] 启动全局 tvg-id 修正拦截器：强力矫正公共 EPG 匹配缺陷...")
    local_channels = m3u_utils.apply_custom_tvg_id(
        channels=local_channels,
        custom_tvg_id_map=config.CUSTOM_TVG_ID_MAP
    )

    # 第九步：全局排序与生成最终列表
    print(">>> [步骤 9] 执行最终排序并生成双版纯净 M3U...")
    local_channels.sort(key=lambda x: x['order'])

    m3u_nofcc_lines, m3u_fcc_lines = m3u_utils.generate_m3u_lines(
        channels=local_channels,
        proxy_prefix=config.PROXY_PREFIX,
        fcc_query=fcc_query,
        clean_header=config.CLEAN_HEADER
    )

    with open(config.OUTPUT_NOFCC, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_nofcc_lines) + "\n")
        
    with open(config.OUTPUT_FCC, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_fcc_lines) + "\n")

    print(f">>> [完美竣工] 终极体定制版 M3U 生成完毕！")

if __name__ == "__main__":
    run()
