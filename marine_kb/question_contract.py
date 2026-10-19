"""Describe the requested scope without inventing observations or requirements."""
import re


def question_contract(question):
    scenario=bool(re.search(r'假设|情景提问|情景设定|模拟待核验',question))
    dated=bool(re.search(r'截至|20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}|今天|明天|当前|现在|目前|此刻|实时',question))
    method=bool(re.search(r'怎么|怎样|如何|为什么|什么是|是什么|是不是|定义|区别|原理|流程|方法|步骤|依据什么|申请什么|哪些.*模块|哪些.*渠道|检查哪些|应按|应核查|可以|能否|要不要',question))
    measured=bool(re.search(r'分别多少|是多少|平均提前量|完成.*情况|落实情况|有没有缺测|目前已经造成|在哪.*开放|开放了吗|哪些地段.*淹|转移多少',question))
    mode='scenario' if scenario else 'dated_facts' if dated else 'method_or_knowledge' if method else 'unspecified'
    return {'mode':mode,'explicit_date_requested':dated,'specific_measurement_requested':measured,
            'requested_outputs':{
                'time_series':bool(re.search(r'逐时|每小时|时间序列|过程线|过程曲线|变化过程|完整序列',question)),
                'quantitative_local_impact':bool(re.search(r'淹没(?:深度|范围|面积)|进水|转移多少|损失多少|概率|定量|多少人',question)),
                'current_release_or_opening':bool(dated and re.search(r'发布了|已发布|开放了吗|是否开放|是否停航|几点恢复|何时恢复',question)),
                'formal_template':bool(re.search(r'正式模板|固定模板|官方模板|法定必填',question))},
            'scope_instruction':(
                '按用户给出的假设解释；不增加“必须先证明灾情已发生”的缺项。地区只限定适用规则，不能把方法问擅自改成查询当天执行记录。'
                if scenario else
                '回答概念、原理、准备步骤或方法。除非用户明确要求当前状态、完整名单或地方实测，不增加当前实况、全部地区统一规定、现场名单等缺项。'
                if mode=='method_or_knowledge' else
                '分别交付实际可得数据及适用范围。日期限定、观测与预报、开放与营业等差异保持明确；没有取得的值不补造。'),
            'calculation_instruction':'来源有公式或阈值时可解释公式、参数与已载明的示例；只有实际输入齐备并经过程序计算才报告本地计算结果。研究论文的实验指标不能说成浙江业务评分。'}
