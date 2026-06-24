import requests

def login_simple(base_url, user_id, headers, authenticator, stbid, user_token,
                 stb_type, stb_version, mac, software_version, area_id,
                 user_group_id, template_name, timeout=10):
    """
    简易固定凭证登录方式
    """
    session = requests.Session()
    
    session.get(
        f"{base_url}/EPG/jsp/AuthenticationURL?UserID={user_id}&Action=Login&FCCSupport=1",
        headers=headers,
        timeout=timeout
    )
    
    session.post(
        f"{base_url}/EPG/jsp/authLoginHWCTC.jsp?UserID={user_id}&SampleId=",
        data={"UserID": user_id, "VIP": ""},
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout
    )
    
    valid_data = {
        "UserID": user_id,
        "Lang": "1",
        "SupportHD": "1",
        "NetUserID": f"tv{user_id}@itv",
        "Authenticator": authenticator,
        "STBType": stb_type,
        "STBVersion": stb_version,
        "conntype": "4",
        "STBID": stbid,
        "templateName": template_name,
        "areaId": area_id,
        "userToken": user_token,
        "userGroupId": user_group_id,
        "productPackageId": "-1",
        "mac": mac,
        "UserField": "2",
        "SoftwareVersion": software_version,
        "IsSmartStb": "0",
        "desktopId": "",
        "stbmaker": "",
        "VIP": ""
    }
    
    session.post(
        f"{base_url}/EPG/jsp/ValidAuthenticationHWCTC.jsp",
        data=valid_data,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout
    )
    
    return session, base_url, user_token
