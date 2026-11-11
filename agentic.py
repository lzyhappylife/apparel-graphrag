"""Bounded agentic retrieval for evidence-first GraphRAG.

The planner can propose only questions and search terms. Retrieval is still
performed by the existing evidence-gated engine, and every loop has a hard
budget. Model output is treated as data, validated, and never used as code.
"""
import hashlib
import json
import re
import time


MAX_SUBQUESTIONS = 4
MAX_ITERATIONS = 2
MAX_FOLLOWUPS = 2


def _text(value, name, minimum=1, maximum=240):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{name}格式无效')
    if '\x00' in value or value.count('\n') > 4:
        raise ValueError(f'{name}包含非法控制字符')
    return value.strip()


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError('Agentic 规划不是对象')
    intent = _text(plan.get('intent', ''), '规划意图', 1, 160)
    raw = plan.get('subquestions')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SUBQUESTIONS:
        raise ValueError('Agentic 子问题数量无效')
    rows = []
    seen = set()
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError('Agentic 子问题格式无效')
        question = _text(row.get('question', ''), '子问题', 2, 260)
        key = re.sub(r'\s+', '', question)
        if key in seen:
            continue
        seen.add(key)
        focus = _text(row.get('focus', '一般服装供应链证据'), '子问题焦点', 1, 100)
        terms = row.get('required_terms', [])
        if not isinstance(terms, list) or len(terms) > 8 or any(not isinstance(x, str) or not 1 <= len(x) <= 60 for x in terms):
            raise ValueError('子问题检索词格式无效')
        priority = row.get('priority', i + 1)
        if type(priority) is not int or not 1 <= priority <= 10:
            priority = i + 1
        rows.append(dict(id='q'+str(len(rows)+1), question=question, focus=focus,
                         required_terms=list(dict.fromkeys(terms)), priority=priority))
    if not rows:
        raise ValueError('Agentic 没有有效子问题')
    stop = plan.get('stop_when', [])
    if not isinstance(stop, list): stop = []
    stop = [_text(x, '停止条件', 1, 120) for x in stop[:5] if isinstance(x, str)]
    constraints = plan.get('constraints', [])
    if not isinstance(constraints, list): constraints = []
    constraints = [_text(x, '规划约束', 1, 160) for x in constraints[:6] if isinstance(x, str)]
    return dict(intent=intent, subquestions=rows, stop_when=stop, constraints=constraints,
                max_iterations=MAX_ITERATIONS)


def validate_gap(gap):
    if not isinstance(gap, dict):
        raise ValueError('Agentic 覆盖检查不是对象')
    coverage = gap.get('coverage', 'partial')
    if coverage not in ('complete', 'partial', 'none'):
        coverage = 'partial'
    missing = gap.get('missing', [])
    if not isinstance(missing, list): missing = []
    rows = []
    for row in missing[:MAX_FOLLOWUPS]:
        if not isinstance(row, dict): continue
        try:
            aspect = _text(row.get('aspect', ''), '缺口主题', 1, 120)
            question = _text(row.get('question', ''), '补检索问题', 2, 260)
        except ValueError:
            continue
        reason = row.get('reason', '')
        if not isinstance(reason, str): reason = ''
        rows.append(dict(aspect=aspect, question=question, reason=reason[:180]))
    stop = gap.get('stop')
    if type(stop) is not bool:
        stop = coverage == 'complete' or not rows
    return dict(coverage=coverage, missing=rows, stop=stop,
                reason=str(gap.get('reason', ''))[:240])


def _inputs(evidence):
    return [dict(id=e['id'], text=e['text'], context=e.get('context', []),
                 role=e.get('role', 'direct'), source=e.get('source', '')) for e in evidence]


def _merge_evidence(results):
    merged = {}
    for result in results:
        for item in result.get('evidence', []):
            # Prefer direct evidence over a scope-only copy of the same clause.
            old = merged.get(item['id'])
            if old is None or (old.get('role') == 'scope_context' and item.get('role') != 'scope_context'):
                merged[item['id']] = item
    return list(merged.values())


def _merge_stage(results, key):
    rows = {}
    for result in results:
        for row in result.get('stages', {}).get(key, []):
            rid = row.get('id') or row.get('target')
            if rid is not None: rows[rid] = row
    return list(rows.values())


