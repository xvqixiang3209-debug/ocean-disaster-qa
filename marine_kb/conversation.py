"""Bounded, browser-owned conversations; previous answers are context, not evidence."""
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
import re
import secrets
import time


@dataclass
class Conversation:
    owner: str
    sid: str | None = None
    turns: list = field(default_factory=list)
    updated: float = field(default_factory=time.monotonic)
    lock: Lock = field(default_factory=Lock)
    deleted: bool = False


class ConversationStore:
    def __init__(self, limit=256, ttl=7200, max_turns=6):
        self.items = OrderedDict()
        self.guard = Lock()
        self.limit, self.ttl, self.max_turns = limit, ttl, max_turns

    def _purge(self):
        cutoff = time.monotonic() - self.ttl
        for key, value in list(self.items.items()):
            if value.updated < cutoff and not value.lock.locked():
                value.deleted = True
                del self.items[key]

    def get(self, owner, conversation_id=None):
        with self.guard:
            self._purge()
            if conversation_id:
                item = self.items.get(conversation_id)
                if item is None or not secrets.compare_digest(item.owner, owner):
                    raise KeyError('对话已失效，请新建对话后继续。')
            else:
                while len(self.items) >= self.limit:
                    idle = next((key for key, row in self.items.items() if not row.lock.locked()), None)
                    if idle is None:
                        raise RuntimeError('对话服务繁忙，请稍后重试。')
                    self.items[idle].deleted = True
                    del self.items[idle]
                conversation_id = secrets.token_urlsafe(24)
                item = Conversation(owner)
                self.items[conversation_id] = item
            item.updated = time.monotonic()
            self.items.move_to_end(conversation_id)
            return conversation_id, item

    def delete(self, owner, conversation_id):
        with self.guard:
            item = self.items.get(conversation_id)
            if item is None or not secrets.compare_digest(item.owner, owner):
                raise KeyError('对话已失效。')
            item.deleted = True
            item.turns.clear()
            del self.items[conversation_id]

    def append(self, item, question, result):
        if item.deleted:
            return
        meta = result.get('answer_meta') or {}
        item.turns.append({
            'question': question[:2000],
            'context_question': str(meta.get('normalized_question') or question)[:2000],
            'answer': str(result.get('answer') or '')[:3000],
            'sources': (result.get('rag_sources') or []) + (result.get('web_sources') or []),
            'answer_context_allowed': meta.get('used_mode') == 'llm',
        })
        del item.turns[:-self.max_turns]
        item.updated = time.monotonic()


def followup_question(question):
    """Limit context to referential questions; a new complete question stands alone."""
    return bool(re.search(
        r'它|他们|这些|那些|这个|那个|该站|该台风|上述|刚才|前面|上面|上一[轮条个]|'
        r'同一(?:清单|规则|口径|标准|方法|条件|地区|地点|时段|批次)|(?:相同|同样)的(?:清单|规则|口径|标准|方法)|'
        r'按(?:之前|原来|上次|前述)|如果(?:改成|换成|只剩)|'
        r'第[一二三四五六七八九十\d]+[点项条步]|继续|详细一点|展开|具体说|为什么会这样|'
        r'^(?:为什么|怎么做|怎么办|怎么算|哪个站|哪里|多少|由谁|什么时候)[呢？?]?$|'
        r'^.{1,20}呢[？?]?$|'
        r'^(?:那|那么)?(?:今天|明天|后天|当天|现在)的?(?:高潮|低潮|退潮|涨潮|浪高|风速|海温|流速).{0,35}[？?]?$|'
        r'^(?:哪个(?:站|岸段|时刻)?(?:最高|最大|最严重)?|最高的是哪个(?:站|岸段)?|最大的是多少|几点最高|什么时候最高|为什么这么高)[呢？?]?$', question))


def selected_case_applies(question, selected, aliases):
    if not selected:
        return False
    name = str(selected.get('name') or '').casefold()
    names = [cn for cn, en in aliases.items() if cn in question or en in question.casefold()]
    if names and not any(cn.casefold() in name or aliases[cn] in name for cn in names):
        return False
    years = re.findall(r'(?:19|20)\d{2}', question)
    if years and str(selected.get('season') or '') not in years:
        return False
    if re.search(r'今年|去年|明年|(?<!出)现在|当前|最新|这几天|未来|明天|后天|今天|明日|今日|预报', question):
        return False
    if re.search(r'什么是|是什么意思|有什么区别|怎么定义|如何划分', question):
        return False
    return bool(re.search(r'这次台风|这场台风|它|该台风|当时|登陆|强度|最大风速|气压|'
                          r'损失|灾情|受灾|防御|应对|最大增水|最高潮位|超警|各站|分站|'
                          r'路径|轨迹|相似度|排名', question)) or bool(names)


def structured_case_question(question):
    return bool(re.search(r'超警|分站|各站|经纬度|相似度|排名|轨迹', question)) and not bool(
        re.search(r'模式表|工作簿|数值表|什么是|是什么意思|有什么区别', question))
