"""Conservative, audited removal of reviewer-identified *unasked* gaps.

Never upgrade answerability here. A removed disclaimer is not a delivered
answer, and reviewer suggestions can never erase a protected business gap.
"""
from __future__ import annotations

import re

HARD_GAPS = {
    'eligibility': frozenset({'individual_determination_required', 'jurisdiction_rule_not_found'}),
    'status_at_time': frozenset({'live_status_not_available'}),
    'numeric_value': frozenset({'business_data_missing', 'calculation_input_missing'}),
    'calculation': frozenset({'business_data_missing', 'calculation_input_missing'}),
    'historical_fact': frozenset({'business_data_missing'}),
}


def classify_gap(text, facet=None, evidence=None):
    """A gap type describes the missing input, not the presence of a city name."""
    value = str(text)
    kind = (facet or {}).get('kind')
    if re.search(r'个人|本人|保单|保险合同|承保范围|投保|参保|免赔|定损|个人资格|家庭.{0,8}(?:收入|财产)|资格核定|个体|是否符合.{0,8}(?:条件|资格)', value):
        return 'individual_determination_required'
    if re.search(r'计算.{0,8}(?:输入|参数)|原始输入|高程基准|单位.{0,6}(?:定义|核定|确认)|SWL|SSW|公式参数|站点映射|阈值.{0,8}(?:未|缺)', value, re.I):
        return 'calculation_input_missing'
    # An outbreak is a dated observation, not a local legislative edition.
    # These nominal phrases are gaps already; they need not repeat "缺少".
    if re.search(r'疫情|病例|暴发|开放状态|发布状态|当前运行状态|当前是否正常运行|实时预警内容|入口有效性|有效时段|后续通知', value):
        return ('business_data_missing' if kind in {'numeric_value', 'historical_fact', 'calculation'}
                else 'live_status_not_available')
    if re.search(r'实测|数值|金额|赔了多少|统计|人数|名单|损失量|增水|潮位|浪高|风速|逐时|数据|数量|赔款总额', value):
        # Monetary eligibility rules belong to jurisdiction; the actual paid
        # amount is a business datum and cannot be removed by a critic.
        if re.search(r'(?:当地|地方|本地|区县).{0,12}(?:补助|赔偿|救助)?(?:金额|标准)', value) and kind == 'eligibility':
            return 'jurisdiction_rule_not_found'
        return 'business_data_missing'
    if re.search(r'当地|地方|本地|所问地点|未明确覆盖|专用|现行要求|现行规定|申请窗口|补助标准|办理窗口|辖区|适用地区', value):
        return 'jurisdiction_rule_not_found'
    if re.search(r'当日|当天|当前|实时|现时|最新|开放状态|发布状态|是否开放|解除公告|停复航|疫情实况|暴发|有效时段|后续通知', value):
        return 'live_status_not_available'
    if re.search(r'缺|未|尚|没有|不足|不详|待核|网址|链接|查询入口|页面布局|查询步骤|办理程序|办理依据|条款|补贴|保费', value):
        if kind in {'numeric_value', 'historical_fact'}:
            return 'business_data_missing'
        if kind == 'status_at_time':
            return 'live_status_not_available'
        return 'evidence_not_found'
    return 'unclassified'


def _gap_family(text):
    """Recognize independent missing inputs, never arbitrary list elements."""
    patterns = (
        ('eligibility', r'承保范围|投保条件|个人资格|保单|保险合同|是否投保|参保条件|免赔|定损'),
        ('optional_benefit', r'保费补贴|保费补助|财政补贴|保险补贴'),
        ('procedure', r'理赔流程|理赔程序|申请流程|办理流程|报案渠道'),
        ('local_rule', r'地方.{0,8}(?:规范|规程|标准)|本地.{0,8}(?:规范|规程|标准)|当地.{0,8}(?:规范|规程|标准)'),
    )
    return next((name for name, expression in patterns if re.search(expression, text)), None)


