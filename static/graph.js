/* Standalone SVG explorer. Solid edges are reviewed facts; dotted edges are provenance. */
class EvidenceGraph {
  constructor(root, onSelect) {
    this.root=root;this.onSelect=onSelect;this.mode='core';this.selected=null;this.positions=new Map();this.uid=root.id;
    root.innerHTML=`<div class="graph-toolbar"><div class="modes"><button data-mode="network">完整网络</button><button data-mode="core" class="active">核心路径</button><button data-mode="chain">关系链</button></div><button class="expand" aria-label="放大图谱">⤢ 放大</button></div><div class="graph-filters"><input placeholder="搜索节点或关系" aria-label="搜索图谱节点"><select class="type" aria-label="按实体类型筛选"><option value="">全部类型</option></select><select class="predicate" aria-label="按关系筛选"><option value="">全部关系</option></select></div><div class="graph-frame"><svg role="group" aria-label="可交互证据图谱"></svg><div class="graph-controls"><button data-zoom="0.8" aria-label="放大">＋</button><button data-zoom="1.25" aria-label="缩小">−</button><button class="fit" aria-label="适应画布">⊙</button></div><div class="minimap" aria-label="图谱缩略导航"><svg></svg></div></div><div class="graph-caption"><span class="counts"></span><span>拖动平移 · 滚轮缩放 · 点击关系定位</span></div><div class="graph-legend"></div>`;
    this.svg=root.querySelector('.graph-frame>svg');this.mini=root.querySelector('.minimap svg');this.frame=root.querySelector('.graph-frame');
    root.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{this.mode=b.dataset.mode;this.render()});
    root.querySelectorAll('.graph-filters input,.graph-filters select').forEach(i=>i.addEventListener(i.tagName==='INPUT'?'input':'change',()=>this.render()));
    root.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>this.zoom(Number(b.dataset.zoom)));
    root.querySelector('.fit').onclick=()=>{this.view=[...this.bounds];this.updateView()};
    root.querySelector('.expand').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen()}catch(e){this.root.querySelector('.counts').textContent='浏览器未开放全屏，可使用缩放按钮'}};
    this.svg.addEventListener('wheel',e=>{e.preventDefault();this.zoom(e.deltaY>0?1.12:.89)},{passive:false});
    this.svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;const node=e.target.closest('[data-node]');this.drag=node?node.dataset.node:null;this.pan={x:e.clientX,y:e.clientY,view:[...this.view]};this.moved=false;this.svg.setPointerCapture(e.pointerId)});
    this.svg.addEventListener('pointermove',e=>{if(!this.pan)return;const dx=e.clientX-this.pan.x,dy=e.clientY-this.pan.y;if(Math.hypot(dx,dy)>4)this.moved=true;if(this.drag){const pt=new DOMPoint(e.clientX,e.clientY).matrixTransform(this.svg.getScreenCTM().inverse());this.positions.set(this.drag,{x:pt.x,y:pt.y});this.draw()}else{const r=this.svg.getBoundingClientRect();this.view=[this.pan.view[0]-dx*this.pan.view[2]/r.width,this.pan.view[1]-dy*this.pan.view[3]/r.height,...this.pan.view.slice(2)];this.updateView()}});
    const end=()=>{this.pan=null;this.drag=null};this.svg.addEventListener('pointerup',end);this.svg.addEventListener('pointercancel',end);
    this.svg.addEventListener('click',e=>{if(this.moved){this.moved=false;return}const edge=e.target.closest('[data-edge]'),node=e.target.closest('[data-node]');if(edge){const x=this.edges[Number(edge.dataset.edge)];this.onSelect({chunk_id:x.chunk_id,edge:x})}else if(node){const x=this.nodes.find(n=>n.id===node.dataset.node);if(x?.chunk_ids?.length)this.onSelect({chunk_id:x.chunk_ids[0],node:x})}});
    this.svg.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const el=e.target.closest('[data-edge],[data-node]');if(el){e.preventDefault();el.dispatchEvent(new MouseEvent('click',{bubbles:true}))}}});
    this.mini.addEventListener('click',e=>{if(!this.bounds)return;const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(this.mini.getScreenCTM().inverse());this.view[0]=p.x-this.view[2]/2;this.view[1]=p.y-this.view[3]/2;this.updateView()});
  }
  esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  setData(network,core){this.network=network||{nodes:[],edges:[]};this.core=core||this.network;const all=[...this.network.nodes,...this.core.nodes];const types=[...new Set(all.map(n=>n.type))].sort();const relations=[...new Set([...this.network.edges,...this.core.edges].filter(e=>e.kind!=='provenance').map(e=>e.relation))].sort();for(const [selector,values,label] of [['.type',types,'全部类型'],['.predicate',relations,'全部关系']]){const el=this.root.querySelector(selector),v=el.value;el.innerHTML=`<option value="">${label}</option>`+values.map(x=>`<option>${this.esc(x)}</option>`).join('');if(values.includes(v))el.value=v}this.render()}
  highlight(chunk,edgeId){this.selected=chunk;this.selectedEdge=edgeId;this.draw()}
  render(){
    const data=this.mode==='network'?this.network:this.core;if(!data)return;
    this.root.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===this.mode);b.setAttribute('aria-pressed',b.dataset.mode===this.mode)});
    const q=this.root.querySelector('input').value.trim().toLowerCase(),type=this.root.querySelector('.type').value,rel=this.root.querySelector('.predicate').value,lookup=new Map(data.nodes.map(n=>[n.id,n]));
    this.edges=data.edges.filter(e=>lookup.has(e.source)&&lookup.has(e.target)&&(!q||[e.source,e.target,e.relation].some(x=>x.toLowerCase().includes(q)))&&(!type||[e.source,e.target].some(x=>lookup.get(x).type===type))&&(!rel||e.relation===rel));
    const ids=new Set(this.edges.flatMap(e=>[e.source,e.target]));
    this.nodes=data.nodes.filter(n=>ids.has(n.id)||(!rel&&(!q||n.label.toLowerCase().includes(q))&&(!type||n.type===type)&&!data.edges.some(e=>e.source===n.id||e.target===n.id)));
    this.types=[...new Set(this.nodes.map(n=>n.type))].sort();this.palette=['#71b8ed','#b197e9','#67c8b0','#dfb376','#dc8a9e','#8aa7df','#9fbd74','#a3bdcd'];
    this.root.querySelector('.graph-legend').innerHTML=this.types.map((t,i)=>`<span><i style="background:${this.palette[i%this.palette.length]}"></i>${this.esc(t)}</span>`).join('');
    this.root.querySelector('.counts').textContent=`${this.nodes.length} 个节点 · ${this.edges.filter(e=>e.kind!=='provenance').length} 条关系`;
    this.layout();this.draw();this.updateView();
  }
  layout(){
    const ns=this.nodes,edges=this.edges;this.positions=new Map();
    if(!ns.length){this.bounds=[0,0,1000,600];this.view=[...this.bounds];return}
    const seen=new Set(),components=[];
    for(const n of ns){if(seen.has(n.id))continue;const todo=[n.id],group=[];seen.add(n.id);while(todo.length){const id=todo.shift();group.push(id);for(const e of edges){const next=e.source===id?e.target:e.target===id?e.source:null;if(next&&!seen.has(next)){seen.add(next);todo.push(next)}}}components.push(group)}
    components.sort((a,b)=>b.length-a.length);
    let cursorY=0,cursorX=0,rowHeight=0;
    for(const group of components){
      const pos=new Map(),gEdges=edges.filter(e=>group.includes(e.source)&&group.includes(e.target));
      if(this.mode==='chain'){
        const incoming=new Set(gEdges.map(e=>e.target));const root=group.find(x=>!incoming.has(x))||group[0],level=new Map([[root,0]]),queue=[root];
        while(queue.length){const id=queue.shift();for(const e of gEdges){const n=e.source===id?e.target:e.target===id?e.source:null;if(n&&!level.has(n)){level.set(n,level.get(id)+1);queue.push(n)}}}
        const counts=new Map();for(const id of group){const l=level.get(id)||0,j=counts.get(l)||0;counts.set(l,j+1);pos.set(id,{x:l*240,y:j*110})}
      }else{
        group.forEach((id,i)=>{const a=i*2.39996,r=90*Math.sqrt(i+1);pos.set(id,{x:Math.cos(a)*r,y:Math.sin(a)*r})});
        for(let step=0;step<180;step++){
          const force=new Map(group.map(id=>[id,{x:0,y:0}]));
          for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++){const a=pos.get(group[i]),b=pos.get(group[j]),dx=a.x-b.x,dy=a.y-b.y,d=Math.max(35,Math.hypot(dx,dy)),f=15000/(d*d);force.get(group[i]).x+=dx/d*f;force.get(group[i]).y+=dy/d*f;force.get(group[j]).x-=dx/d*f;force.get(group[j]).y-=dy/d*f}
          for(const e of gEdges){const a=pos.get(e.source),b=pos.get(e.target),dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),f=(d-210)*.025;force.get(e.source).x+=dx/d*f;force.get(e.source).y+=dy/d*f;force.get(e.target).x-=dx/d*f;force.get(e.target).y-=dy/d*f}
          for(const id of group){const p=pos.get(id),f=force.get(id);p.x+=Math.max(-12,Math.min(12,f.x))*(1-step/220);p.y+=Math.max(-12,Math.min(12,f.y))*(1-step/220)}
        }
      }
      const xs=[...pos.values()].map(p=>p.x),ys=[...pos.values()].map(p=>p.y),minX=Math.min(...xs),minY=Math.min(...ys),gw=Math.max(...xs)-minX+230,gh=Math.max(...ys)-minY+160;if(cursorX>0&&cursorX+gw>1200){cursorY+=rowHeight;cursorX=0;rowHeight=0}for(const [id,p] of pos)this.positions.set(id,{x:p.x-minX+cursorX+110,y:p.y-minY+cursorY+70});cursorX+=gw;rowHeight=Math.max(rowHeight,gh);
    }
    const points=[...this.positions.values()];const width=Math.max(...points.map(p=>p.x))+120,height=Math.max(...points.map(p=>p.y))+80;
    this.bounds=[-20,-20,Math.max(600,width+40),Math.max(300,height+40)];this.view=[...this.bounds];
  }
  draw(){
    if(!this.nodes)return;const esc=x=>this.esc(x),positions=this.positions;
    this.frame.querySelector('.graph-empty')?.remove();
    if(!this.nodes.length){this.svg.innerHTML='';const empty=document.createElement('div');empty.className='graph-empty';empty.textContent='当前范围没有匹配的关系。可以调整筛选，或查看条款证据。';this.frame.append(empty);this.mini.innerHTML='';return}
    const marker=this.uid+'-arrow';
    let html=`<defs><marker id="${marker}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#b78f5d"/></marker></defs>`;
    html+=this.edges.map((e,i)=>{
      const a=positions.get(e.source),b=positions.get(e.target),dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy));
      const trim=Math.min(.28,70/d),x1=a.x+dx*trim,y1=a.y+dy*trim,x2=b.x-dx*trim,y2=b.y-dy*trim;
      const active=this.selectedEdge?e.edge_id===this.selectedEdge:e.chunk_id===this.selected,provenance=e.kind==='provenance',color=active?'#ffc06c':provenance?'#365a78':'#698baa';
      const similar=this.edges.filter(x=>x.source===e.source&&x.target===e.target),index=similar.indexOf(e),bend=similar.length>1?(index-(similar.length-1)/2)*40:15;
      const cx=(x1+x2)/2-dy/d*bend,cy=(y1+y2)/2+dx/d*bend,path=`M${x1} ${y1}Q${cx} ${cy} ${x2} ${y2}`;
      return `<g class="edge${active?' selected':''}" data-edge="${i}" tabindex="0" role="button" aria-label="${esc(e.source+' '+e.relation+' '+e.target)}"><title>${esc(e.source+' → '+e.relation+' → '+e.target+' · '+e.chunk_id)}</title><path d="${path}" stroke="transparent" stroke-width="18" fill="none"/><path class="edge-line" d="${path}" stroke="${color}" stroke-width="${active?3:1.6}" fill="none" ${provenance?'stroke-dasharray="5 5"':`marker-end="url(#${marker})"`}/><text x="${cx}" y="${cy-8}" text-anchor="middle" fill="${active?'#ffd496':'#88a2b8'}" font-size="10" stroke="#08131e" stroke-width="5" paint-order="stroke">${esc(e.relation)}</text></g>`;
    }).join('');
    html+=this.nodes.map(n=>{const p=positions.get(n.id),color=this.palette[this.types.indexOf(n.type)%this.palette.length],active=this.edges.some(e=>e.chunk_id===this.selected&&(e.source===n.id||e.target===n.id)),w=Math.min(176,Math.max(104,n.label.length*12+24)),label=n.label.length>13?n.label.slice(0,12)+'…':n.label;return `<g class="node" data-node="${esc(n.id)}" transform="translate(${p.x},${p.y})" tabindex="0" role="button" aria-label="${esc(n.label)}"><title>${esc(n.label+' · '+n.type)}</title><rect x="${-w/2-4}" y="-28" width="${w+8}" height="56" rx="15" fill="${active?'#8b59271f':'none'}" stroke="${active?'#b8793e55':'none'}" stroke-width="5"/><rect x="${-w/2}" y="-24" width="${w}" height="48" rx="12" fill="${active?'#382b1c':'#102333'}" stroke="${active?'#e2ab64':color}" stroke-opacity=".8"/><text text-anchor="middle" y="-2" fill="${active?'#ffe0ad':'#d7e8f4'}" font-size="11">${esc(label)}</text><text text-anchor="middle" y="14" fill="${color}" font-size="8">${esc(n.type)}</text></g>`}).join('');
    this.svg.innerHTML=html;
    const b=this.bounds;this.mini.setAttribute('viewBox',b.join(' '));this.mini.innerHTML=this.edges.map(e=>{const a=positions.get(e.source),b=positions.get(e.target);return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#4b7594" stroke-width="5"/>`}).join('')+this.nodes.map(n=>{const p=positions.get(n.id);return `<circle cx="${p.x}" cy="${p.y}" r="10" fill="${n.chunk_ids?.includes(this.selected)?'#e2ab64':'#78a3c2'}"/>`}).join('')+'<rect class="viewport" fill="#76b9ec22" stroke="#9ecded" stroke-width="6"/>';
    this.updateView();
  }
  updateView(){if(!this.view)return;this.svg.setAttribute('viewBox',this.view.join(' '));const r=this.mini.querySelector('.viewport');if(r){['x','y','width','height'].forEach((k,i)=>r.setAttribute(k,this.view[i]))}}
  zoom(factor){if(!this.view)return;const [x,y,w,h]=this.view,nw=Math.max(100,Math.min(this.bounds[2]*4,w*factor)),nh=nw*h/w;this.view=[x+(w-nw)/2,y+(h-nh)/2,nw,nh];this.updateView()}
}
