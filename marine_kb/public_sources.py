"""Primary-source discovery routes, never stored question answers.

These URLs are fetched and checked at request time. Papers support methods,
never local opening, permission, current observations or adopted thresholds.
"""
import re
from .intent_lenses import HOUSEHOLD,WAVE_GRADING,MARINE_RESEARCH,WARNING_COMPARE,WARNING_LEVELS,WARNING_TECHNICAL,STANDARDS_CATALOG

ACADEMIC_DOMAINS=('arxiv.org','aclanthology.org','nhess.copernicus.org','gmd.copernicus.org','nora.nerc.ac.uk')
QA_EVIDENCE_RE=r'(?:问答|回答).*(?:证据|引用|溯源)|(?:检索|搜索).*(?:纠错|改进|不相关)|搜错资料|改检索'
PROGRESS_RE=r'(?:海洋|海平面|海浪|风暴潮|海啸).*(?:技术进展|研究进展|最新进展|前沿技术|技术有哪些.*进展)'
RESEARCH_RE=re.compile(r'RAG|检索增强|知识库|大模型|智能体|幻觉|同化|率定|订正|脆弱性|损失率曲线|频率曲线|淹没模拟|情景矩阵|遥感.*验证|警戒潮位.*(?:核定|怎么定)|承灾体|技术流程|'+QA_EVIDENCE_RE+'|'+PROGRESS_RE+'|'+MARINE_RESEARCH,re.I)

