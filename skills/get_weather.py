import requests
import re

CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280101",
    "深圳": "101280601", "杭州": "101210101", "成都": "101270101",
    "南京": "101190101", "武汉": "101200101", "西安": "101110101",
    "重庆": "101040100", "天津": "101030100", "青岛": "101120201",
    "大连": "101070201", "沈阳": "101070101", "长春": "101060101",
    "哈尔滨": "101050101", "厦门": "101230201", "长沙": "101250101",
    "郑州": "101180101", "济南": "101120101", "苏州": "101190401",
    "昆明": "101290101", "贵阳": "101260101", "南宁": "101300101",
    "海口": "101310101", "拉萨": "101140101", "兰州": "101160101",
    "西宁": "101150101", "银川": "101170101", "乌鲁木齐": "101130101",
    "呼和浩特": "101080101", "石家庄": "101090101", "太原": "101100101",
    "南昌": "101240101", "合肥": "101220101", "福州": "101230101",
    "台北": "101340101", "香港": "101320101", "澳门": "101330101",
}

PINYIN_MAP = {v: k for k, v in {
    "beijing": "北京", "shanghai": "上海", "guangzhou": "广州",
    "shenzhen": "深圳", "hangzhou": "杭州", "chengdu": "成都",
    "nanjing": "南京", "wuhan": "武汉", "xian": "西安",
    "dalian": "大连", "shenyang": "沈阳", "qingdao": "青岛",
    "tianjin": "天津", "chongqing": "重庆",
}.items()}

def get_weather(city: str, days: int = 1) -> str:
    if city in CITY_CODES:
        code = CITY_CODES[city]
    elif city.lower().replace(" ", "") in PINYIN_MAP:
        code = CITY_CODES[PINYIN_MAP[city.lower().replace(" ", "")]]
    else:
        return f"未知城市：{city}。支持的城市列表：{', '.join(sorted(CITY_CODES.keys()))}"

    url = f"https://www.weather.com.cn/weather/{code}.shtml"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    resp.encoding = "utf-8"
    items = re.findall(r'<li class="sky.*?>(.*?)</li>', resp.text, re.DOTALL)

    results = []
    for i, item in enumerate(items):
        if i >= days:
            break

        m_title = re.search(r'<h1>(.*?)</h1>', item)
        m_wea = re.search(r'<p title="(.*?)" class="wea">', item)
        spans = re.findall(r'<span>(.*?)</span>', item)
        ies = re.findall(r'<i>(.*?)</i>', item)

        title = m_title.group(1).strip() if m_title else f"第{i+1}天"
        weather = m_wea.group(1).strip() if m_wea else "未知"

        # 今天：只有 <i>当前温度</i>，无 <span>
        # 明天及以后：<span>高温</span>/<i>低温</i>，<i>风力</i>
        if spans:
            high = spans[0]
            low = ies[0] if ies else ""
            wind = ies[1] if len(ies) > 1 else ""
            line = f"{title}：{weather}，{low}~{high}"
        else:
            current = ies[0] if ies else ""
            wind = ies[1] if len(ies) > 1 else ""
            line = f"{title}：{weather}，{current}"

        if wind:
            line += f"，{wind}"
        results.append(line)

    return "\n".join(results) if results else "解析失败：无法获取天气数据"
