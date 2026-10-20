"""Recognise business requests independently of incidental domain vocabulary."""
import re


def administrative_question(question):
    return bool(re.search(r'核定.{0,12}(?:工作|要求|进度|安排)|(?:工作要求|时间进度|修编周期|职责分工|部门职责|编制计划)|现行.{0,45}(?:预案|规范|标准)|(?:预案|规范|标准).{0,16}(?:关键变化|修订|修编|版本|有哪些)', question))


def analogue_question(question):
    return bool('台风' in question and re.search(r'相似|类似|比较像|哪一次.*像|历史.*像',question)
                and not re.search(r'算法|计算方法|怎么算|原理|技术方法',question))


def coastal_request(question):
    return bool(re.search(r'超警|超.{0,8}警戒|哪些岸段|哪个岸段|最大增水|最高潮位|总潮位|最不利叠加|叠加相位|天文潮.{0,12}(?:增水|叠加)|增水.{0,12}天文潮', question)
                or re.search(r'浙江(?:省)?(?:沿海|全省)?|全省|(?:嘉兴|宁波|舟山|台州|温州)(?:市)?沿海',question)
                   and re.search(r'增水|潮位|水位',question))


def grid_request(question):
    if re.search(r'浪高|波高|风浪|海温|水温|流速|谱峰周期|波向|流向', question):
        return True
    if not re.search(r'风力|风速', question):
        return False
    if not re.search(r'台风|热带气旋|气旋中心', question):
        return True
    # Centre intensity belongs to the typhoon API, not to the regional NC
    # wind field. An explicit local wind subquestion still requests a grid.
    local=r'海域|海面|近海|沿海|岸段|海岸|局地|当地|地面|网格|格点|附近|所在地|'
    local+=r'经纬|东经|北纬|\blon\b|\blat\b|(?:浙江|嘉兴|宁波|舟山|台州|温州)(?:市|省)?(?:的)?(?:风力|风速)'
    for part in re.split(r'[，,。；;？！!?、]|以及|同时|另外|并且|与|和',question):
        if not re.search(r'风力|风速',part):continue
        city_wind=(re.search(r'浙江|嘉兴|宁波|舟山|台州|温州',part)
                   and not re.search(r'中心.{0,12}(?:风力|风速)',part))
        if re.search(local,part,re.I) or city_wind:
            return True
    return False


def tide_request(question):
    return bool(re.search(r'天文潮|高潮|低潮|潮汐|退潮|涨潮|潮时', question))


def numeric_request(question):
    if administrative_question(question):
        return False
    # An explicit forecast subquestion still runs when another part asks why.
    query = bool(re.search(r'未来|明天|明日|后天|今天|当前|(?<!出)现在|本次|这次|预报|查询|多少|几点|哪个|哪些|最大|最不利|20\d{2}年\d{1,2}月|\d{1,2}月\d{1,2}日|20\d{2}[-/]', question))
    if not query:
        return False
    method = bool(re.search(r'什么是|定义|是什么意思|什么不同|有何不同|区别|差别|一回事|原理|如何计算|怎么算|计算方法|怎么划分|如何划分|重现期|年一遇|设计|爬高|情景矩阵|误差|准确率|同化|海平面上升|地面沉降|风力.{0,8}对应.{0,8}浪|船只.{0,8}等级.{0,8}出海', question))
    dated_values = bool(re.search(r'未来\d+|(?:今天|明天|明日|后天).{0,30}(?:多少|查询|最大)|20\d{2}年\d{1,2}月\d{1,2}日.{0,120}(?:预报|多少|查询|最大|最不利)', question))
    # A numeric clause can precede its date and coexist with a separate
    # definition clause. Do not let “波向定义” suppress “查询某时某坐标浪高”.
    dated_values = dated_values or any(
        re.search(r'(?:查询|提取|预报).{0,15}20\d{2}(?:年|[-/])',part)
        and (grid_request(part) or tide_request(part) or coastal_request(part))
        for part in re.split(r'[；;。？！!?]',question))
    if not dated_values and re.search(r'热带气旋等级|按.{0,20}(?:等级|分级)标准|分在哪个等级', question):
        return False
    if method and not dated_values:
        return False
    if re.search(r'淹没|易淹|倒灌|灾损|损失|风险区|隐患|高风险|避险|防御|避护|停航|降级|终止|预警解除|数据可用|有没有缺测|哪些潮位站|哪些.*浮标|雷达|卫星|应急响应|五停', question):
        # Those domains need their own sources/models; vocabulary is not a
        # license to substitute a representative tide for the requested result.
        return False
    return coastal_request(question) or grid_request(question) or tide_request(question) or bool(re.search(r'潮位|增水|岸段', question))