# (intent, title, URL, scope). This is a source catalogue, not an answer bank.
PRIMARY_ROUTES=(
 (r'(?=.*台风)(?=.*路径)(?=.*(?:预警|警报))(?=.*(?:区别|替代|代替|不同|一样))',
  '台风的预报与防御 中国气象局',
  'https://www.cma.gov.cn/kppd/kppdqxsj/kppdqxgc/201212/t20121212_195831.html','national_guidance'),
 (r'(?=.*台风)(?=.*路径)(?=.*(?:预警|警报))(?=.*(?:区别|替代|代替|不同|一样))',
  '气象灾害预警信号发布与传播管理办法 中国气象局公开版本',
  'https://www.cma.gov.cn/zfxxgk/gknr/flfgbz/gz/202005/P020220628619411458474.pdf','national_framework'),
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾',
  '洪涝后如何做好健康防护 中国政府网',
  'https://www.gov.cn/yaowen/liebiao/202408/content_6966289.htm','national_guidance'),
 (r'(?=.*(?:预警|警报))(?=.*(?:渠道|哪里|在哪|平台|查询|接收|从哪|替代))',
  '天气预警 浙江天气网',
  'https://www.weather.com.cn/zhejiang/tqyj/index.shtml','service_directory'),
 (r'(?=.*(?:预警|警报))(?=.*(?:渠道|哪里|在哪|平台|查询|接收|从哪|替代))',
  '气象灾害预警 中央气象台',
  'https://www.nmc.cn/f/alarm.html','service_directory'),
 (r'保险|理赔|赔付|能赔|获赔|赔偿',
  '中华人民共和国保险法 司法部行政复议与应诉局公开版本',
  'https://xzfy.moj.gov.cn/c/2021-01-06/487603.shtml','national_framework'),
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾',
  '台风天就医防病避险 上海政府网 蚊媒疾病与防护通用知识',
  'https://www.shanghai.gov.cn/nw17239/20260811/5fa49594f56d4ea699331888e2db68e1.html','technical_reference'),
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾',
  '台风过后注意灾后防疫 国家应急广播网',
  'https://www.cneb.gov.cn/2018/09/18/ARTI1537226470896187.shtml','national_guidance'),
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾',
  '防蚊灭蚊宣传知识要点 国家卫生健康委员会',
  'https://www.nhc.gov.cn/jkj/c100062/201602/9c34a03ea1c24857a529da540cc5cfe6.shtml','national_guidance'),
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅|乙脑|疟疾',
  '国家卫生健康委员会2026年4月27日新闻发布会文字实录 蚊媒传染病防控',
  'https://www.nhc.gov.cn/xcs/c100122/202604/3945a950329a48d6abc5792dabe86fcb.shtml','national_guidance'),
 (r'(?=.*(?:网箱|鱼排|养殖塘|池塘|养殖设施))(?=.*(?:台风|防台|加固|防灾|修复|检查))',
  '台风灾害下海水鱼养殖防灾减灾技术手册 国家海水鱼产业技术体系',
  'https://yyj.moa.gov.cn/gzdt/202409/P020250219610458020453.pdf','national_guidance'),
 (r'(?=.*(?:网箱|鱼排|养殖塘|池塘|养殖设施))(?=.*(?:台风|防台|加固|防灾|修复|检查))',
  '汛期和干旱期间水产养殖防灾减灾技术指引 2024年版 农业农村部',
  'https://yyj.moa.gov.cn/gzdt/202406/t20240621_6457580.htm','national_guidance'),
 (r'政府救助|财政救助|生活救助|申请.{0,12}(?:救助|补助)|(?:受灾|灾后|台风).{0,25}救助',
  '国家自然灾害救助应急预案 2024年印发版本',
  'https://www.gov.cn/zhengce/zhengceku/202402/content_6930039.htm','national_framework'),
 (r'政府救助|财政救助|生活救助|申请.{0,12}(?:救助|补助)|(?:受灾|灾后|台风).{0,25}救助',
  '应急管理部2024年10月22日新闻发布会 冬春救助办理程序',
  'https://www.mem.gov.cn/xw/xwfbh/2024n10y22xwfbh/','national_guidance'),
 (r'(?=.*(?:浙江|宁波|舟山|温州|台州|鄞州))(?=.*(?:台风|气象))(?=.*(?:渠道|哪里|在哪|平台|接收|收到|订阅|从哪))',
  '浙江省气象灾害预警发布平台 浙江天气网',
  'https://zj.weather.com.cn/zwfww/tqyj/index.shtml','service_directory'),
 (r'(?=.*(?:浙江|海洋))(?=.*(?:官方渠道|查询渠道|从哪|哪里查|怎么接收|渠道有哪些))',
  '浙江海洋预报网发布范围与公众服务介绍 浙江省经济信息中心',
  'https://zjic.zj.gov.cn/zkdt/rdzx/202104/t20210401_6497373.shtml','technical_reference'),
 (r'海水倒灌|风暴潮.{0,12}(?:成因|形成)',
  '南方部分地区海水倒灌现象三问 国家海洋环境监测中心转载专家解释',
  'https://www.nmemc.org.cn/hyyw/202411/t20241122_1095903.shtml','technical_reference'),
 (r'海平面(?:上升|变化)|地面沉降',
  '2020年度中国海洋灾害公报和中国海平面公报发布 自然资源部门户网站转载',
  'https://www.tianshui.gov.cn/zrzy/info/1832/603972.htm','technical_reference'),
 (r'近岸海域|近海海域',
  '海洋灾害应急预案 2022年印发版本的术语解释',
  'https://www.gov.cn/zhengce/zhengceku/2022-09/04/5708229/files/08090d20496748de8e02d412cba9348f.pdf','technical_reference'),
 (r'(?=.*(?:宁波|鄞州|镇海))(?=.*(?:停水|水压|自来水|供水|报修))',
  '宁波水务办事服务信息 清泉热线及报修方式',
  'https://www.zh.gov.cn/art/2023/12/26/art_1229778205_59315885.html','technical_reference'),
 (r'停电|电力报修|供电|电力抢修',
  '国家电网网上国网 故障报修与客服电话',
  'https://www.bl.gov.cn/zffw/cydh_all.shtml','technical_reference'),
 (r'(?=.*(?:宁波|鄞州|象山|镇海|慈溪|余姚|奉化|海曙|北仑))(?=.*(?:保险|赔付|赔偿|理赔|救助))',
  '宁波市人民政府办公厅关于印发宁波市公共巨灾保险工作实施方案（2024—2026年）的通知',
  'https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/jcms_files/jcms1/web3776/site/attach/0/7dd1311585f7463ea281d33dfbaf286d.pdf','versioned_standard'),
 (r'历史.*(?:最严重|死亡|淹没)|最严重.*风暴潮',
  '风暴潮灾害科普知识（青岛市海洋主管部门历史资料，非全国完整排名）',
  'https://ocean.qingdao.gov.cn/hykj/202110/t20211030_3700679.shtml','technical_reference'),
 (r'灾情.*统计|灾情快报|核报|计损口径|直接经济损失.*(?:核算|定义|范围)|死亡.*(?:认定|口径)|失踪.*(?:认定|统计)',
  '自然灾害情况统计调查制度（应急管理部，2024年3月）',
  'https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202403/W020240318718425077200.pdf','technical_reference'),
 (r'受灾.*救助|申请.*救助|倒塌.*住房|重建.*救助|救助.*申请|倒损住房|住房.{0,10}恢复重建',
  '因灾倒塌损坏住房恢复重建救助工作规范（应急管理部，2023）',
  'https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202307/t20230719_456671.shtml','technical_reference'),
 (r'(?=.*(?:宁波|鄞州|象山|镇海|慈溪|余姚|奉化|海曙|北仑))(?=.*(?:保险|赔付|赔偿|理赔|救助))',
  '巨灾保险宁波样本十年蝶变（宁波市地方金融监管局，2024年2月历史介绍）',
  'https://jrb.ningbo.gov.cn/art/2024/2/7/art_1229024326_58898960.html','technical_reference'),
 (r'(?=.*(?:宁波|鄞州|象山|镇海|慈溪|余姚|奉化|海曙|北仑))(?=.*(?:保险|赔付|赔偿|理赔|救助))',
  '2024年宁波国家保险创新综合试验区建设十大事件（公开2024—2026保险期限）',
  'https://jrb.ningbo.gov.cn/art/2025/1/10/art_1229024326_58900022.html','technical_reference'),
 (r'数据同化|同化方案|增水.*同化|同化.*增水',
  'Variational data assimilation of sea surface height into a regional storm surge model: Benefits and limitations (North Sea, 2021)',
  'https://nora.nerc.ac.uk/id/eprint/529769/1/Variational%20data%20assimilation%20of%20sea%20surface%20height%20into%20a%20regional%20storm%20surge%20model%20Benefits%20and%20limitations.pdf','research_method'),
 (r'数据同化|同化方案|增水.*同化|同化.*增水',
  'Implicit Assimilation of Sparse In Situ Data for Dense & Global Storm Surge Forecasting (Ebel et al., 2024)',
  'https://arxiv.org/html/2404.05758v1','research_method'),
 (WARNING_LEVELS+'|'+WAVE_GRADING,
  '2023年宁波市海洋灾害公报（附录四色警戒潮位及海浪级别说明）',
  'https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/jcms_files/jcms1/web3506/site/attach/0/bf561100ca6b4c7ea721fd19e4c0d4f6.pdf','general_safety'),
 (r'消毒|清理|清淤|泡过|水淹|饮用水.*(?:污染|恢复)|灾后.*卫生',
  '洪涝灾区环境卫生处置与预防性消毒指引（2023版，国家疾控局）',
  'https://www.ndcpa.gov.cn/jbkzzx/yqxxxw/common/content/content_1696053564394565632.html','general_safety'),
 (r'(?=.*(?:国内|中国))(?=.*(?:海洋灾害|台风))(?=.*(?:进展|技术|研究))',
  '科技铸盾防御台风：监测预报的智慧升级与实践创新（中国气象局，2025）',
  'https://www.cma.gov.cn/ztbd/2025zt/20250418/2025041803/202506/t20250616_7140902.html','technical_reference'),
 (WARNING_COMPARE,
  '国家防汛抗旱应急预案（2022年印发，部门监测与防台职责参考）',
  'https://www.mee.gov.cn/zcwj/gwywj/202207/t20220707_987881.shtml','technical_reference'),
 (WARNING_COMPARE,
  '关注海洋灾害，筑牢蓝色防线（泉州市海洋与渔业局科普）',
  'https://hyj.quanzhou.gov.cn/zwgk/zhxx/202505/t20250513_3168309.htm','general_safety'),
 (MARINE_RESEARCH,
  'HIDRA-D: deep-learning model for dense sea level forecasting using sparse altimetry and tide gauge data',
  'https://gmd.copernicus.org/articles/19/2177/2026/gmd-19-2177-2026.html','research_method'),
 (MARINE_RESEARCH,
  'Computing extreme storm surges in Europe using neural networks',
  'https://nhess.copernicus.org/articles/25/4593/2025/','research_method'),
 (r'质控|质量控制|尖峰|连续不变|观测.*异常',
  'QARTOD现场温盐实时质量控制手册（NOAA IOOS，2020版方法参考）',
  'https://cdn.ioos.noaa.gov/media/2020/03/QARTOD_TS_Manual_Update2_200324_final.pdf','technical_reference'),
 (r'台风眼|风眼|眼区|眼壁|中心过境|风突然停|风小了|物资|家庭.*准备|开车.*注意|预警.*解除|转移.*回家',
  '台风来了，这份安全攻略请收好（福建省政府）',
  'https://www.fj.gov.cn/jdhy/hygq/202407/t20240724_6488728.htm','general_safety'),
 (r'物资|水和粮|储备|囤水|囤粮|三口之家|转移.*带什么|'+HOUSEHOLD,
  '全国基础版家庭应急物资储备建议清单（应急管理部）',
  'https://www.mem.gov.cn/kp/shaq/202011/t20201129_372149.shtml','general_safety'),
 (r'12395|海上.*求救|遇险.*求救',
  '国家海上搜救应急预案（交通运输部公开附件）',
  'https://xxgk.mot.gov.cn/jigou/aqyzljlglj/202006/P020200623751659515041.pdf','general_safety'),
 (r'12395|海上.*求救|遇险.*求救',
  '拨打12395应注意什么事项（吴江区政府交通出行问答）',
  'https://www.wujiang.gov.cn/zgwj/rdwdjtcx/202512/dd006dd1ca944c4aa0763d33575b633f.shtml','general_safety'),
 (r'12395|海上.*求救|遇险.*求救',
  '水上交通突发事件应急预案：全国统一水上搜救电话（陕西省政府）',
  'https://www.shaanxi.gov.cn/zfxxgk/fdzdgknr/zcwj/nszfbgtwj/szbh/202208/t20220808_2237265.html','general_safety'),
 (r'离岸流|被卷出去', 'What is a rip current?（NOAA通用海滩安全知识）',
  'https://oceanservice.noaa.gov/facts/ripcurrent.html','general_safety'),
 (r'离岸流|被卷出去', '海边玩水警惕离岸流（国家消防救援局）',
  'https://www.119.gov.cn/kp/zjts/qtzh/2024/44711.shtml','general_safety'),
 (r'离岸流|被卷出去', '海边游玩安全知识（青岛市公安局公开资料）',
  'https://www.qingdao.gov.cn/zwgk/xxgk/gaj/xxgkml_551/gzxx/202506/t20250616_9679432.shtml','general_safety'),
 (r'离岸流|被卷出去', '夏季下水安全：国家水域救援专家提示（烟台市体育局）',
  'https://tyj.yantai.gov.cn/art/2025/8/19/art_7295_2884412.html','general_safety'),
 (r'热带风暴|强热带风暴|强台风|超强台风|热带气旋等级|强度分级|'+STANDARDS_CATALOG,
  'GB/T 32935—2025 热带气旋等级（中国气象局公开附件）',
  'https://www.cma.gov.cn/zfxxgk/gknr/flfgbz/bz/202512/P020251225481860646746.pdf','versioned_standard'),
 (r'消毒|清理|自来水|饮水|水泡过|泡水|水淹|被淹',
  '洪涝灾害家庭消毒杀虫技术指南（重庆市卫生健康委）',
  'https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202008/t20200820_7800061.html','general_safety'),
 (r'自来水|饮水|蔬菜|海鲜|食品|蚊子|传染病',
  '苏州疾控发布洪水过后健康提示',
  'https://www.suzhou.gov.cn/szsrmzf/mszx/202608/76553f59616b45219462812950f19324.shtml','general_safety'),
 (r'蚊子|蚊虫|传染病|病媒', '洪涝灾害后病媒生物防控（重庆市疾控中心）',
  'https://www.cq.gov.cn/zt/2026cqsaqdx/aqkp/202606/t20260624_15772512.html','general_safety'),
 (r'RAG|检索增强|知识库|幻觉|溯源|'+QA_EVIDENCE_RE, 'Corrective Retrieval Augmented Generation',
  'https://arxiv.org/abs/2401.15884','research_method'),
 (r'RAG|检索增强|知识库|多子问题|复杂问题|'+QA_EVIDENCE_RE, 'Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity',
  'https://aclanthology.org/2024.naacl-long.389/','research_method'),
 (r'幻觉|核验|溯源|专报生成|'+QA_EVIDENCE_RE, 'Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection',
  'https://arxiv.org/abs/2310.11511','research_method'),
 (r'智能体|大模型|核心能力|海洋.*技术.*进展', 'OceanGPT: A Large Language Model for Ocean Science Tasks',
  'https://aclanthology.org/2024.acl-long.184/','research_method'),
 (r'多源数据|数据.*接入|海洋.*智能体|自然语言.*数据',
  'FathomGPT: A Natural Language Interface for Interactively Exploring Ocean Science Data',
  'https://arxiv.org/abs/2412.02784','research_method'),
 (r'警戒潮位.*(?:核定|怎么定)|四色.*警戒|警戒潮位的.*四',
  'Warning water level determination and its spatial distribution in coastal areas of China',
  'https://nhess.copernicus.org/articles/23/127/2023/index.html','research_method'),
 (r'淹没.*(?:模拟|输入)|模拟.*(?:输入|验证)|情景矩阵|DEM.*精度|地形.*验证',
  'Simulation of storm surge inundation under different typhoon intensity scenarios: case study of Pingyang County, China',
  'https://nhess.copernicus.org/articles/20/2777/2020/','research_method'),
)

