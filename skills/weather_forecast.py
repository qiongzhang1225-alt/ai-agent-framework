
def weather_forecast(city: str, days: int = 1) -> str:
    """查询指定城市未来几天的天气预报。
    
    参数:
        city: 城市名（中文或拼音均可）
        days: 1=明天，2=后天……（最多7天）
    
    返回:
        天气预报信息文本
    """
    import urllib.request
    import re
    
    # 城市代码映射
    city_codes = {
        "北京": "101010100", "上海": "101020100", "天津": "101030100",
        "重庆": "101040100", "哈尔滨": "101050101", "长春": "101060101",
        "沈阳": "101070101", "大连": "101070201", "呼和浩特": "101080101",
        "石家庄": "101090101", "太原": "101100101", "济南": "101120101",
        "青岛": "101120201", "郑州": "101180101", "西安": "101110101",
        "南京": "101190101", "苏州": "101190401", "杭州": "101210101",
        "武汉": "101200101", "长沙": "101250101", "南昌": "101240101",
        "福州": "101230101", "厦门": "101230201", "广州": "101280101",
        "深圳": "101280601", "南宁": "101300101", "成都": "101270101",
        "昆明": "101290101", "贵阳": "101260101", "拉萨": "101140101",
        "乌鲁木齐": "101130101", "兰州": "101160101", "西宁": "101150101",
        "银川": "101170101", "海口": "101310101", "香港": "101320101",
        "澳门": "101330101",
    }
    
    # 匹配城市
    code = None
    if city in city_codes:
        code = city_codes[city]
    else:
        for name, c in city_codes.items():
            if name in city or city in name:
                code = c
                break
    
    if not code:
        pinyin_map = {
            "beijing": "北京", "shanghai": "上海", "tianjin": "天津",
            "chongqing": "重庆", "dalian": "大连", "guangzhou": "广州",
            "shenzhen": "深圳", "chengdu": "成都", "hangzhou": "杭州",
            "nanjing": "南京", "wuhan": "武汉", "xian": "西安",
            "shenyang": "沈阳", "qingdao": "青岛", "kunming": "昆明",
        }
        cl = city.lower().replace(" ", "")
        if cl in pinyin_map:
            code = city_codes.get(pinyin_map[cl])
    
    if not code:
        return f"错误：未知城市「{city}」"
    
    url = f"https://www.weather.com.cn/weather/{code}.shtml"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        return f"获取天气数据失败：{e}"
    
    # 解析7天预报
    items = re.findall(
        r'<li>.*?<h1>(.*?)</h1>.*?<p class="wea">(.*?)</p>.*?<p class="tem">.*?<span>(.*?)</span>.*?<i>(.*?)</i>',
        html, re.DOTALL
    )
    
    if not items:
        return "错误：无法解析天气预报数据"
    
    if days < 1 or days > len(items):
        return f"错误：days 超出范围（1-{len(items)}）"
    
    item = items[days - 1]
    date_text = item[0].strip()
    weather_text = item[1].strip()
    temp_high = item[2].strip()
    temp_low = item[3].strip() if len(item) > 3 else ""
    
    # 获取风力
    wind = ""
    for sec in html.split('<li>'):
        if f'<h1>{date_text}</h1>' in sec:
            wm = re.search(r'<p class="win">.*?<i>(.*?)</i>', sec, re.DOTALL)
            if wm:
                wind = wm.group(1).strip()
            break
    
    temp_str = f"{temp_high}°C / {temp_low}°C" if temp_low and temp_low != temp_high else f"{temp_high}°C"
    result = f"{city} {date_text}：{weather_text}，{temp_str}"
    if wind:
        result += f"，{wind}"
    
    return result