def _gap_units(text):
    """Split mixed requirements only at explicit, independently named inputs.

    Never split a conjunction of factual/conditional clauses, quoted text,
    scope-qualified business values or lists of inspection objects.  The
    original string is retained in every audit decision.
    """
    if re.search(r'[《》“”"「」]|如果|只有|除非|否则|但|若', text):
        return [text]
    if ';' in text or '；' in text or '\n' in text:
        parts = [part.strip() for part in re.split(r'[；;\n]', text) if part.strip()]
        if len(parts) > 1 and all(_gap_family(part) for part in parts):
            return parts
    # A benefits list is safe to separate only when *every* child names a
    # known requirement and an optional benefit is independently present.
    parts, current, depth, position = [], [], 0, 0
    while position < len(text):
        char = text[position]
        if char in '（(【[':
            depth += 1
        elif char in '）)】]':
            depth = max(0, depth - 1)
        marker = next((mark for mark in ('以及', '、', '，', ',', '及', '和')
                       if depth == 0 and text.startswith(mark, position)), None)
        if marker:
            if current:
                parts.append(''.join(current).strip())
            current = []
            position += len(marker)
        else:
            current.append(char)
            position += 1
    if current:
        parts.append(''.join(current).strip())
    families = [_gap_family(part) for part in parts]
    if len(parts) > 1 and all(families) and 'optional_benefit' in families:
        return parts
    return [text]


def _unasked_reason(text, facets):
    """Only high-confidence extras can be demoted without a model critic.

    All plausible owners must agree.  This is not a general ban on local or
    current information: a sibling asking for that information protects it.
    """
    if not facets:
        return None
    for facet in facets:
        question = str(facet.get('question') or facet.get('text') or '')
        kind = facet.get('kind')
        if (kind in {'risk_relation', 'definition', 'distinction', 'method_steps'} and
                re.search(r'疫情|病例|(?:是否|已经|当地).{0,8}暴发', text) and
                not re.search(r'截至|今天|今日|当前|现在|实时|20\d{2}|有多少|病例数|疫情实况|是否(?:已经)?暴发|暴发了吗', question)):
            continue
        if (kind == 'channel' and
                re.search(r'独立(?:的|访问)?(?:网址|链接)|当前运行状态|当前是否正常运行|实时预警内容|完整.{0,8}产品清单|页面布局|入口有效性', text) and
                not re.search(r'独立.{0,4}(?:网址|链接)|具体网址|完整.{0,8}产品清单|当前|现在|实时|是否有效|能否访问|页面布局', question)):
            continue
        if (re.search(r'保费补贴|保费补助|财政补贴|保险补贴', text) and
                not re.search(r'补贴|补助|保费|费用|多少钱|金额|标准', question)):
            continue
        return None
    return 'outside_requested_scope'


def _text(item):
    return str(item.get('text') or item.get('missing_information') or item.get('gap') or '') if isinstance(item, dict) else str(item)


def _facet_candidates(item, contract, coverage):
    if isinstance(item, dict):
        if item.get('facet_ids'):
            identities = {str(value) for value in item['facet_ids']}
            return [f for f in contract if str(f.get('id')) in identities or str(f.get('fid')) in identities]
        identity = item.get('fid', item.get('id', item.get('subquestion_id')))
        if identity is not None:
            return [f for f in contract if identity in {f.get('id'), f.get('fid')}]
    value = _text(item)
    mapped = []
    for row in coverage or []:
        if any(value == _text(gap) for gap in row.get('missing_information', [])):
            identity = row.get('fid', row.get('id'))
            mapped.extend(f for f in contract if identity in {f.get('id'), f.get('fid')})
    if mapped:
        return mapped
    scored = [(sum(obj in value for obj in f.get('objects', []) if len(obj) > 1), f) for f in contract]
    top = max((score for score, _ in scored), default=0)
    return [f for score, f in scored if score == top] if top else list(contract)


