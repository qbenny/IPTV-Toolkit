def login(login_mode, base_url, user_id, headers, stbid, mac, 
          authenticator=None, user_token=None, ip=None, des_key=None,
          stb_type=None, stb_version=None, software_version=None,
          area_id=None, user_group_id=None, template_name=None, timeout=10):
    """
    统一登录路由入口，根据模式动态载入对应登录模块，避免不必要的依赖导入
    """
    mode = login_mode.lower().strip()
    if mode == "simple":
        print(">>> [系统] 正在以 [简易固定凭证] 模式进行登录...")
        import auth_simple
        return auth_simple.login_simple(
            base_url=base_url, user_id=user_id, headers=headers,
            authenticator=authenticator, stbid=stbid, user_token=user_token,
            stb_type=stb_type, stb_version=stb_version, mac=mac,
            software_version=software_version, area_id=area_id,
            user_group_id=user_group_id, template_name=template_name,
            timeout=timeout
        )
    elif mode == "dynamic":
        print(">>> [系统] 正在以 [动态算密鉴权] 模式进行登录...")
        if not ip or not des_key:
            raise ValueError("动态登录模式必须配置 IPTV_IP 和 IPTV_DES_KEY")
        try:
            import auth_dynamic
        except ImportError as e:
            if "Crypto" in str(e) or "pycryptodome" in str(e):
                raise ImportError(
                    "未检测到 Crypto 模块。当启用动态登录模式时，您需要安装 pycryptodome 库：\n"
                    "pip install pycryptodome"
                ) from e
            raise e
        return auth_dynamic.login_dynamic(
            base_url=base_url, user_id=user_id, headers=headers,
            stbid=stbid, mac=mac, ip=ip, des_key=des_key,
            stb_type=stb_type, stb_version=stb_version, timeout=timeout
        )
    else:
        raise ValueError(f"未知的登录模式: {login_mode}，只支持 'simple' 或 'dynamic'")
