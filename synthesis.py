"""Cited answer synthesis with literal supports, numeric checks, and model audit.

The audit is a second pass of the same local model, not independent certification.
When any sentence fails, the whole generated answer falls back to verified quotes.
"""
import re
import time


def validate_sentences(rows, evidence):
    lookup = {e['id']: e for e in evidence}
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5: raise ValueError('回答句数无效')
    result = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict): raise ValueError('回答格式无效')
        text = row.get('text')
        supports = row.get('supports')
        if not isinstance(text, str) or not 1 <= len(text) <= 600: raise ValueError('回答句长度无效')
        if not isinstance(supports, list) or not 1 <= len(supports) <= 4: raise ValueError('每句必须提供原文支持')
        spans = []
        for support in supports:
            if not isinstance(support, dict) or support.get('id') not in lookup: raise ValueError('引用了不存在的证据')
            e = lookup[support['id']]; quote = support.get('quote')
            if not isinstance(quote, str) or len(quote) < 4: raise ValueError('支持原文无法逐字定位')
            located = locate_quote(e['text'], quote)
            if not located: raise ValueError('支持原文无法逐字定位')
            offset,end_offset,canonical = located
            if e['text'].find(canonical, offset + 1) != -1: raise ValueError('支持原文位置不唯一')
            spans.append(dict(chunk_id=e['id'], citation=e['citation'], quote=canonical,
                              start=e['start'] + offset, end=e['start'] + end_offset))
        # This is only a structural gate; entailment is checked separately below.
        numbers = re.findall(r'\d+(?:\.\d+)?', text)
        supported_numbers = set(re.findall(r'\d+(?:\.\d+)?', ''.join(s['quote'] for s in spans)))
        if any(n not in supported_numbers for n in numbers): raise ValueError('回答包含引用中不存在的数值')
        result.append(dict(id='A'+str(i+1), text=text, supports=spans,
                           citations=list(dict.fromkeys(s['citation'] for s in spans))))
    return result


def enrich(engine, result, progress=None):
    if not result.get('evidence'): return dict(result, sentences=[], answer_mode='abstained')
    start = time.monotonic(); emit = progress or (lambda *args: None)
    evidence = result['evidence']
    fallback = [dict(id='A'+str(i+1), text=e['text'], citations=[e['citation']],
                    supports=[dict(chunk_id=e['id'], citation=e['citation'], quote=e['text'], start=e['start'], end=e['end'])])
                for i, e in enumerate(evidence) if e.get('role') != 'scope_context']
    generation = dict(model='Qwen3-4B-Instruct-2507-4bit', audit='same_model_second_pass', state='not_started')
    try:
        if not engine.evidence_guard: raise RuntimeError('本地生成与复核模型未启用')
        if sum(len(e['text']) for e in evidence) > 14000: raise RuntimeError('证据超出本地整理窗口，保留完整原文')
        emit('compose', '根据核验后的证据整理逐句回答')
        inputs = [dict(id=e['id'], text=e['text'], context=e.get('context', []), role=e.get('role', 'direct')) for e in evidence]
        drafted = engine.evidence_guard.request(dict(mode='compose', question=result['question'], evidence=inputs))
        sentences = validate_sentences(drafted.get('sentences'), evidence)
        emit('audit', '检查每句回答的引用、数值和适用条件')
        audited = engine.evidence_guard.request(dict(mode='audit_answer', question=result['question'], evidence=inputs, sentences=sentences))
        approved = audited.get('approved')
        if not isinstance(approved, list) or any(type(x) is not int for x in approved) or set(approved) != set(range(len(sentences))):
            generation['reasons'] = audited.get('reasons', [])
            raise ValueError('整理后的回答未全部通过逐句证据复核')
        generation.update(state='verified', sentence_count=len(sentences))
        result.update(sentences=sentences, answer_mode='synthesized', answer='依据已收录版本整理；点击句末引用，核对关系和原文。')
    except Exception as ex:
        generation.update(state='fallback', reason=str(ex))
        result.update(sentences=fallback, answer_mode='extractive_fallback', answer='本次保留经核验的原文回答，未展示未通过检查的改写。')
    if result.get('status') == 'partial': result['answer'] = '部分子问题证据不足。' + result['answer']
    generation['latency_ms'] = round((time.monotonic()-start)*1000)
    result['generation'] = generation
    result['latency_ms'] = result.get('latency_ms', 0) + generation['latency_ms']
    return result
def locate_quote(source, quote):
    """Return a source-aligned span; whitespace-only OCR/model differences are normalized."""
    offset = source.find(quote)
    if offset >= 0:
        return offset, offset + len(quote), source[offset:offset+len(quote)]
    compact_source = ''.join(ch for ch in source if not ch.isspace())
    compact_quote = ''.join(ch for ch in quote if not ch.isspace())
    if len(compact_quote) < 4: return None
    at = compact_source.find(compact_quote)
    if at < 0: return None
    positions = [i for i,ch in enumerate(source) if not ch.isspace()]
    start = positions[at]; end = positions[at+len(compact_quote)-1] + 1
    return start, end, source[start:end]