def _protected(gap_type, facets, text):
    # Type disagreements are resolved conservatively. A current-status facet
    # cannot lose its dated evidence just because a reviewer calls it unasked.
    for facet in facets:
        # Input definitions and individual adjudication never become optional
        # solely because a planner assigned an informational facet kind.
        if gap_type in {'calculation_input_missing', 'individual_determination_required'}:
            return True
        question = str(facet.get('question') or facet.get('text') or '')
        if (re.search(r'网址|链接|URL', question, re.I) and re.search(r'网址|链接|URL', text, re.I)):
            return True
        if (re.search(r'补贴|补助|保费', question) and
                re.search(r'保费补贴|保费补助|财政补贴|保险补贴', text)):
            return True
        if gap_type in HARD_GAPS.get(facet.get('kind'), ()):
            return True
        if facet.get('kind') == 'status_at_time' and re.search(r'公告|有效|开放|当前|当日|发布|当地', text):
            return True
        if (re.search(r'现行|当前有效|是否有效|仍然有效|法定|目前适用', facet.get('question', '')) and
                re.search(r'版本|生效|修订|废止|有效|现行', text)):
            return True
        if (facet.get('kind') == 'procedure' and
                re.search(r'救助|救济|赔付|赔偿|保险', facet.get('question', '')) and
                re.search(r'申请材料|受理时限|补助标准|申请窗口|办理窗口', text)):
            return True
        if facet.get('place_role') == 'jurisdiction_required' and gap_type == 'jurisdiction_rule_not_found':
            if facet.get('kind') in {'basis_document', 'procedure', 'method_steps'}:
                return True
    return False


def _nonrequired_dimension_gap(text, facets):
    """Only remove a standalone dimension the contract explicitly excludes.

    List-count units and municipal jurisdictions are not physical forecast
    units or storm coordinates. Mixed missing inputs, actual requested region
    filters, valid times, source provenance and numerical definitions remain.
    """
    if not facets or re.search(r'[；;\n]|高程|基准|SWL|潮位|增水|浪高|风速|经纬|经度|纬度|范围|统计口径|时刻|日期|时间|来源|出处|数值', text, re.I):
        return False
    unit_only = bool(re.fullmatch(r'(?:尚?未(?:明确|取得|提供|核实|核定)|尚?缺(?:少)?|没有)?\s*(?:该数量的|计数的|物理量的)?(?:物理量)?(?:计量)?单位(?:资料|信息|说明|定义|核定|确认|尚未核定|未核实)?[。\s]*', text))
    place_only = bool(re.fullmatch(r'(?:尚?未(?:明确|取得|提供|核实)|尚?缺(?:少)?|没有)?\s*(?:适用|所问|具体)?(?:行政区|行政地点|行政区划|地点|地区)(?:资料|信息|说明|覆盖|尚未明确|未明确)?[。\s]*', text))
    if not unit_only and not place_only:
        return False
    for facet in facets:
        if facet.get('quantity_type') != 'count' and facet.get('domain') != 'typhoon_dynamics':
            return False
        required = set(facet.get('required_outputs') or [])
        if unit_only and 'unit' in required:
            return False
        if place_only and ('place' in required or facet.get('places')):
            return False
    return True


