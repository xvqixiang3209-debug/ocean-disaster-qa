"""Reproducible independent live questions; checkpoint original replies, not ideal answers."""
import json, hashlib, time, re, argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from urllib.request import Request, urlopen

APP=Path(__file__).resolve().parents[1]
OUT=APP/'tmp/qa_eval_20261017'
BASE='http://127.0.0.1:8767'
def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def get(path):
    with urlopen(BASE+path,timeout=45) as r:return json.load(r)
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((APP/'data/requirements/catalog.json').read_text(encoding='utf-8'))
    status=get('/api/knowledge/status'); forecast=get('/api/forecast/status'); storm=get('/api/typhoon/current')
    model=status['external_model']
    assert model['enabled'] and model['answer_generation_required']
    name=storm['name']; sid=storm['sid']; ty=f'2026年第{sid[-2:]}号台风“{name}”（编号{sid}）'
    date='2026年10月17日'; span='2026年10月17日21时至10月20日21时'
    q={
    'Q-001':'台风、风暴潮、海浪、海啸到底有什么区别？作为浙江沿海居民，我该关心哪一个？',
    'Q-006':'我住在宁波市鄞州区，海平面上升跟我有关系吗？它会让台风灾害更严重吗？',
    'Q-008':f'截至{date}，现在有几个台风在活动？最新的位置、强度和移动方向是什么？',
    'Q-009':f'截至{date}，{ty}预计什么时候、在哪里登陆？登陆时有多强？',
    'Q-010':f'{ty}的预报路径会不会变？还有几种可能？偏离的概率有多大？',
    'Q-011':f'我住在宁波市鄞州区，截至{date}，{ty}对我这里影响大吗？',
    'Q-012':f'{span}，舟山海域东经122.3度、北纬30.3度的风力和浪高有多大？',
    'Q-013':f'截至{date}，{ty}会不会突然加强？什么时候加强？',
    'Q-014':f'截至{date}，{ty}跟历史上哪一次台风比较像？那次损失多大？',
    'Q-015':f'截至{date}，浙江当前发布了哪些海洋灾害预警？覆盖哪些海域？',
    'Q-017':'假设舟山海域发布海浪红色警报，这是什么意思？舟山沿海居民现在必须做什么？',
    'Q-018':'浙江的海洋灾害预警什么时候发、多久更新一次？',
    'Q-019':'情景提问：假设舟山海浪预警从黄色升到橙色，通常是因为什么？',
    'Q-021':'作为宁波市鄞州区居民，我在哪里能第一时间收到官方预警？',
    'Q-022':f'{span}浙江沿海预报增水有多大，最高潮位会到多少？请区分网格预报与{ty}的因果归属。',
    'Q-023':f'{span}宁波市鄞州区会不会发生海水倒灌？哪些低洼地段会被淹？',
    'Q-024':'浙江省玉环市坎门街道这片岸段历史上被淹过吗？最严重是哪一次？',
    'Q-025':f'截至{date}，{ty}会对宁波舟山港、象山县养殖区和舟山朱家尖滨海景区造成多大影响？',
    'Q-026':f'截至{date}，{ty}可能在浙江造成多大经济损失？',
    'Q-027':f'截至{date}，{ty}对浙江影响最坏会到什么程度？有没有情景推演？',
    'Q-030':f'截至{date}，象山县渔船什么时候必须回港？该去哪个避风锚地？',
    'Q-031':f'我住在宁波市鄞州区咸祥镇海边低洼地段，截至{date}需要转移吗？转到哪里？',
    'Q-032':f'{date}还能去舟山朱家尖海边赶海、游泳、海钓吗？',
    'Q-033':'台风天从宁波驾车、乘舟山客船或从宁波机场坐飞机，要注意什么？',
    'Q-034':'浙江海上风电平台、施工平台的工人怎么撤离？',
    'Q-042':'2025年我国海洋灾害损失是多少？主要是什么灾种？',
    'Q-043':'按有全国统计依据的死亡人数和直接经济损失分别比较，历史上最严重的台风风暴潮是哪一次？',
    'Q-044':'浙江省历史上最严重的一次海洋灾害是什么情况？请说明比较指标和统计口径。',
    'Q-045':f'截至{date}，{ty}目前已经造成了哪些损失？',
    'Q-047':f'我在宁波市鄞州区咸祥镇，附近的应急避护场所在哪里？{date}开放了吗？',
    'Q-049':'假设台风期间宁波市鄞州区发生停水停电，找谁？多久能修好？',
    'Q-050':'浙江有哪些官方渠道可以查台风和海洋预警？',
    'Q-051':'当前这个台风海洋灾害问答系统能做什么？不能做什么？',
    'Q-052':f'截至{date}，当前这个系统的数据来自哪里？资料、台风实况和网格预报分别多久更新一次？',
    'Q-053':'当前这个系统给的预报准不准？有没有本系统独立检验的误差数据，误差多大？',
    'Q-054':'当前这个系统给的预报答案为什么可能和别的平台不一样？',
    'Q-055':f'请生成一份截至{date}的{ty}对浙江影响研判报告，区分实况、预报和资料缺项。',
    'Q-056':'浙江沿海台风过后什么时候可以出海、复航、复课、复工？',
    'Q-057':'假设象山县养殖塘和农田被海水浸泡，怎么处理？还能救回来吗？',
    'Q-058':'假设宁波市鄞州区家里被台风积水淹了，怎么清理消毒？自来水还能喝吗？',
    'Q-059':'假设宁波市鄞州区家庭因台风受灾，能申请什么救助？保险怎么赔？',
    'Q-060':'浙江灾后海堤、码头、养殖设施要怎么检查和修复？',
    'R-001':f'截至{date}，针对{ty}过程，宁波市应该启动几级海洋灾害应急响应？依据是什么？',
    'R-002':'情景提问：宁波市海浪橙色预警叠加风暴潮黄色预警，按本地预案对应什么响应级别？谁来签发？',
    'R-003':'宁波市海洋灾害响应启动后，自然资源（海洋）部门具体要干哪些事？',
    'R-004':'浙江省海洋灾害应急响应什么时候可以降级或终止？要走什么程序？',
    'R-005':f'给我一份{date}上午浙江台风海洋灾害会商要点：舟山海域东经122.3度北纬30.3度的风力、浪高，以及浙江岸段潮位、增水。',
    'R-006':f'{span}浙江沿海最高潮位会出现在哪个验潮站？超警戒潮位多少？',
    'R-007':f'跟历史台风2019年“利奇马”相比，{ty}截至{date}预报的浙江风暴潮影响是偏重还是偏轻？',
    'R-008':'浙江省海洋灾害警报发布的审批流程和签发权限是什么？要求多少分钟内发出？',
    'R-009':'宁波市怎么把海洋灾害预警信息真正传到村里、传到渔船上？“叫应”怎么落实？',
    'R-010':'给宁波市领导的海洋灾害预警专报怎么写？有没有模板和必填数据项？',
    'R-011':'浙江气象、应急、海洋三家说法不完全一致，对外口径怎么统一？',
    'R-012':'宁波市哪些岸段属于海洋灾害高风险区和隐患点？清单在哪查？',
    'R-013':'宁波市海水倒灌易淹没区有哪些？到哪一级警戒潮位开始进水？',
    'R-015':'宁波市海堤达标率是多少？哪些是未合拢或非标准海堤？重点巡查哪些？',
    'R-016':f'针对{ty}，截至{date}宁波市需要转移多少人？涉及哪些村和社区？',
    'R-017':f'浙江防台“六个百分百”落实情况怎么核查？截至{date}渔船回港的完成时限和统计口径是什么？',
    'R-018':'宁波市危破房、低洼简易房、户外施工人员转移到哪？责任主体是谁？',
    'R-019':'浙江省什么条件下启动“停课停工停产停运停业”？由谁决定、怎么公告？',
    'R-020':'浙江海洋灾情快报要报哪些指标？多长时间内必须上报？',
    'R-023':'浙江海洋部门报的灾情数字和应急、气象、农业农村不一致，以谁为准？',
    'R-025':'浙江海堤修复、渔港修复这类灾后重建项目怎么申报？',
    'R-026':'以2019年“利奇马”浙江防御工作为具体复盘案例，暴露了哪些短板？复盘报告怎么组织？',
    'R-028':f'截至{date}，浙江新一轮沿海警戒潮位核定的工作要求和时间进度是什么？',
    'R-029':'浙江海洋灾害应急预案多久修编一次？修编要重点改什么？',
    'R-031':'宁波市自然资源（海洋）部门跟应急、气象、水利、农业农村、海事、文旅的职责怎么划？',
    'R-033':f'{span}浙江岸段最大预报风暴增水是多少？出现在什么时间、哪个岸段？',
    'R-034':f'截至{date}，{ty}路径集合预报离散度如何？敏感区在哪里？',
    'R-035':f'截至{date}，{ty}会不会快速增强？判据怎么看？',
    'R-036':f'{span}浙江省玉环市坎门街道岸段的天文潮和风暴增水最不利叠加相位出现在什么时候？',
    'R-037':f'{span}舟山近岸点东经122.3度北纬30.3度与近海点东经123.5度北纬30.3度的有效波高、谱峰周期、波向分别是多少？',
    'R-039':'预报模式在浙江浅水、河口、复杂岸形处的误差特征是什么？怎么订正？',
    'R-040':f'截至{date}，浙江有哪些实时潮位站、浮标、雷达、卫星观测数据可用？有没有缺测？请区分观测和天文潮预报。',
    'R-045':'海洋灾害预警命中率、空报率、漏报率、TS评分怎么算？浙江2023至2025年业务评分分别多少？',
    'R-046':'浙江海洋灾害预警的实际平均提前量是多少？红色警报规定要求提前多久发布？',
    'R-057':f'截至{date}，现行海洋灾害应急预案和警戒潮位核定规范有哪些关键变化？',
    'R-059':f'截至{date}，海洋观测预报与防灾减灾领域现行标准有哪些？',
    'R-064':f'截至{date}，国内外海洋灾害预警技术有哪些最新进展？',
    'R-065':'情景提问：因台风从宁波出发的机票、高铁票、船票受到影响，分别能退改吗？',
    'R-066':f'截至{date}，舟山跨海大桥、轮渡和客运码头什么时候停运？什么时候恢复？',
    'R-067':'台风天在宁波市鄞州区开车要注意什么？车停哪里最安全？',
    'R-068':f'{date}宁波市鄞州区还能点外卖、收快递吗？会不会因台风停运？',
    'R-069':'情景提问：宁波台风天气公司是否还要上班？员工无法到岗算不算旷工？',
    'R-070':f'{date}去舟山朱家尖海边安全吗？若因台风关闭，多久恢复开放？',
    'R-071':'假设舟山已发布海浪警报，还能赶海、游泳、海钓、玩摩托艇吗？',
    'R-072':'情景提问：因台风不能入住舟山朱家尖海边民宿、酒店，订房能退吗？景区什么时候重新开？',
    'R-074':f'我住在宁波市鄞州区一楼并有地下车位，{span}会不会进水？要不要提前挪车？',
    'R-075':'宁波市鄞州区一个三口之家为台风应急要不要储备水和粮？储备多少合适？',
    'R-077':'假设宁波市鄞州区台风天停电停水，怎么办？应急电话多少？',
    'R-078':'我住在宁波市鄞州区铁皮屋顶老房子，台风前是不是必须搬走？',
    'R-079':f'截至{date}，象山县渔船什么时候必须回港？不回去会怎么样？',
    'R-080':'我是象山县养殖户，台风前怎么加固网箱和池塘？损失了能赔吗？',
    'R-081':f'截至{date}，舟山海上风电平台人员什么时候撤？家里怎么联系上他们？',
    'R-082':'宁波码头和工地在台风情况下什么时候必须停工？不停会被罚吗？',
    'R-083':'浙江沿海台风过后多久能出海？出海前要检查什么？',
    'R-084':'在浙江能从哪里查到最权威的台风和海洋预警？',
    'R-087':f'模拟待核验说法“{ty}在10月20日将以特大台风登陆宁波”，截至{date}有依据吗？怎样辨别？这只是测试情景，未提供真实网帖。',
    'R-088':'假设宁波市鄞州区咸祥镇已通知我转移，我要带什么？去哪里？能带宠物吗？',
    'R-089':'假设宁波市鄞州区咸祥镇家里老人行动不便，台风转移有困难怎么办？',
    'R-090':'假设已从宁波市鄞州区咸祥镇转移避险，之后什么时候能回家？',
    'R-091':'假设宁波市鄞州区家里因台风进水，怎么清理消毒？泡水的电器还能用吗？',
    'R-092':'假设台风后宁波市鄞州区家里被淹，自来水还能喝吗？被水泡过的蔬菜海鲜还能吃吗？',
    'R-093':'假设宁波市鄞州区灾后停水停电，一般多久能恢复？',
    'R-095':'假设台风把宁波市家庭的房子、车和象山县渔船冲坏了，保险分别赔吗？',
    'R-096':'假设象山县养殖、种植受灾，理赔要准备什么材料？找谁？',
    'R-097':'假设宁波市鄞州区家里因台风受灾严重，能申请什么政府救助？',
    'R-098':'假设台风影响宁波市鄞州区交通，需要生孩子、透析或拿药怎么办？',
    'R-099':'假设我是被台风滞留在舟山朱家尖的游客，吃住谁负责？怎么办？',
    'R-100':'假设家里老人小孩独自在宁波市鄞州区咸祥镇沿海村里，台风时谁来照看？',
    }
    items=[]
    for item in catalog['items']:
        original=item['question']; actual=q.get(item['id'],original)
        reason='原题明确，保持不变。' if actual==original else '按原意补齐地区、对象或时间；“假设”“情景”内容仅为测试设定。'
        if item['id']=='R-026':reason='“这次”替换为已发生的2019年利奇马浙江防御过程，用于复盘类问题；不声称当前台风已造成灾害。'
        items.append({**item,'original_question':original,'question':actual,'rewrite_reason':reason})
    assert len(items)==160 and len({x['id'] for x in items})==160
    manifest={'created_at':now(),'scenario_date':date,'forecast_interval':span,'storm':{k:storm.get(k) for k in ['sid','name','issued_at','source','data_status']},
              'note':'每题独立问答，业务预报不得自动归因于选定台风；未来网格批次和归档材料均按实际时间标记。',
              'source_name':catalog['source_name'],'source_sha256':catalog['source_sha256'],'items':items}
    save(OUT/'manifest.json',manifest)
    save(OUT/'snapshot_start.json',{'at':now(),'external_model':model,'forecast':forecast,'typhoon':storm,
           'system_version':(APP/'VERSION.txt').read_text(encoding='utf-8').strip()})
    return manifest,status

