"""Public subject/action lenses shared by search, excerpting and evidence checks.

Only controlled public vocabulary leaves for discovery. These are question
families, not stored answers, question IDs, operational limits or live states.
"""
import re

MOSQUITO = r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾'
AQUACULTURE = r'网箱|鱼排|养殖塘|养殖池塘|池塘|养殖设施'
RELIEF = r'政府救助|财政救助|救灾救助|生活救助|(?:受灾|灾后|台风).{0,30}(?:申请|获得|领取|哪些|什么).{0,10}(?:救助|补助)|(?:申请|哪些|什么).{0,12}(?:救助|补助)'
INSURANCE = r'保险|理赔|赔付|能赔|获赔|赔偿'
CHANNELS = r'(?=.*(?:预警|警报))(?=.*(?:渠道|哪里|在哪|何处|平台|收到|接收|订阅|从哪|怎样查询|如何查询|怎么查询|替代))'


def families(question):
    return [name for name, pattern in [('mosquito', MOSQUITO), ('aquaculture', AQUACULTURE),
            ('disaster_relief', RELIEF), ('warning_channels', CHANNELS)] if re.search(pattern, question)]


def public_queries(question):
    """Preserve the actual domain object instead of broad marine keywords."""
    queries = []
    if re.search(MOSQUITO, question):
        queries += ['洪涝 灾后 蚊虫 蚊媒传染病 风险 防蚊 site:nhc.gov.cn',
                    '防蚊灭蚊 清除积水 孳生地 长袖 蚊帐 site:nhc.gov.cn',
                    '灾后 蚊虫 传播 防蚊 长袖 积水 site:gov.cn']
    if re.search(AQUACULTURE, question) and re.search(r'加固|防台|台风|防灾|修复|检查', question):
        if re.search(r'网箱|鱼排|养殖设施', question):
            queries += ['台风 海水鱼 网箱 锚泊 缆绳 框架 加固 技术手册 site:moa.gov.cn']
        if re.search(r'池塘|养殖塘|养殖设施', question):
            queries += ['台风 水产养殖 池塘 堤坝 进排水 防灾 技术指引 site:moa.gov.cn']
    if re.search(RELIEF, question):
        queries += ['自然灾害 受灾人员 应急生活 过渡期 冬春 住房重建 救助 site:mem.gov.cn',
                    '自然灾害 生活救助 申请 户报 村评 乡审 县定 site:mem.gov.cn']
    if re.search(INSURANCE, question):
        queries += ['保险法 保险合同 保险事故 赔偿 请求 保险人 site:gov.cn']
        if '巨灾保险' in question:
            queries += ['巨灾保险 政府救助 保险合同 理赔 site:gov.cn']
    if re.search(CHANNELS, question):
        # A local forecast product is not a meteorological warning channel.
        if re.search(r'台风|气象', question):
            scope = '浙江' if re.search(r'浙江|宁波|舟山|温州|台州|鄞州', question) else ''
            queries += [f'{scope} 气象 台风 预警 发布平台 查询 site:weather.com.cn'.strip(),
                        f'{scope} 气象 预警 官方 查询 渠道 site:cma.gov.cn'.strip()]
        if re.search(r'海洋|海浪|风暴潮', question):
            queries += ['浙江 海洋预报网 灾害预警 官方 发布 渠道 site:gov.cn'] if re.search(
                r'浙江|宁波|舟山|温州|台州|鄞州', question) else ['海洋灾害 预警 官方 查询 渠道 site:mnr.gov.cn']
        if re.search(r'路径',question) and re.search(r'替代|代替|区别|不同|一样',question):
            queries += ['台风 路径预报 预警信号 发布 定义 区别 site:cma.gov.cn']
    return queries


def focus_words(question):
    words = []
    if re.search(MOSQUITO, question):
        words += ['蚊', '登革热', '基孔肯雅', '乙脑', '疟疾', '孳生', '积水', '长袖', '驱蚊', '蚊帐', '就医']
    if re.search(AQUACULTURE, question):
        words += ['网箱', '锚泊', '缆绳', '框架', '网衣', '池塘', '堤坝', '堤埂', '进排水', '排水', '撤离']
    if re.search(RELIEF, question):
        words += ['应急生活救助', '过渡期', '住房恢复重建', '冬春', '救助对象', '申请', '村', '评议', '公示', '审核']
    if re.search(INSURANCE, question):
        words += ['保险合同', '保险人', '赔偿', '请求', '证明和资料', '报案', '保险责任']
    if re.search(CHANNELS, question):
        words += ['预警发布平台', '台风', '预警信号', '查询', '网站', '微信公众号', '灾害预警']
    return words