def run(e, original, doc_id=None, progress=None, limit=6):
    """Run a bounded plan/retrieve/gap-fill loop and return a normal query result."""
    emit = progress or (lambda *args: None)
    started = time.monotonic()
    guard = e.evidence_guard
    if guard is None or e.semantic is None:
        return None
    emit('plan', '本地 Agent 正在拆解问题并规划证据路径')
    try:
        from schemas import domain_profile
        schema = domain_profile().get('name', '垂直证据知识库')
        planned = guard.request(dict(mode='agent_plan', question=original, domain=schema))
        plan = validate_plan(planned)
    except Exception as ex:
        return dict(error='Agentic 规划失败：'+str(ex)[:180], fallback=True)

    plan_rows = sorted(plan['subquestions'], key=lambda x: x['priority'])
    results = []
    completed_questions = set()
    iterations = []
    gap = dict(coverage='partial', missing=[], stop=False, reason='尚未检查覆盖率')
    followups = plan_rows
    for iteration in range(MAX_ITERATIONS):
        if not followups: break
        iteration_rows = []
        for sub in followups[:MAX_SUBQUESTIONS]:
            normalized = re.sub(r'\s+', '', sub['question'])
            if normalized in completed_questions: continue
            completed_questions.add(normalized)
            emit('agent_retrieve', f'Agent 补充检索：{sub["focus"]}')
            result = e.query(sub['question'], limit=limit, doc_id=doc_id, agentic=False,
                             progress=lambda key, label, focus=sub['focus']: emit('agent_retrieve', focus+' · '+label))
            result['_agent_subquestion'] = sub
            results.append(result)
            supported = bool(result.get('evidence')) and result.get('status') != 'abstained'
            iteration_rows.append(dict(id=sub['id'], question=sub['question'], focus=sub['focus'],
                                       supported=supported, evidence=[x['id'] for x in result.get('evidence', [])],
                                       status=result.get('status')))
        merged = _merge_evidence(results)
        emit('gap', 'Agent 正在检查证据覆盖率并寻找缺口')
        try:
            gap_raw = guard.request(dict(mode='agent_gap', question=original, plan=plan,
                                         subquestions=iteration_rows, evidence=_inputs(merged)))
            gap = validate_gap(gap_raw)
        except Exception as ex:
            gap = dict(coverage='partial', missing=[], stop=True, reason='覆盖检查失败：'+str(ex)[:160])
        iterations.append(dict(index=iteration+1, subquestions=iteration_rows, evidence_count=len(merged), gap=gap))
        if gap['stop'] or iteration + 1 >= MAX_ITERATIONS:
            break
        followups = [dict(id='followup-'+str(i+1), question=x['question'], focus=x['aspect'],
                          required_terms=[], priority=i+1) for i, x in enumerate(gap['missing'][:MAX_FOLLOWUPS])]

    evidence = _merge_evidence(results)
    if not evidence:
        return dict(question=original, status='abstained', answer='Agentic 检索没有找到通过复核的证据。', evidence=[],
                    graph=e.graph([]), trace=[], entities=[], mode='agentic', agentic=dict(enabled=True, plan=plan, iterations=iterations, gap=gap))

    emit('agent_verify', 'Agent 汇总证据并进行最终问题适用性复核')
    final_check = dict(mode='select', selected=[x['id'] for x in evidence])
    try:
        verified = guard.select(original, _inputs(evidence))
        if verified:
            keep = set(verified)
            # Preserve scope clauses alongside a verified direct clause; they
            # carry the subject, condition, and exception needed for synthesis.
            evidence = [x for x in evidence if x['id'] in keep or x.get('role') == 'scope_context']
            final_check.update(selected=verified, state='verified')
        else:
            final_check.update(selected=[], state='empty_union_preserved')
    except Exception as ex:
        final_check.update(state='failed_closed', error=str(ex)[:180])

    if not evidence:
        return dict(question=original, status='abstained', answer='Agentic 最终复核没有留下可直接支持问题的条款。', evidence=[],
                    graph=e.graph([]), trace=[], entities=[], mode='agentic', agentic=dict(enabled=True, plan=plan, iterations=iterations, gap=gap, final_check=final_check))

    roles = {x['id']: x.get('role', 'direct') for x in evidence}
    ids = [x['id'] for x in evidence]
    claims = [dict(text=x['text'], citation='E'+str(i+1), chunk_id=x['id'], role=roles[x['id']], context=x.get('context', [])) for i,x in enumerate(evidence)]
    for i,x in enumerate(evidence): x['citation']='E'+str(i+1)
    # Re-label graph/path references using the final set; graph edges remain source-grounded.
    graph = e.graph(ids)
    stages={'candidates':_merge_stage(results,'candidates'), 'expansion':_merge_stage(results,'expansion'),
            'final':[dict(id=x['id'], citation=x['citation'], role=x.get('role','direct')) for x in evidence]}
    subquestions=[]
    for item in iterations:
        subquestions.extend(item['subquestions'])
    trace=[]
    for result in results:
        trace.extend(result.get('trace', []))
    dedup_trace={x.get('id'):x for x in trace if x.get('id')}
    agentic=dict(enabled=True, planner='Qwen3-4B-Instruct-2507-4bit', plan=plan,
                 iterations=iterations, gap=gap, final_check=final_check,
                 stopped_reason='覆盖完成' if gap.get('coverage')=='complete' else ('达到两轮预算' if len(iterations)>=MAX_ITERATIONS else gap.get('reason','无可执行补检索')))
    return dict(question=original, status='partial' if any(not x['supported'] for x in subquestions) else 'evidence_found',
                answer='Agentic 检索已完成；以下回答均来自通过复核的原文证据。', claims=claims, evidence=evidence,
                graph=graph, paths=[], subquestions=subquestions, trace=list(dedup_trace.values()), stages=stages,
                verification={'enabled':True,'model':'Qwen3-4B-Instruct-2507-4bit','selected':[x['id'] for x in evidence]},
                entities=[], mode='agentic', agentic=agentic,
                latency_ms=round((time.monotonic()-started)*1000))