RESEARCH_FOCUS=(
 (MARINE_RESEARCH,('sea level','forecast','storm surge','deep learning','observation','validation','code availability','limitation')),
 (PROGRESS_RE,('sea level','forecast','storm surge','deep learning','observation','validation','code availability','data availability','limitation')),
 (QA_EVIDENCE_RE,('retrieval','citation','evaluator','generation','reflection','complexity')),
 (r'质控|质量控制|尖峰|连续不变|观测.*异常',('spike','flat line','gross range','rate of change','threshold','quality control','test 5','test 7')),
 (r'RAG|知识库|幻觉|溯源',('retrieval','citation','evaluator','generation','reflection','complexity')),
 (r'同化|误差|订正',('assimilation','ensemble','error','rmse','surge')),
 (r'淹没|DEM|情景|区划|技术流程',('inundation','dem','validation','seawall','model','mesh','data')),
 (r'脆弱性|损失率|承灾体',('vulnerability','depth','damage','exposure','function','validation')),
 (r'警戒潮位.*(?:核定|怎么定)',('warning water level','formula','return period','dike','calculation')),
)

def research_focus(question):
    return [word for pattern,words in RESEARCH_FOCUS if re.search(pattern,question,re.I) for word in words]


CONNECTOR_V2_HEALTH_ROUTES = (
 (r'蚊[子虫媒]|防蚊|灭蚊|登革热|基孔肯雅',
  '2026年5月健康风险提示（中国疾病预防控制中心）',
  'https://www.chinacdc.cn/jkts/202605/t20260509_1835669.html','national_guidance'),
 (r'出血热|鼠媒|鼠类|老鼠',
  '肾综合征出血热健康科普（中国疾病预防控制中心）',
  'https://www.chinacdc.cn/jkkp/crb/ycr/202502/t20250220_304455.html','national_guidance'),
 (r'乙脑|蚊[子虫媒]|防蚊',
  '乙型脑炎健康科普（中国疾病预防控制中心）',
  'https://www.chinacdc.cn/jkyj/mygh02/ymkyfjb/yn/202606/t20260622_1837243.html','national_guidance'),
 (r'钩体|鼠类|鼠媒|洪涝.{0,20}(?:防疫|防病)|灾后.{0,20}(?:防疫|疾病)',
  '自然灾害传染病防控技术指南（中国疾病预防控制中心）',
  'https://www.chinacdc.cn/jkyj/tfggws/jswj1/201408/t20140820_301450.html','technical_reference'),
)


