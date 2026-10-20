"""Bounded, browser-owned conversations; previous answers are context, not evidence."""
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
import re
import secrets
import time
import hashlib
import json


@dataclass
class Conversation:
    owner: str
    sid: str | None = None
    turns: list = field(default_factory=list)
    updated: float = field(default_factory=time.monotonic)
    lock: Lock = field(default_factory=Lock)
    deleted: bool = False
    turn_state: dict | None = None


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
            item.turn_state = None
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
        from .facet_contract import facet_contract_enabled
        if facet_contract_enabled():
            state = meta.get('turn_state')
            if state is None:
                state = resolve_turn(question, item.turn_state if len(item.turns) > 1 else None).as_dict()
            item.turn_state = TurnState.from_dict(state).as_dict()
            item.turns[-1]['turn_state'] = item.turn_state
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


@dataclass(frozen=True)
class TurnState:
    """Resolved input entities only: previous answers are never evidence.

    Additive helper for the typed-contract path.  Legacy ConversationStore
    behavior is intentionally unchanged until its caller explicitly uses it.
    """
    storm_sid: str | None = None
    places: tuple = ()
    time: dict | None = None
    objects: tuple = ()
    as_of: str | None = None
    inherited_fields: tuple = ()

    def as_dict(self):
        return {'storm_sid': self.storm_sid, 'places': list(self.places),
                'time': dict(self.time) if self.time else None,
                'objects': list(self.objects), 'as_of': self.as_of,
                'inherited_fields': list(self.inherited_fields)}

    def fingerprint(self):
        value = self.as_dict()
        value.pop('inherited_fields')
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value
        value = value or {}
        return cls(storm_sid=value.get('storm_sid'), places=tuple(value.get('places') or ()),
                   time=value.get('time'), objects=tuple(value.get('objects') or ()),
                   as_of=value.get('as_of'), inherited_fields=tuple(value.get('inherited_fields') or ()))


def resolve_turn(question, prev=None, *, now=None, selected_sid=None):
    """Explicit replacements win; references inherit; a new topic resets.

    Correcting a storm clears an old forecast window unless a replacement
    window was also explicitly supplied. A date-only correction uses the
    prior explicit year, not an unrelated machine-clock year.
    """
    from .evidence_applicability import leaf_places, question_context, parse_date
    from .facet_contract import object_names, resolve_time_scope
    from .retrieval import STORM_ALIASES
    previous = TurnState.from_dict(prev)
    correction = bool(re.search(r'不是.{0,50}[，,是]|改成|换成|更正|纠正|应为|说错', question))
    text = question
    corrected = re.search(r'不是.{0,50}?[，,]\s*(?:而)?是(.+)', question)
    if corrected:
        text = corrected[1]
    followup = bool(prev) and (correction or followup_question(question))
    region = tuple(leaf_places(text))
    names = [en for cn, en in STORM_ALIASES.items() if cn in text or en.casefold() in text.casefold()]
    code = re.search(r'(?<!\d)(20\d{4})(?!\d)', text)
    storm = code[1] if code else selected_sid or (names[0] if len(names) == 1 else None)
    explicit_time = bool(re.search(r'(?:20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}|\d{1,2}月\d{1,2}日|今天|今日|明天|昨天|后天|当前|现在|实时|截至|截止)', text))
    scope = None
    as_of = None
    if explicit_time:
        scope = resolve_time_scope(text, previous.time if followup else None, now)
        as_of = scope.get('as_of')
    objects = tuple(object_names(text))
    inherited = []
    if followup:
        if not region and previous.places:
            region = previous.places
            inherited.append('places')
        if not storm and previous.storm_sid:
            storm = previous.storm_sid
            inherited.append('storm_sid')
        storm_changed = bool(storm and previous.storm_sid and storm != previous.storm_sid and
                             'storm_sid' not in inherited)
        if not explicit_time and previous.time and not storm_changed:
            scope = previous.time
            as_of = previous.as_of
            inherited.append('time')
        if not objects and previous.objects:
            objects = previous.objects
            inherited.append('objects')
    return TurnState(storm_sid=storm, places=region, time=scope, objects=objects,
                     as_of=as_of, inherited_fields=tuple(inherited))


def turn_state_from_history(history):
    """Read only recorded input state, never the previous assistant answer."""
    state = None
    for turn in (history or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        if isinstance(turn.get('turn_state'), dict):
            state = TurnState.from_dict(turn['turn_state'])
        else:
            question = turn.get('context_question') or turn.get('question')
            if question:
                state = resolve_turn(str(question), state)
    return state


def state_reference_question(question):
    return followup_question(question) or bool(re.search(r'不是.{0,50}[，,是]|改成|换成|更正|纠正|应为|说错', question))


def resolve_state_question(question, previous_question, previous_state, state):
    """Replace old entities in an elliptical follow-up before normalizing it.

    This is an input fallback, not generated answer text. Non-entity follow-ups
    retain the old planner behavior so ordinal answers are still resolved by
    their actual context rather than a guessed numbered action.
    """
    if not previous_state or not state_reference_question(question):
        return None
    previous = TurnState.from_dict(previous_state)
    state = TurnState.from_dict(state)
    changed_place = bool(state.places and state.places != previous.places)
    changed_storm = bool(state.storm_sid and state.storm_sid != previous.storm_sid)
    changed_time = bool(state.time and state.time != previous.time and 'time' not in state.inherited_fields)
    changed_objects = bool(state.objects and state.objects != previous.objects and 'objects' not in state.inherited_fields)
    if not any((changed_place, changed_storm, changed_time, changed_objects)):
        return None
    from .evidence_applicability import places
    from .retrieval import STORM_ALIASES
    text = previous_question
    prefixes = []
    if changed_place:
        for name in sorted(places(previous_question), key=len, reverse=True):
            text = re.sub(re.escape(name) + r'(?:省|市|县|区)?', '', text)
        prefixes.append('、'.join(state.places))
    if changed_storm:
        for cn, en in STORM_ALIASES.items():
            text = re.sub(re.escape(cn) + '|' + re.escape(en), '', text, flags=re.I)
        text = re.sub(r'(?<!\d)20\d{4}(?!\d)|(?:19|20)\d{2}年?', '', text)
        prefixes.append('台风' + str(state.storm_sid))
    if changed_time or changed_storm and not state.time:
        text = re.sub(r'(?:截至|截止)?\s*(?:20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?|\d{1,2}月\d{1,2}日)(?:\s*\d{1,2}(?:[:：时]\d{0,2})?分?)?', '', text)
        text = re.sub(r'今天|今日|明天|昨天|后天', '', text)
        if state.time and state.time.get('start'):
            start = state.time['start']
            end = state.time.get('end')
            prefixes.append(start + ('至' + end if end and end != start else ''))
    if changed_objects:
        for name in previous.objects:
            text = text.replace(name, '')
        prefixes.extend(state.objects)
    text = re.sub(r'^[；;，,\s至到—–-]+|[；;，,\s至到—–-]+$', '', text)
    return (' '.join(prefixes) + ' ' + text).strip()[:2000]
