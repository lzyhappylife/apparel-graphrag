"""Graph expansion, reranking, answerability gates and exact-evidence responses."""
import re,time
# Reviewed context dependencies preserve scope even if a short numeric clause ranks first.
CONTEXT={
 'S1:7.9.2':['S1:7.9.1'],'S1:7.9.3':['S1:7.9.1'],'S1:7.9.4':['S1:7.9.1'],
 'S1:7.8.2':['S1:7.8.1'],'S1:7.7.3':['S1:7.7.1','S1:7.7.2'],
 'S1:8.3.3.3':['S1:8.3.3'],'S1:8.3.4.4':['S1:8.3.4']}
# These are retrieval relevance thresholds, not probabilities of correctness.
MIN_RELEVANCE=-3.0
MAX_GAP=2.2

def query(e,original,expanded,lexical,trace,entities,limit,doc_id=None,progress=None):
 emit=progress or (lambda *args:None)
 emit('retrieve','正在进行关键词与语义向量召回')
 start=time.monotonic();dense,windows=e.semantic.search(expanded)
 if doc_id:dense={c:v for c,v in dense.items() if e.chunks[c]['doc_id']==doc_id}
 lexical_order=sorted(lexical,key=lexical.get,reverse=True)
 dense_order=sorted(dense,key=dense.get,reverse=True)
 # Reciprocal rank fusion treats lexical and semantic retrieval on equal rank scales.
 fused={}
 for ranking in [lexical_order,dense_order]:
  for i,cid in enumerate(ranking[:50]):fused[cid]=fused.get(cid,0)+1/(40+i+1)
 candidates=sorted(fused,key=fused.get,reverse=True)[:24];via={}
 emit('expand','融合候选并沿已审核关系扩展')
 seed_scores=e.semantic.rerank(expanded,candidates,windows)
 seeds=sorted(seed_scores,key=seed_scores.get,reverse=True)[:4]
 frontier=seeds[:]
 for depth in range(2):
  following=[]
  for cid in frontier:
   destinations=[]
   for edge in e.edges:
    if edge['chunk_id']!=cid:continue
    if edge['kind']=='reference':destinations.append((edge['target'],'显式条款引用'))
    elif edge['kind']=='reviewed':
     # Traverse reviewed business edges sharing an endpoint, not all co-occurrences.
     for linked in e.edges:
      if linked['kind']=='reviewed' and linked['chunk_id']!=cid and set((edge['source'],edge['target']))&set((linked['source'],linked['target'])):
       destinations.append((linked['chunk_id'],'业务关系相邻'))
   destinations.extend((x,'适用条件依赖') for x in CONTEXT.get(cid,[]))
   for dest,kind in destinations:
    if dest in dense and dest not in seeds and dest not in via:
     via[dest]={'from':cid,'kind':kind,'depth':depth+1};following.append(dest)
  frontier=following[:16]
 emit('rerank','重排候选并检查适用范围')
 extras=[cid for cid in via if cid not in seed_scores][:20]
 scores=dict(seed_scores,**e.semantic.rerank(expanded,extras,windows))
 subquestions=[];subselected=[]
 operations=[x for x in ('采购','纺纱','织造','染色','印花','整理','检测','审核','装运','维修','回收') if x in expanded]
 if len(operations)>1 and re.search(r'和|及|与|、|分别',original):
  for operation in operations[:4]:
   sub=operation+'有什么要求？';ds,ws=e.semantic.search(sub)
   if doc_id:ds={c:v for c,v in ds.items() if e.chunks[c]['doc_id']==doc_id}
   subids=sorted(ds,key=ds.get,reverse=True)[:12]
   if not subids:
    subquestions.append({'question':sub,'supported':False,'evidence':None});continue
   ss=e.semantic.rerank(sub,subids,ws);best=max(ss,key=ss.get);ok=ss[best]>=MIN_RELEVANCE
   subquestions.append({'question':sub,'supported':ok,'evidence':best if ok else None,'rerank':round(ss[best],4)})
   if ok:
    for match in sorted(ss,key=ss.get,reverse=True)[:3]:
     if ss[match]>=max(MIN_RELEVANCE,ss[best]-MAX_GAP):
      subselected.append(match);scores.setdefault(match,ss[match]);dense.setdefault(match,ds[match]);via[match]={'from':'query','kind':'子问题覆盖','depth':0}
 ranked=sorted(scores,key=scores.get,reverse=True)
 reject=None
 if re.search(r'最新|现行|截至|当前有效|(?:今天|现在).*(?:规定|政策|法规|标准)',original):reject='已收录版本不能证明当前法律效力。请先核定适用地域及现行版本。'
 if not ranked or scores[ranked[0]]<MIN_RELEVANCE:reject='检索到的资料不能充分回答这个问题。请补充相关资料或说明具体操作场景。'
 selected=[cid for cid in ranked if scores[cid]>=max(MIN_RELEVANCE,scores[ranked[0]]-MAX_GAP)][:min(limit,4)]
 # A normative corpus cannot establish a named business's measurements or past events.
 # A mixed question is conservatively refused until its factual part has a source.
 needs_records=bool(re.search(r'\d{4}年\d{1,2}月\d{1,2}日|昨晚|昨天|前天|上周|本周|这周|实测|当班|当日|发生了|完成了',original) and re.search(r'订单|工厂|批次|生产|供应商|检验|记录|签字|营业额|不良率|色差|起球|缩水',original))
 if needs_records and not any(e.chunks[c]['doc_id'].startswith('U') for c in selected):
  reject='该问题包含具体订单、日期、批次或实际业务记录。公开规范只能说明应当如何操作，不能证明已经发生了什么。请导入并核验对应的供应链或质量记录后再查询。'
 # A relevant reference to another standard is insufficient to answer a numeric limit.
 if re.search(r'多少|最大|上限|最低|最高',original) and re.search(r'用量|添加量|浓度|剂量',original):
  if not any(re.search(r'\d+(?:\.\d+)?\s*(?:mg|g|克|毫克|%|％|毫升|mL)',e.chunks[c]['text']) for c in selected):
   reject='库内条款只给出原则或引用其他标准，没有该问题所需的具体数值。请补充添加剂/消毒剂名称、用途及适用标准。'
 if re.search(r'号码|编号|联系电话|联系方式',original) and re.search(r'是多少|是什么|查一下|查询',original) and not re.search(r'组成|规则|格式|几位|编制',original):
  if not any(e.chunks[c]['doc_id'].startswith('U') for c in selected):reject='当前库是公开参考资料，不包含具体企业的证照号码、订单号或联系方式，不能用规则代替真实业务记录。'
 if reject:return dict(question=original,status='abstained',answer=reject,evidence=[],graph=e.graph([]),trace=[dict(id=c,rerank=round(scores[c],4),dense=round(dense[c],4)) for c in ranked[:3]],entities=entities,mode='semantic',latency_ms=round((time.monotonic()-start)*1000))
 for cid in subselected:
  if cid not in selected:selected.append(cid)
 verification={'enabled':bool(e.evidence_guard)}
 if e.evidence_guard:
  emit('verify','本地模型核对问题、主体与原文证据')
  # The verifier sees complete clauses and their scope, including weaker candidates;
  # it may remove topical matches but cannot invent a source or answer text.
  pool=list(dict.fromkeys(selected+[c for c in ranked[:8] if scores[c]>=MIN_RELEVANCE]))
  from scopes import excluded_reason
  excluded={c:excluded_reason(c,original) for c in pool if excluded_reason(c,original)}
  if needs_records:
   excluded.update({c:'实际业务问题须引用订单、批次或质量记录，公开参考资料不能作为已发生事实的证据' for c in pool if not e.chunks[c]['doc_id'].startswith('U')})
  pool=[c for c in pool if c not in excluded]
  verification['scope_excluded']=excluded
  try:
   inputs=[dict(id=c,text=e.chunks[c]['text'],context=e.chunks[c]['context'],source=e.docs[e.chunks[c]['doc_id']]['title']) for c in pool]
   if subquestions:
    verified=[]
    for sub in subquestions:
     matches=e.evidence_guard.select(original,inputs,sub['question'].removesuffix('有什么要求？'))
     sub['supported']=bool(matches);sub['verified_evidence']=matches
     verified.extend(matches)
    verified=list(dict.fromkeys(verified))
   else:verified=e.evidence_guard.select(original,inputs)
   verification.update(model='Qwen3-4B-Instruct-2507-4bit',considered=pool,selected=verified,rejected=[c for c in pool if c not in verified])
   selected=[c for c in pool if c in verified]
  except Exception as ex:
   selected=[];verification['error']=str(ex)
  if not selected:
   return dict(question=original,status='abstained',answer='候选条款未通过问题相关性与适用对象复核，没有足够证据作答。'+(' 本地复核模型暂时不可用，请查看检索轨迹。' if 'error' in verification else ''),evidence=[],graph=e.graph([]),trace=[],entities=entities,verification=verification,mode='semantic',latency_ms=round((time.monotonic()-start)*1000))
 roles={c:'direct' for c in selected}
 for cid in list(selected):
  for context in CONTEXT.get(cid,[]):
   if context in dense and context not in selected:selected.append(context);roles[context]='scope_context';via.setdefault(context,{'from':cid,'kind':'适用条件依赖','depth':1})
 evidence=[dict(e.evidence(cid),citation='E'+str(i+1),role=roles[cid]) for i,cid in enumerate(selected)]
 claims=[]
 for item in evidence:
  # Claim text is source text verbatim, never free model generation.
  claims.append(dict(text=item['text'],citation=item['citation'],chunk_id=item['id'],role=item['role'],context=item.get('context',[])))
 paths=[dict(target=c,**via[c]) for c in selected if c in via]
 response_trace=[]
 for cid in selected:
  row=dict(id=cid,**trace.get(cid,{}),dense=round(dense[cid],4),rerank=round(scores[cid],4) if cid in scores else None,fusion=round(fused.get(cid,0),5),role=roles[cid])
  if cid in via:row['via']=via[cid]
  response_trace.append(row)
 stages={
  'candidates':[dict(id=c,source=e.docs[e.chunks[c]['doc_id']]['title'],text=e.chunks[c]['text'],bm25=trace.get(c,{}).get('bm25'),dense=round(dense[c],4),fusion=round(fused[c],5)) for c in candidates],
  'expansion':[dict(id=c,text=e.chunks[c]['text'],selected=c in selected,**via[c]) for c in via],
  'final':[dict(id=c,citation=evidence[i]['citation'],role=roles[c],rerank=round(scores[c],4) if c in scores else None) for i,c in enumerate(selected)]}
 return dict(question=original,status='partial' if any(not x['supported'] for x in subquestions) else 'evidence_found',answer=('部分子问题没有充分证据。' if any(not x['supported'] for x in subquestions) else '')+'以下为能支持本次问题的原文回答；适用条件单独列出。每项回答均可回到来源核对。',claims=claims,evidence=evidence,graph=e.graph(selected),paths=paths,subquestions=subquestions,trace=response_trace,stages=stages,verification=verification,entities=entities,mode='semantic',latency_ms=round((time.monotonic()-start)*1000))