def primary_routes(question,connector_v2=False):
    rows=[{'title':title,'url':url,'_primary_scope':scope}
          for pattern,title,url,scope in PRIMARY_ROUTES if re.search(pattern,question,re.I)]
    if connector_v2:
        health = [{'title':title,'url':url,'_primary_scope':scope}
                  for pattern,title,url,scope in CONNECTOR_V2_HEALTH_ROUTES if re.search(pattern,question,re.I)]
        rows = health + rows
    if re.search(WARNING_LEVELS,question) and not re.search(WARNING_TECHNICAL,question):
        rows=[row for row in rows if row['_primary_scope']!='research_method']
    if re.search(r'历史|曾经|哪一次|哪一年',question) and re.search(r'被淹|淹没|漫堤|倒灌|最严重',question) and not re.search(r'消毒|清理|疾病|水质',question):
        rows=[row for row in rows if row['_primary_scope']!='general_safety']
    # A catalogue of observed source URLs, not year-to-value answer mappings.
    if re.search(r'2025年?.*(?:海洋灾害.*损失|国民经济|统计公报)',question):
        rows[:0]=[{'title':'中华人民共和国2025年国民经济和社会发展统计公报（国家统计局）',
            'url':'https://www.stats.gov.cn/sj/zxfbhjd/202602/t20260228_1962662.html','_primary_scope':'annual_statistics'},
           {'title':'中华人民共和国2025年国民经济和社会发展统计公报（黑龙江省统计局转载国家统计局）',
            'url':'https://tjj.hlj.gov.cn/tjj/c106736/202603/c00_31918855.shtml','_primary_scope':'annual_statistics'}]
    if re.search(r'2025年?.*(?:海洋灾害|台风).*(?:损失|灾情|类型|主要)',question):
        rows.insert(0,{'title':'2025年全国自然灾害基本情况（应急管理部）','url':'https://www.mem.gov.cn/xw/yjglbgzdt/202601/t20260116_592135.shtml','_primary_scope':'annual_statistics'})
    from .public_service_intents import RELIEF, INSURANCE
    if re.search(RELIEF,question) and not re.search(INSURANCE,question):
        rows=[row for row in rows if not re.search(r'保险|赔付', row['title'])]
    return rows


def research_question(question):
    if re.search(WARNING_LEVELS,question) and not re.search(WARNING_TECHNICAL,question):return False
    return bool(RESEARCH_RE.search(question))
