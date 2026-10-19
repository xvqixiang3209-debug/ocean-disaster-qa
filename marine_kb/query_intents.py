"""Small retrieval intents, containing search vocabulary rather than answers.

These apply to unseen wording and coexist with BM25 and the local semantic
index. A mention of a topic alone never establishes supporting evidence.
"""
import re
from .intent_lenses import HOUSEHOLD,WAVE_GRADING,WARNING_COMPARE,WARNING_LEVELS,ALERT_CHANNELS,STANDARDS_CATALOG

# Action wording is an intent, while hazard/topic matching stays separate.
DEFENSIVE_ACTION_QUESTION = r'(?:必须|应当|应该|需要|该|要)(?:采取什么(?:行动|措施)|做(?:些)?什么|怎么(?:做|行动))'

PROFILES = {
    'seasonality': {
        'trigger': r'(?:台风|热带气旋).{0,35}(?:季节|月份|几月|夏[季天]|秋冬|冬[季天]|全年|一年四季|高发|活跃|集中)|(?:季节|月份|夏[季天]|秋冬|冬[季天]|全年|一年四季).{0,25}(?:台风|热带气旋)',
        'queries': ('台风 季节 活跃月份 高发 全年 夏秋', '热带气旋 生成月份 季节分布'),
        'subject': r'台风|热带气旋',
        'support': r'全年|一年四季|各[个]?季节|季节分布|活跃(?:期|月份)|高发(?:期|季节)|集中.{0,12}(?:月|季)|[一二三四五六七八九十\d]{1,2}\s*[—–~至到-]\s*[一二三四五六七八九十\d]{1,2}\s*月',
    },
    'return_period': {
        'trigger': r'重现期|[十百千万\d]+年一遇|年发生概率|年超越概率',
        'queries': ('重现期 年超越概率 年一遇 含义',),
        'subject': r'重现期|年一遇|超越概率',
        'support': r'概率|平均.{0,15}(?:年|次数)|不(?:是|代表|意味).{0,15}(?:一次|发生|间隔)',
    },
    'storm_definition': {
        'trigger': r'(?:台风|热带气旋).{0,20}(?:是什么|什么是|定义|区别|一回事)|什么是.{0,8}(?:台风|热带气旋)',
        'queries': ('台风 定义 热带气旋',),
        'subject': r'台风|热带气旋',
        'support': r'台风(?:是|指|属于)|热带气旋(?:是|指)|(?:最大|中心).{0,12}风速',
    },
    'wave_definition': {
        'trigger': r'(?:有效波高|海浪|波浪).{0,35}(?:是什么|什么意思|什么是|定义|区别|什么不同|有何不同|差别)|什么是.{0,8}(?:有效波高|海浪|波浪)',
        'queries': ('有效波高 海浪 定义 风浪 涌浪',),
        'subject': r'有效波高|海浪|波浪',
        'support': r'平均|三分之一|1/3|风浪和涌浪|海浪(?:是|指)|波浪(?:是|指)',
    },
}