def gate(missing, contract, review_issues, *, subquestion_coverage=None):
    kept, removed, decisions, scope_notes, replacements = [], [], [], [], []
    flags = [str(i.get('text') or '') for i in review_issues or []
             if i.get('kind') == 'unasked_gap' and i.get('text')]
    for item in missing or []:
        value = _text(item)
        facets = _facet_candidates(item, contract, subquestion_coverage)
        parts = _gap_units(value)
        retained_parts = []
        for part in parts:
            types = {classify_gap(part, facet) for facet in facets} or {'unclassified'}
            # The critic can point to this exact item, its complete parent, or
            # an exact independently recognized child.  Similar paraphrases
            # and arbitrary interior substrings do not authorize an edit.
            flagged = any(span == value or span == part or
                          len(parts) == 1 and span in part for span in flags)
            nonrequired_dimension = _nonrequired_dimension_gap(part, facets)
            protected = not nonrequired_dimension and any(_protected(typ, facets, part) for typ in types)
            compatible = any(typ in facet.get('allowed_gaps', []) for facet in facets for typ in types)
            outside = _unasked_reason(part, facets)
            mixed = len(re.split(r'[；;\n]', part)) > 1
            can_remove = bool(facets and not protected and not mixed and
                              (nonrequired_dimension or 'unclassified' not in types and (flagged or outside)))
            decision = {'text': part, 'parent_text': value,
                        'facet_ids': [f.get('fid') or f.get('id') for f in facets],
                        'gap_types': sorted(types), 'reviewed_exact_span': flagged,
                        'protected': protected, 'allowed_by_contract': compatible,
                        'action': 'remove_unasked_gap' if can_remove else 'keep',
                        'rule': ('dimension_not_required_by_typed_contract' if nonrequired_dimension else
                                 'reviewer_exact_unasked_nonhard' if can_remove and flagged else
                                 'contract_scope_note' if can_remove else
                                 'hard_gap_protected' if protected else 'not_exactly_reviewed_or_ambiguous')}
            decisions.append(decision)
            if can_remove:
                removed.append(decision)
                scope_notes.append({**decision, 'kind': 'not_requested',
                                    'does_not_block_delivery': True})
            else:
                retained_parts.append(part)
        if retained_parts:
            replacement = value if len(retained_parts) == len(parts) else '；'.join(retained_parts)
            kept.append(replacement)
        else:
            replacement = None
        if replacement != value:
            replacements.append({'original': value, 'replacement': replacement,
                                 'facet_ids': [f.get('fid') or f.get('id') for f in facets]})
    return {'kept': kept, 'scope_notes': scope_notes, 'removed': removed, 'decisions': decisions,
            'replacements': replacements,
            'answerability_upgrade_allowed': False}


def gate_answer(answer, missing, contract, review_issues, *, subquestion_coverage=None):
    """Remove only a complete, exclusively limiting unit copied by a critic.

    Substrings inside mixed factual sentences are never edited. This prevents
    a scope cleanup from destroying a supported sibling answer or citation.
    """
    result = gate(missing, contract, review_issues, subquestion_coverage=subquestion_coverage)
    units = re.split(r'(\n\s*\n|(?<=[。！？]))', answer)
    removed_units = []
    for issue in review_issues or []:
        span = str(issue.get('text') or '')
        if issue.get('kind') != 'unasked_gap' or not span:
            continue
        for index, unit in enumerate(units):
            if unit.strip() != span.strip():
                continue
            limitation = re.search(r'未提供|未取得|未覆盖|未核|缺少|缺乏|尚缺|尚未|没有.{0,8}(?:资料|数据|依据)', unit)
            if not limitation:
                continue
            prefix = unit[:limitation.start()]
            # Even a critic's exact paragraph target is not an instruction to
            # delete a factual/action prefix. This gate never parses or
            # rewrites mixed truth conditions, conjunctions or negation.
            if (re.search(r'[，,；;]|\[资料|检查|加固|清理|修复|申请|审核|批准|签发|下达|发放|是|按照|依据', prefix) or
                    re.search(r'(?:如果|只有|除非|当.{0,12}时|若)', prefix)):
                continue
            local = gate([unit.strip()], contract, [issue], subquestion_coverage=subquestion_coverage)
            if local['removed'] and not local['kept']:
                units[index] = ''
                removed_units.append({'text': unit, 'rule': 'exact_reviewed_complete_limitation_unit'})
    result.update(answer=''.join(units).strip(), removed_answer_units=removed_units)
    return result


def gate_atoms(accepted_atoms, contract, review_issues=None):
    """Apply the same scope decisions to typed gap atoms after any rewrite.

    Substantive facts and scope qualifiers are never edited here.  Returning a
    new atom list lets the caller recalculate delivery rather than inheriting
    a model's pre-cleanup ``full`` or an initial metadata snapshot.
    """
    atoms, audit, removed_ids = [], [], []
    for atom in accepted_atoms:
        if atom.get('type') != 'gap':
            atoms.append(atom)
            continue
        result = gate([atom], contract, review_issues or [])
        audit.append({'aid': atom.get('aid'), **result})
        if not result['kept']:
            removed_ids.append(atom.get('aid'))
            continue
        text = result['kept'][0]
        if text != _text(atom):
            atoms.append({**atom, 'text': text, 'display_text': text})
        else:
            atoms.append(atom)
    return {'accepted_atoms': atoms, 'removed_gap_atom_ids': removed_ids,
            'gap_atom_scope': audit, 'answerability_upgrade_allowed': False}