def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=OUT)
    parser.add_argument('--manifest-source',type=Path)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();OUT=args.output_dir;OUT.mkdir(parents=True,exist_ok=True)
    if args.manifest_source and not (OUT/'manifest.json').exists():
        save(OUT/'manifest.json',json.loads(args.manifest_source.read_text(encoding='utf-8')))
        save(OUT/'snapshot_start.json',{'at':now(),'external_model':get('/api/knowledge/status')['external_model'],
             'forecast':get('/api/forecast/status'),'typhoon':get('/api/typhoon/current'),
             'system_version':(APP/'VERSION.txt').read_text(encoding='utf-8').strip()})
    if (OUT/'manifest.json').exists():manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'));status=get('/api/knowledge/status')
    else:manifest,status=prepare()
    token=status['csrf_token']; model=status['external_model']['model']
    fingerprint=hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    def evaluate(item):
        path=OUT/(item['id']+'.json')
        if path.exists():
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved['manifest_sha256']==fingerprint:return saved
            raise RuntimeError('Checkpoint scenario mismatch')
        start=time.monotonic();attempts=[];result=None
        for attempt in range(2):
            try:
                req=Request(BASE+'/api/knowledge/ask',data=json.dumps({'question':item['question'],'allow_web':True},ensure_ascii=False).encode(),
                     headers={'Content-Type':'application/json','X-Knowledge-Token':token,'Origin':BASE},method='POST')
                with urlopen(req,timeout=240) as r:result=json.load(r)
                record={'attempt':attempt+1,'at':now(),'result':result}
                eid=result.get('answer_meta',{}).get('business_evidence_id')
                if not eid:
                    ids=re.findall(r'/api/forecast/evidence/([0-9a-f]{24})',json.dumps(result,ensure_ascii=False));eid=ids[0] if ids else None
                if eid:
                    try:record['business_evidence']=get('/api/forecast/evidence/'+eid)
                    except Exception as exc:record['evidence_capture_error']=type(exc).__name__
                attempts.append(record)
                # Preserve grounding failures; retry only transport or provider unavailability.
                if result.get('answer_meta',{}).get('route')!='model_unavailable':break
            except Exception as exc:attempts.append({'attempt':attempt+1,'at':now(),'transport_error':type(exc).__name__,'detail':str(exc)[:200]})
        if result is None:result={'answer':'本次未能从系统取得回答。','rag_sources':[],'web_sources':[],'answer_meta':{'used_mode':'model_error','route':'evaluation_transport_error'}}
        row={**item,'manifest_sha256':fingerprint,'model':model,'allow_web':True,'collected_at':now(),'elapsed_seconds':round(time.monotonic()-start,2),'attempts':attempts,'result':result}
        save(path,row);return row
    completed={};started=now()
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        futures=[pool.submit(evaluate,x) for x in manifest['items']]
        for f in as_completed(futures):
            row=f.result();completed[row['id']]=row
            meta=row['result'].get('answer_meta',{})
            print(json.dumps({'completed':len(completed),'id':row['id'],'mode':meta.get('used_mode'),'coverage':meta.get('answerability'),'seconds':row['elapsed_seconds']},ensure_ascii=False),flush=True)
    rows=[completed[x['id']] for x in manifest['items']]
    report={'title':'160题系统实测与缺项记录','started_at':started,'completed_at':now(),'model':model,'manifest_sha256':fingerprint,
            'question_count':160,'expert_accepted':False,'items':rows,
            'mode_counts':dict(Counter(x['result'].get('answer_meta',{}).get('used_mode','unknown') for x in rows)),
            'answerability_counts':dict(Counter(x['result'].get('answer_meta',{}).get('answerability','unspecified') for x in rows))}
    save(OUT/'answers.json',report)
    save(OUT/'snapshot_end.json',{'at':now(),'forecast':get('/api/forecast/status')})
    print(json.dumps({k:v for k,v in report.items() if k!='items'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