# Technical question families use their operative vocabulary, rather than
# treating every request as the broad phrase “海洋灾害”. No thresholds or
# answers are stored in these retrieval profiles.
PROFILES.update({
    'defensive_actions': {
        'trigger': r'(?=.*(?:台风|海浪|风暴潮|海啸|海洋灾害|预警|警报))(?=.*(?:' + DEFENSIVE_ACTION_QUESTION + r'))',
        'queries': ('海洋灾害 公众 防御指南 避险措施',),
        'subject': r'台风|海浪|风暴潮|海啸|海洋灾害|预警|警报',
        'support': r'防御指南|防御措施|避险|(?:居民|公众|群众).{0,15}(?:远离|停止|撤离|转移|听从)|不要|避免'},
    'alert_channels':{
        'trigger':ALERT_CHANNELS,
        'queries':('官方预警 发布 接收 渠道 订阅 微信 短信 应急广播',),
        'subject':r'预警|警报',
        'support':r'渠道|订阅|公众号|微信|微博|短信|广播|发布平台'},
    'standards_catalog':{
        'trigger':STANDARDS_CATALOG,
        'queries':('海洋 观测 预报 国家标准 行业标准 目录','海洋防灾减灾 标准 名称 编号 实施日期'),
        'subject':r'标准|规范|GB/T|HY/T',
        'support':r'标准号|标准编号|发布日期|实施日期|现行|废止|GB/T|HY/T'},
    'warning_level_meanings':{
        'trigger':WARNING_LEVELS,
        'queries':('四色警戒潮位说明 蓝色 黄色 橙色 红色 戒备','警戒潮位 分级 定义 防御指南'),
        'subject':r'警戒潮位',
        'support':r'蓝色警戒潮位|黄色警戒潮位|橙色警戒潮位|红色警戒潮位|四色警戒潮位'},
    'household_supplies':{
        'trigger':HOUSEHOLD,
        'queries':('家庭 应急物资 储备 建议清单 饮用水 每人','全国基础版 家庭应急物资 储备清单'),
        'subject':r'家庭|每人',
        'support':r'饮用水|备水|储备清单'},
    'wave_grading':{
        'trigger':WAVE_GRADING,
        'queries':('海浪级别划分 有效波高 巨浪 狂浪','有效波高 最大波高 定义'),
        'subject':r'有效波高|Hs|海浪级别',
        'support':r'海浪级别划分|附表.{0,8}海浪|[≤＜<>].{0,5}Hs|Hs.{0,5}[≤＜<>]|前.{0,4}1/3'},
    'warning_compare':{
        'trigger':WARNING_COMPARE,
        'queries':('台风预警信号 风力 风暴潮警报 警戒潮位 区别','台风预警信号 含义'),
        'subject':r'台风预警|风暴潮|台风警报',
        'support':r'预警信号|警戒潮位|高潮位|监测预警|风雨'},
    'forecast_product_formats':{
        'trigger':r'预警预报产品|产品.*(?:格式|业务标准|制作|字段)',
        'queries':('防台期间 预警报产品 发布细则','风暴潮 海浪 预报产品 制作规范','海上大风浪 事件 发布制度'),
        'subject':r'预警报|预报|海浪|产品',
        'support':r'产品|报头|编号|签发|格式|发布'},
    'observation_quality':{
        'trigger':r'质控|质量控制|尖峰|连续不变|恒定检验|卡值|异常值|观测设备|缺测.*补|补测|补录',
        'queries':('海洋观测 质量控制 异常值 缺测 数据处理','潮位 浮标 数据质量控制'),
        'subject':r'浮标|潮位|观测|数据质量',
        'support':r'(?:格式|范围|时间|连续性|尖峰|恒定|相关性|缺测|递增性|一致性|增减水).{0,3}检验|数据(?:可疑|异常)|质量标识|观测.*异常'},
    'loss_statistics':{
        'trigger':r'灾情.*统计|灾情快报|报送.*指标|死亡.*核报|直接经济损失|计损口径',
        'queries':('自然灾害情况统计调查制度 直接经济损失 死亡 失踪 核报','海洋灾害 调查评估 统计指标 直接经济损失','海洋灾情 信息报告 报送时限'),
        'subject':r'海洋灾害|灾情|灾害损失',
        'support':r'统计|填报|报送|核报|直接经济损失|人员伤亡'},
    'warning_criteria':{
        'trigger':r'警戒潮位.*(?:怎么|核定)|警报.*(?:判据|代表站|发布|格式)|近岸海域.*近海海域|海洋灾害预警.*更新',
        'queries':('浙江省海洋灾害联合会商与信息发布实施细则 代表站','海洋灾害 预警报 发布条件 代表站 技术规范','风暴潮 海浪 警报 产品制作规范'),
        'subject':r'警报|预警|警戒潮位',
        'support':r'发布|签发|有效波高|级别|分级|代表站|更新'},
    'representative_stations':{
        'trigger':r'代表(?:性)?(?:潮位|验潮)?站(?:点)?|(?:潮位站|验潮站).{0,12}(?:选取|怎么选|如何选|代表性)|选站',
        'queries':('风暴潮 代表站点 潮位站 核定岸段', '代表站 选取 代表性 站点 职责'),
        'subject':r'代表(?:性)?(?:潮位|验潮)?站(?:点)?|潮位站|验潮站',
        # A list assigned to coastal sections is useful evidence for which
        # stations a document uses, but not proof of a complete siting rule.
        # A generic product column headed "代表站" does not meet this signal.
        'support':r'为代表站|代表站点|核定岸段|(?:选取|选择|选定|布设|代表性).{0,20}(?:站|岸段)|(?:站点|站址|潮位站|验潮站).{0,20}(?:选取|原则|代表性)'},
    'risk_methods':{
        'trigger':r'风险评估|区划|承灾体|脆弱性|损失率|淹没.*(?:模拟|方法)|模拟.*(?:输入|验证)|海堤.*(?:设计|修复)|波浪爬高|设计高潮位',
        'queries':('海洋灾害 风险评估 危险性 淹没模拟 技术方法','海洋灾害 承灾体 调查 损失率 验证 精度'),
        'subject':r'风险|危险性|淹没|承灾体|海堤|波浪|地形',
        'support':r'调查|方法|模型|验证|精度|参数|公式|损失率'},
    'response_duties':{
        'trigger':r'职责|责任主体|修编|演练|培训|预案.*程序|启动.*响应|响应.*(?:降级|终止)',
        'queries':('海洋灾害 应急预案 职责分工 响应程序','海洋灾害 应急预案 培训 演练 修订'),
        'subject':r'海洋灾害|应急预案|海洋部门',
        'support':r'职责|指挥部|批准|办公室|培训|演练|修订'},
    'tide_datum':{
        'trigger':r'潮位基准|基准面|高程基准|数字不能直接比',
        'queries':('潮位基准面 高程基准 换算 统一','验潮零点 理论最低潮面 1985国家高程'),
        'subject':r'潮位|潮面|基准面|高程',
        'support':r'基准|零点|换算|1985|吴淞'},
    'cyclone_grades':{
        'trigger':r'热带风暴|强热带风暴|强台风.*怎么分|超强台风.*怎么分|热带气旋.*分级',
        'queries':('热带气旋 等级 划分 最大平均风速','台风 强度 等级 GB/T32935'),
        'subject':r'热带气旋|台风|强热带风暴',
        'support':r'平均风速|等级划分|划分指标|分级|底层中心'},
    'spike_quality':{
        'trigger':r'尖峰|毛刺|突跳',
        'queries':('尖峰检验 连续性 梯度检验',),
        'subject':r'尖峰|毛刺|突跳',
        'support':r'尖峰检验|周围观测值|连续性检验'},
    'flat_quality':{
        'trigger':r'连续不变|恒定|粘滞|卡值|卡死',
        'queries':('恒定检验 粘滞检验 卡值检验',),
        'subject':r'恒定|粘滞|卡值',
        'support':r'恒定检验|连续性检验|数据可疑'},
})


