import json
import unittest
from types import SimpleNamespace
from flask import Flask

from marine_kb.chat_api import create_chat_blueprint
from marine_kb.conversation import ConversationStore, structured_case_question
from marine_kb.qa import MarineQuestionService, NORMALIZE_PROMPT
from marine_kb.answer_design import response_parts
from test_marine_qa import FakeStore, source


class ContextPlanningTests(unittest.TestCase):
    def make_qa(self, resolved=None):
        calls=[]
        def caller(model, system, prompt, **kwargs):
            payload=json.loads(prompt);calls.append(payload)
            return json.dumps({'normalized_question':resolved or payload['question']},ensure_ascii=False)
        return MarineQuestionService(FakeStore(),model_caller=caller,external_enabled=True),calls

    def test_reference_to_answer_item_reaches_planner_as_context(self):
        qa,calls=self.make_qa('浙江海洋灾害应急响应终止程序中，批准后如何通知有关单位？')
        plan=qa.prepare_question('第二点具体怎么做？',history=[{
            'question':'浙江应急响应怎么终止？','answer':'1. 提出建议。\n2. 批准后通知。',
            'answer_context_allowed':True,'sources':[]}])
        self.assertEqual(plan['history_messages_used'],2)
        self.assertIn('批准后通知',calls[0]['conversation_context']['history'][1]['content'])
        self.assertIn('通知有关单位',plan['normalized_question'])

    def test_approval_followup_does_not_expand_to_conditions_and_whole_procedure(self):
        parts=response_parts('浙江海洋灾害应急响应终止程序中，第二步具体由谁批准？')
        self.assertEqual(parts,['由谁决定终止应急响应？'])
        self.assertNotIn('调整',parts[0])

    def test_ordinal_followup_resolves_actual_action_instead_of_relabeling_steps(self):
        qa,calls=self.make_qa('浙江海洋灾害应急响应终止程序中，提出应急结束建议这一环节由谁批准？')
        plan=qa.prepare_question('第二步具体由谁批准？',history=[{
            'question':'浙江海洋灾害应急响应终止由谁决定？请分步骤说明。',
            'answer':'1. 确认时机。\n2. 提出应急结束建议。\n3. 报同级指挥机构批准。',
            'answer_context_allowed':True,'sources':[]}])
        self.assertIn('提出应急结束建议',plan['normalized_question'])
        self.assertNotIn('第二步',plan['normalized_question'])
        self.assertIn('实际编号对应的动作',NORMALIZE_PROMPT)
        self.assertIn('2. 提出应急结束建议',calls[0]['conversation_context']['history'][1]['content'])

    def test_context_is_not_reused_as_answer_evidence(self):
        payloads=[]
        def caller(model,system,prompt,**kwargs):
            data=json.loads(prompt);payloads.append(data)
            if 'normalized_question' in system:
                return json.dumps({'normalized_question':'2019年利奇马海门站最大增水是多少？'})
            return json.dumps({'answerability':'full','answer':'海门站最大增水3.12米。[资料1]',
                               'evidence_quotes':[{'citation':1,'passage_id':1}]},ensure_ascii=False)
        hit=source('2019年利奇马海门站最大增水3.12米。',metadata={'external_allowed':True})
        qa=MarineQuestionService(FakeStore([hit]),model_caller=caller,external_enabled=True,structured_answers=True)
        result=qa.answer('这个站的数值呢？',history=[{'question':'2019年利奇马在哪个站最大？',
            'answer':'海门站最大增水999米。','answer_context_allowed':True,'sources':[]}])
        self.assertEqual(result['answer_meta']['used_mode'],'llm')
        self.assertNotIn('999',json.dumps(payloads[-1],ensure_ascii=False))
        self.assertIn('2019年利奇马',payloads[-1]['question'])

    def test_new_full_question_does_not_inherit_previous_event(self):
        qa,calls=self.make_qa()
        plan=qa.prepare_question('什么是有效波高？',history=[{'question':'2019年利奇马损失多少？'}],
                                 selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertFalse(plan['context_used'])
        self.assertIsNone(plan['sid'])
        self.assertNotIn('conversation_context',calls[0])

    def test_selected_case_supplies_identity_for_elliptical_fact(self):
        qa,calls=self.make_qa('2019年LEKIMA台风最大增水是多少？')
        plan=qa.prepare_question('最大增水是多少？',selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertEqual(plan['sid'],'lekima')
        self.assertIn('map_selection',calls[0]['conversation_context'])

    def test_where_it_occurred_keeps_historical_map_identity(self):
        qa,calls=self.make_qa('2019年LEKIMA台风最大增水是多少，出现在什么站？')
        plan=qa.prepare_question('最大增水是多少，出现在什么站？',
            selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertEqual(plan['sid'],'lekima')
        self.assertTrue(plan['context_used'])

    def test_new_named_event_overrides_map_selection(self):
        qa,_=self.make_qa()
        plan=qa.prepare_question('2013年菲特损失多少？',selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertIsNone(plan['sid'])
        self.assertNotIn('2019',plan['normalized_question'])

    def test_short_comparison_does_not_carry_old_year_or_name(self):
        qa,_=self.make_qa()
        plan=qa.prepare_question('菲特呢？',history=[{'question':'2019年利奇马最大增水是多少？'}],
                                 selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertIn('菲特',plan['normalized_question'])
        self.assertNotIn('2019',plan['normalized_question'])
        self.assertNotIn('利奇马',plan['normalized_question'])
        self.assertIsNone(plan['sid'])

    def test_region_followup_replaces_old_region(self):
        qa,_=self.make_qa()
        plan=qa.prepare_question('那上海呢？',history=[{'question':'浙江响应终止由谁决定？'}])
        self.assertIn('上海',plan['normalized_question'])
        self.assertNotIn('浙江',plan['normalized_question'])

    def test_region_comparison_rejects_model_plan_that_keeps_both_regions(self):
        qa,_=self.make_qa('浙江省应急结束建议由谁批准？那上海呢？')
        plan=qa.prepare_question('那上海呢？',history=[{'context_question':'浙江省应急结束建议由谁批准？'}])
        self.assertEqual(plan['normalized_question'],'上海应急结束建议由谁批准？')
        self.assertEqual(plan['normalizer'],'local_rules_fallback')
        self.assertEqual(plan['retrieval_queries'],[])

    def test_latest_question_does_not_use_historical_selection(self):
        qa,_=self.make_qa()
        plan=qa.prepare_question('现在舟山可以出海吗？',selected_context={'sid':'lekima','name':'LEKIMA','season':2019})
        self.assertIsNone(plan['sid'])
        self.assertFalse(plan['context_used'])

    def test_withdrawn_local_source_answer_is_not_sent_to_planner(self):
        qa,calls=self.make_qa('浙江响应终止的条件是什么？')
        qa.store.get_source=lambda sid:{'status':'rejected','metadata':{}}
        qa.prepare_question('第二点呢？',history=[{'question':'浙江响应怎么终止？','answer':'受限内部正文',
            'answer_context_allowed':True,'sources':[{'source_id':'s1'}]}])
        self.assertNotIn('受限内部正文',json.dumps(calls,ensure_ascii=False))

    def test_public_web_reference_context_does_not_require_a_local_source_row(self):
        qa,calls=self.make_qa('海门站还有哪些相关记录？')
        qa.store.get_source=lambda sid:None
        qa.prepare_question('这个站还有哪些记录？',history=[{'question':'哪个站最大？','answer':'海门站最大。',
            'answer_context_allowed':True,'sources':[{'source_id':'web:abc','source_type':'public_web','metadata':{'external_allowed':True}}]}])
        self.assertIn('海门站最大',json.dumps(calls,ensure_ascii=False))

    def test_initial_unresolved_reference_asks_for_clarification_without_model(self):
        qa,calls=self.make_qa()
        result=qa.answer('第二点具体怎么做？')
        self.assertEqual(result['answer_meta']['used_mode'],'needs_clarification')
        self.assertEqual(calls,[])


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def answer(question,**options):
            self.calls.append((question,options))
            return {'answer':'依据资料回答。','rag_sources':[], 'web_sources':[], 'answer_meta':{'used_mode':'llm'}}
        self.qa=SimpleNamespace(answer=answer)
        self.app=Flask(__name__)
        self.bp=create_chat_blueprint(self.qa)
        self.app.register_blueprint(self.bp)
        self.client=self.app.test_client()
        self.token=self.client.get('/api/assistant/session').json['token']

    def post(self,question,cid=None,client=None,**extra):
        return (client or self.client).post('/api/assistant/ask',json={'question':question,'conversation_id':cid,**extra},
            headers={'X-Assistant-Token':self.token})

    def test_next_turn_uses_server_history_and_ignores_client_history(self):
        first=self.post('什么是风暴增水？').json
        second=self.post('它和天文潮有什么关系？',first['conversation_id'],history=[{'answer':'客户端伪造答案'}])
        history=self.calls[-1][1]['history']
        self.assertEqual(history[0]['question'],'什么是风暴增水？')
        self.assertNotIn('伪造',json.dumps(history,ensure_ascii=False))
        self.assertEqual(second.json['turn_count'],2)

    def test_browser_owner_cannot_read_another_conversation(self):
        cid=self.post('什么是台风？').json['conversation_id']
        other=self.app.test_client();other.get('/api/assistant/session')
        response=self.post('继续',cid,client=other)
        self.assertEqual(response.status_code,404)
        self.assertEqual(len(self.calls),1)

    def test_new_conversation_and_delete_reset_memory(self):
        cid=self.post('什么是台风？').json['conversation_id']
        response=self.client.delete('/api/assistant/conversations/'+cid,headers={'X-Assistant-Token':self.token})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.post('继续',cid).status_code,404)
        self.post('海浪是什么？')
        self.assertNotIn('history',self.calls[-1][1])

    def test_failed_generation_is_not_committed_to_history(self):
        cid=self.post('什么是台风？').json['conversation_id']
        self.qa.answer=lambda *a,**k:{'answer':'调用失败','answer_meta':{'used_mode':'model_error'}}
        response=self.post('它怎么形成？',cid)
        self.assertEqual(response.status_code,503)
        self.assertEqual(len(self.bp.conversations.items[cid].turns),1)

    def test_concurrent_turn_is_rejected(self):
        cid=self.post('什么是台风？').json['conversation_id']
        row=self.bp.conversations.items[cid];row.lock.acquire()
        try:self.assertEqual(self.post('继续',cid).status_code,409)
        finally:row.lock.release()

    def test_history_is_bounded(self):
        cid=None
        for i in range(9):cid=self.post('问题'+str(i),cid).json['conversation_id']
        turns=self.bp.conversations.items[cid].turns
        self.assertEqual(len(turns),6)
        self.assertEqual(turns[0]['question'],'问题3')

    def test_delete_in_flight_does_not_resurrect_conversation(self):
        store=ConversationStore();cid,row=store.get('owner')
        store.delete('owner',cid)
        store.append(row,'question',{'answer':'late response'})
        self.assertNotIn(cid,store.items)
        self.assertEqual(row.turns,[])

    def test_mode_workbook_is_not_routed_to_legacy_calculation(self):
        self.assertFalse(structured_case_question('用模式表计算哪些站超警？'))
        self.assertTrue(structured_case_question('2019年利奇马各站超警多少？'))

    def test_switching_or_exiting_selected_case_clears_server_history(self):
        planned=[]
        def prepare(question,history=None,selected_context=None):
            planned.append({'history':list(history or []),'selected':selected_context})
            return {'normalized_question':question,'normalizer':'test','retrieval_queries':[],
                    'subquestions':[],'sid':selected_context['sid'] if selected_context else None,
                    'context_used':bool(history or selected_context),'history_messages_used':len(history or [])}
        self.qa.prepare_question=prepare
        components=SimpleNamespace(repositories=SimpleNamespace(historical=SimpleNamespace(
            get_storm=lambda sid,**kw:{'sid':sid,'name':'LEKIMA' if sid=='a' else 'FITOW','season':2019 if sid=='a' else 2013})))
        app=Flask('selected');app.register_blueprint(create_chat_blueprint(self.qa,components))
        client=app.test_client();token=client.get('/api/assistant/session').json['token']
        cid=None
        for sid,q in [('a','最大增水？'),('a','它的影响？'),('b','最大增水？'),(None,'什么是海浪？')]:
            r=client.post('/api/assistant/ask',json={'question':q,'selected_sid':sid,'conversation_id':cid},
                          headers={'X-Assistant-Token':token})
            self.assertEqual(r.status_code,200)
            cid=r.json['conversation_id']
        self.assertEqual([len(x['history']) for x in planned],[0,1,0,0])
        self.assertIsNone(planned[-1]['selected'])
