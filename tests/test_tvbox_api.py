"""TVBox API 离线测试（使用 sample 数据）。"""
import urllib.request, urllib.parse, json, base64

HOST = "http://localhost:8880"


def call(path):
    # 自动处理中文 URL 编码
    if "?" in path:
        base_path, qs = path.split("?", 1)
        params = urllib.parse.parse_qs(qs, keep_blank_values=True)
        # 还原单值
        params = {k: v[0] for k, v in params.items()}
        encoded = urllib.parse.urlencode(params, encoding="utf-8")
        full_url = f"{HOST}{base_path}?{encoded}"
    else:
        full_url = f"{HOST}{path}"
    req = urllib.request.Request(full_url)
    resp = urllib.request.urlopen(req, timeout=5)
    return json.loads(resp.read().decode("utf-8"))


# 1. 初始化
init = call("/api/vod")
print("=== 1. 初始化 (分类+过滤器) ===")
print(f"  code={init['code']}")
print(f"  分类: {[c['type_id'] for c in init['class']]}")
print(f"  过滤器: {list(init['filters'].keys())}")
print(f"  series 过滤条件: {[f['key'] for f in init['filters']['series']]}")
print()

# 2. 分类列表 - 电视剧
slist = call("/api/vod?ac=list&t=series")
print("=== 2. 分类列表 series ===")
print(f"  code={slist['code']}, total={slist['total']}, page={slist['page']}/{slist['pagecount']}")
for item in slist["list"][:3]:
    print(f"  - {item['vod_id']}: {item['vod_name']} [{item['vod_remarks']}]")
print()

# 3. 搜索
search = call("/api/vod?ac=list&wd=白")
print("=== 3. 搜索: 白 ===")
print(f"  total={search['total']}")
for item in search["list"][:3]:
    print(f"  - {item['vod_id']}: {item['vod_name']} [{item['vod_remarks']}]")
print()

# 4. 地区过滤 - 日本
f_jp = base64.b64encode(json.dumps({"country": "日本"}).encode()).decode()
filt = call(f"/api/vod?ac=list&t=series&f={f_jp}")
print("=== 4. 过滤: country=日本 ===")
print(f"  total={filt['total']}")
for item in filt["list"][:3]:
    print(f"  - {item['vod_name']} [{item['vod_remarks']}]")
print()

# 5. 年份范围过滤
f_y = base64.b64encode(json.dumps({"year": "2020-2029"}).encode()).decode()
filt2 = call(f"/api/vod?ac=list&t=series&f={f_y}")
print("=== 5. 过滤: year=2020-2029 ===")
print(f"  total={filt2['total']}")
for item in filt2["list"][:3]:
    print(f"  - {item['vod_name']} [{item['vod_remarks']}]")
print()

# 6. 组合过滤
f_both = base64.b64encode(json.dumps({"country": "日本", "year": "2020-2029"}).encode()).decode()
filt3 = call(f"/api/vod?ac=list&t=series&f={f_both}")
print("=== 6. 组合过滤: country=日本 + year=2020-2029 ===")
print(f"  total={filt3['total']}")
for item in filt3["list"][:5]:
    print(f"  - {item['vod_name']} [{item['vod_remarks']}]")
print()

print("=== 全部测试通过! ===")