def intents(question):
    return [key for key, spec in PROFILES.items() if re.search(spec['trigger'], question)]


def focused_queries(question):
    """Keep identity constraints for event-specific requests; never change a date.

Focused generic searches supply possible explanatory material only. Original
question reranking still enforces its event/year/region and review constraints.
"""
    return list(dict.fromkeys(query for key in intents(question) for query in PROFILES[key]['queries']))


def child_queries(question,parts):
    """Inherit an unambiguous event identity, never borrow another event's data."""
    from .retrieval import STORM_ALIASES
    names=[name for name,english in STORM_ALIASES.items() if name in question or english in question.lower()]
    years=list(dict.fromkeys(re.findall(r'(?:19|20)\d{2}年',question)))
    output=[]
    for part in parts:
        value=part.get('question','') if isinstance(part,dict) else str(part)
        if not value or value==question:continue
        if names and not any(name in value or STORM_ALIASES[name] in value.lower() for name in names):
            if len(names)>1:continue  # An unresolved pronoun cannot choose a storm.
            value=(years[0] if len(years)==1 else '')+names[0]+' '+value
        output.append(value)
    return output


def support_score(question, body, title=''):
    score = 0.0
    text = re.sub(r'\s+', '', str(body))
    for key in intents(question):
        spec = PROFILES[key]
        if re.search(spec['subject'], text) and re.search(spec['support'], text):
            score += 4.0
            if re.search(r'科普|定义|常识|季节|重现期|年一遇', title):
                score += 1.0
    return score