def _outputs_named_by_gap(text, facet):
    """Map a gap to required slots without treating a model's label as proof."""
    required = set(facet.get('required_outputs') or [])
    names = []
    for expression, outputs in (
        (r'渠道|入口|网址|链接|查询方式|查询步骤', {'channel_name', 'access_path'}),
        (r'定义|含义', {'definition'}),
        (r'区别|替代|比较|一回事|归纳|结论', {'verdict', 'comparison'}),
        (r'依据|文件|条款', {'document_identity', 'basis_content'}),
        (r'流程|程序|步骤|方法|怎么', {'steps', 'actors', 'application_path'}),
        (r'风险|传播|传染|关系|条件', {'relation', 'conditions'}),
    ):
        if re.search(expression, text):
            names.extend(required & outputs)
    return set(names) or required


def reconcile_gap_resolution(missing, facets, accepted_atoms):
    """Resolve a narrowly bound gap only with *new*, grounded recovery facts.

    This does not promote an answer. The caller must recalculate facet delivery
    and every business/relationship check after accepting this change. It is
    intentionally unable to certify eligibility, current legal editions,
    permits, site safety or a supposedly complete provincial warning archive.

    Binding must be explicit: a missing item may carry ``fid``/legacy ``id``;
    otherwise an equal gap atom with one immutable facet owner is required.
    Recovery's ``local1_`` namespace identifies new atoms, not a model-proposed
    full label. Frozen pre-recovery facts cannot erase an earlier limitation.
    """
    from .facet_delivery import evaluate_delivery, covered_outputs
    from .evidence_applicability import DATE, MD, parse_date
    table = {str(f.get('fid') or f.get('id')): f for f in facets}
    new = [a for a in accepted_atoms if str(a.get('aid', '')).startswith('local1_') and
           a.get('type') in {'direct_fact', 'calculation', 'document_identity', 'synthesis'} and
           any(isinstance(r, dict) and type(r.get('citation')) is int and r['citation'] > 0 and
               (r.get('passage_id', 1) != 0 or a.get('type') == 'document_identity') and
               r.get('quote') for r in a.get('evidence_quotes', []))]
    clean = lambda text: re.sub(r'[\s。；;]', '', re.sub(r'\[资料\d+\]', '', str(text)))
    kept, resolved, decisions, dropped_gap_ids = [], [], [], set()
    prohibited = re.compile(r'个人|资格|参保|保单|赔付条件|合同|现行|修订|废止|版本|高程基准|SWL|SSW|'
                            r'水质|救生|返程|开放|许可|准入|允许|批准|复航|停航|现场|游泳|赶海|岸钓|船钓|'
                            r'完整清单|全量|全部|所有|解除链|平均|统计口径|官方授权', re.I)
    for item in missing or []:
        text = _text(item)
        own_gaps = [a for a in accepted_atoms if a.get('type') == 'gap' and clean(a.get('text')) == clean(text)]
        if isinstance(item, dict) and item.get('fid', item.get('id')) is not None:
            identity = item.get('fid', item.get('id'))
            target = [f for f in facets if identity in {f.get('id'), f.get('fid')}]
        else:
            ids = {str(fid) for a in own_gaps for fid in a.get('facet_ids', [])}
            target = [table[fid] for fid in ids if fid in table]
        reason = 'gap_not_explicitly_bound'
        proof = []
        if len(target) == 1:
            facet = target[0]
            fid = str(facet.get('fid') or facet.get('id'))
            kind = facet.get('kind')
            gap_type = classify_gap(text, facet)
            if prohibited.search(text + '\n' + facet.get('question', '')):
                reason = 'protected_business_or_permission_gap'
            elif (kind not in {'numeric_value', 'status_at_time'} and _protected(gap_type, [facet], text)
                  and not (kind == 'channel' and re.search(r'网址|链接', text))):
                reason = 'hard_gap_requires_independent_verification'
            elif kind in {'eligibility', 'calculation', 'historical_fact'}:
                reason = 'gap_kind_requires_separate_verification'
            elif kind not in {'numeric_value', 'status_at_time'}:
                # A recovered informational facet may combine its own frozen
                # facts and *new* grounded premises, never siblings' facts.
                # The body requirement below prevents document metadata alone
                # from resolving a missing substantive answer.
                own = [a for a in accepted_atoms if a.get('type') != 'gap' and a.get('facet_ids') == [fid]]
                additions = [a for a in new if a.get('facet_ids') == [fid]]
                required = set(facet.get('required_outputs') or [])
                new_outputs = covered_outputs(facet, additions)
                all_outputs = covered_outputs(facet, own)
                old_outputs = covered_outputs(facet, [a for a in own if a not in additions])
                matched = _outputs_named_by_gap(text, facet)
                grounded_body = any(a.get('type') in {'direct_fact', 'calculation', 'synthesis'} for a in additions)
                progress = bool(matched & new_outputs) and bool(matched - old_outputs)
                # If the old gap was stale before recovery, an independently
                # grounded replacement explicitly addressing it is still a
                # valid lifecycle transition. No new claim: no transition.
                fresh_restatement = bool(matched <= new_outputs)
                if not additions:
                    reason = 'no_new_bound_grounded_recovery'
                elif not grounded_body:
                    reason = 'metadata_only_cannot_resolve_body_gap'
                elif re.search(r'网址|链接', text) and not re.search(r'https?://|www\.', '\n'.join(a.get('text', '') for a in additions)):
                    reason = 'requested_url_not_delivered'
                elif required and required <= all_outputs and matched <= all_outputs and (progress or fresh_restatement):
                    proof = [a['aid'] for a in additions]
                    reason = 'resolved_by_new_grounded_facet_delivery'
                else:
                    reason = 'new_recovery_facet_delivery_incomplete'
            elif gap_type not in {'business_data_missing', 'live_status_not_available'}:
                reason = 'gap_requires_separate_input_verification'
            else:
                scope = facet.get('time') or {}
                start, end = parse_date(scope.get('start')), parse_date(scope.get('end'), end=True)
                object_scoped = facet.get('quantity_type') == 'count' or facet.get('domain') == 'typhoon_dynamics'
                if not start or not end or not facet.get('places') and not object_scoped:
                    reason = 'target_tuple_not_bounded'
                else:
                    bounded = []
                    for candidate in new:
                        if candidate.get('facet_ids') != [fid]:
                            continue
                        dates = list(DATE.finditer(candidate.get('text', '')))
                        if not dates:
                            dates = list(MD.finditer(candidate.get('text', '')))
                        if not dates:
                            continue
                        stamps = [parse_date(match.group(), start) for match in dates]
                        # Every explicit date in the result belongs to the
                        # target range; a cited publication date cannot be
                        # substituted for the valid time of an outside value.
                        if any(stamp is None or not start <= stamp <= end for stamp in stamps):
                            continue
                        bounded.append(candidate)
                        row = evaluate_delivery([facet], [candidate])[0]
                        if row['status'] == 'full':
                            proof.append(candidate['aid'])
                    if object_scoped and not proof and bounded:
                        # Multiple independently grounded fields from one
                        # object-scoped subquestion may form its delivery;
                        # physical forecasts still require a single tuple.
                        row = evaluate_delivery([facet], bounded)[0]
                        if row['status'] == 'full':
                            proof = [candidate['aid'] for candidate in bounded]
                    reason = 'resolved_by_new_bound_grounded_tuple' if proof else 'new_recovery_tuple_incomplete'
        decision = {'text': text, 'facet_ids': [f.get('fid') for f in target],
                    'action': 'resolve' if proof else 'keep', 'rule': reason,
                    'new_grounded_atom_ids': proof}
        decisions.append(decision)
        if proof:
            resolved.append(decision)
            dropped_gap_ids.update(a['aid'] for a in own_gaps)
        else:
            kept.append(item)
    return {'kept': kept, 'resolved': resolved, 'decisions': decisions,
            'accepted_atoms': [a for a in accepted_atoms if a.get('aid') not in dropped_gap_ids],
            'removed_gap_atom_ids': sorted(dropped_gap_ids), 'answerability_upgrade_allowed': False}