def focus_groups(question):
    """Independent evidence needs; one popular paragraph cannot fill all."""
    groups = []
    if re.search(MOSQUITO, question):
        groups += [r'蚊.{0,30}(?:传播|传染)|蚊媒传染病|登革热', r'清除.{0,20}积水|孳生', r'长袖|蚊帐|防叮咬']
    if re.search(AQUACULTURE, question):
        if re.search(r'网箱|鱼排|养殖设施', question): groups += [r'锚泊|缆绳|网箱框架', r'网衣|盖网']
        if re.search(r'池塘|养殖塘|养殖设施', question): groups += [r'池塘.{0,30}(?:堤|进排水|加固)|池堤|堤埂', r'排水|排淡|进排水']
    if re.search(RELIEF, question):
        if re.search(r'住房|房屋|重建',question):
            groups += [r'户报.{0,8}村评|申请.{0,20}(?:村|居)民', r'救助对象|恢复重建救助对象']
        groups += [r'应急生活救助|紧急生活救助', r'过渡期生活救助', r'冬春.{0,10}救助',
                   r'住房.{0,8}恢复重建救助|倒损住房', r'户报.{0,8}村评|申请.{0,20}(?:村|居)民|申请.{0,10}救助']
    if re.search(INSURANCE,question):
        groups += [r'本法所称保险|按照保险合同请求', r'保险人收到.{0,30}赔偿|向保险人提供']
    if re.search(CHANNELS,question) and '路径' in question:
        groups += [r'路径预报', r'预警信号.{0,30}(?:是指|组成)|名称、图标、标准和防御指南']
    return groups


def supports_subject(part, text):
    """Reject topic drift; semantic completeness is still evaluated by CRAG."""
    if re.search(MOSQUITO, part) and not re.search(MOSQUITO, text):
        return False
    if re.search(r'网箱|鱼排', part) and not re.search(r'网箱|鱼排', text):
        return False
    if re.search(r'池塘|养殖塘', part) and not re.search(r'池塘|养殖塘', text):
        return False
    if re.search(RELIEF, part) and not re.search(INSURANCE, part):
        # Insurance is a separate route, even when its name contains 救助.
        fiscal = re.search(r'应急生活救助|过渡期.{0,8}救助|冬春.{0,8}救助|恢复重建救助|临时救助|自然灾害救助条例|救助款物|救助资金', text)
        if re.search(r'保险|赔付|保费|承保', text) and not fiscal:
            return False
    return True


GENERATION_GUIDANCE = (
    '按用户所问业务对象组织结论与步骤。国家部门的通用防蚊、养殖技术和灾害救助资料可说明一般做法，'
    '不因用户说了浙江或宁波就把国家层面方法全部拒绝；地方资格、金额、期限、现场状态仍需对应证据。'
    '政府财政救灾救助与商业保险、公共巨灾保险赔付分别说明，不能用保险表替代政府救助类别及办理流程。'
    '网箱与池塘分别引用技术操作原文，不能用海塘、山塘或人员撤离统计替代设施加固方法。'
    '蚊媒风险不等于当地已发生疫情；回答传播风险与防护办法，不凭蚊虫增多断言正在流行。'
    '只回答所问范围：通用步骤题即便提到地区，也不额外要求当地专用标准、疫情实况、补助金额或工程参数。'
    '用户未问的地方金额和现场条件可用一句适用范围提示，不列成阻止一般方法回答的缺项。'
    '查询渠道题列已核验的入口名称、地址、查什么即可；没有要求穷尽所有渠道时，不额外要求完整名单、合作后的新增平台或独立平台网址。'
    '只问设施检查加固时，不扩展到养殖用药和饲喂方案；只问能否赔付时，先解释合同条件及报案取证，不展开未问的所有法定期限。'
    '防蚊回答先清积水和物理防护；未问配药、治疗时不提供杀虫剂配方、投药周期或治疗用药。'
    '原文将鼠类和蚊虫合写时，不把鼠传疾病都说成蚊媒疾病；蚊虫多只能提示风险，不能据此认定当地暴发登革热。'
)
