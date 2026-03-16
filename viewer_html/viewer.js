
'use strict';

const LBL_COLOR_LIGHT = 'rgba(255,255,255,.65)';
const LBL_COLOR_DARK  = 'rgba(0,0,0,.85)';

const CATS = {
  chest_legendary: {label:'Legendary Chest', color:'#FFD700', group:'Chests',    r:7, ring:true,  pri:10},
  chest_hoard:     {label:'Hoard Chest',     color:'#FF8C00', group:'Chests',    r:7, ring:true,  pri:9},
  chest_rare:      {label:'Rare Chest',      color:'#C060FF', group:'Chests',    r:6, ring:true,  pri:8},
  chest_uncommon:  {label:'Uncommon Chest',  color:'#00CC44', group:'Chests',    r:6, ring:false, pri:7},
  chest_common:    {label:'Common Chest',    color:'#AAAAAA', group:'Chests',    r:5, ring:false, pri:5},
  resource:        {label:'Resource / Ore',  color:'#00CED1', group:'Resources', r:6, ring:true,  pri:8},
  shrine:          {label:'Shrine',          color:'#FF69B4', group:'Shrines',   r:6, ring:true,  pri:8},
  exit:            {label:'Exit',            color:'#00FF88', group:'Exits',     r:7, ring:true,  pri:9},
  sub_boss:        {label:'Boss Spawn',      color:'#FF3333', group:'Bosses',    r:8, ring:true,  pri:10},
  loot_valuable:   {label:'Valuable Loot',   color:'#FFD060', group:'Loot',      r:5, ring:false, pri:6},
  loot_equipment:  {label:'Equipment',       color:'#50C850', group:'Loot',      r:5, ring:false, pri:5},
  loot_trinket:    {label:'Trinket',         color:'#A050E0', group:'Loot',      r:4, ring:false, pri:4},
  loot_consumable: {label:'Consumable',      color:'#80C040', group:'Loot',      r:4, ring:false, pri:3},
  loot_ground:     {label:'Ground Loot',     color:'#607080', group:'Loot',      r:3, ring:false, pri:2},
  trap:            {label:'Trap',            color:'#FF6030', group:'Hazards',   r:4, ring:false, pri:4},
  hazard_zone:     {label:'Hazard Zone',     color:'#FF2020', group:'Hazards',   r:6, ring:true,  pri:6},
  gate:            {label:'Gate',            color:'#C09850', group:'Interact',  r:4, ring:false, pri:3},
  lever:           {label:'Lever',           color:'#D0A060', group:'Interact',  r:3, ring:false, pri:3},
  door:            {label:'Door',            color:'#A07050', group:'Interact',  r:3, ring:false, pri:2},
  monster:         {label:'Monster Spawn',   color:'#884422', group:'Monsters',  r:4, ring:false, pri:1},
};
const GROUPS = ['Chests','Exits','Bosses','Resources','Shrines','Loot','Hazards','Interact','Monsters'];

const S = {
  map:null, mode:'N', modules:{},
  visible: new Set(Object.keys(CATS).filter(k=>CATS[k].group!=='Monsters')),
  showLbls:true, showMks:true,
  focusMarkers:true, markerScale:1.0, focusKey:null,
  zoom:1, panX:0, panY:0,
  drag:false, dx:0, dy:0, px:0, py:0,
};
const TILE = 200;
let _dragMoved = false;

const vp   = id('vp'), mc = id('mc'), tt = id('tt');
const sbar = id('sbar'), msel = id('msel'), nd = id('nd'), zlbl = id('zlbl');
const fArea= id('f-area');

function id(x){ return document.getElementById(x); }

async function init(){
  setS('Connecting…');
  let maps;
  try { maps = await fj('/api/maps'); }
  catch(e){ setS('Cannot reach server. Is map_viewer.py running?'); return; }

  msel.innerHTML='';
  let first=null;
  for(const [n,inf] of Object.entries(maps)){
    const o=document.createElement('option');
    o.value=n;
    o.textContent=`${inf.has_json?'✓':'✗'}  ${n}  (${inf.module_count})`;
    if(!inf.has_json) o.style.color='#555';
    msel.appendChild(o);
    if(inf.has_json&&!first) first=n;
  }
  buildFilters();
  bindAll();
  if(first){ msel.value=first; await loadMap(first); }
  else{ nd.style.display='block'; setS('No map data. Run dad_downloader.py first.'); }
}

async function fj(url){
  const r=await fetch(url);
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

async function loadMap(name){
  if(!name) return;
  setS('Loading '+name+'…');
  nd.style.display='none';
  S.map=name;
  let data;
  try{ data=await fj(`/api/mapdata?map=${encodeURIComponent(name)}&mode=${S.mode}`); }
  catch(e){ setS('Error: '+e.message); return; }
  if(data.error){ setS('Error: '+data.error); nd.style.display='block'; return; }
  S.modules=data.modules;
  render(); fit(); refreshCounts(); populateModList();
  setS(`${name}  •  ${Object.keys(S.modules).length} modules  •  ${S.mode==='N'?'Normal':'High Roller'}`);
}

function render(){
  mc.innerHTML='';
  if(!Object.keys(S.modules).length) return;
  let maxC=0, maxR=0;
  for(const m of Object.values(S.modules)){
    maxC=Math.max(maxC, m.col+m.span);
    maxR=Math.max(maxR, m.row+m.span);
  }
  mc.style.width =(maxC*TILE)+'px';
  mc.style.height=(maxR*TILE)+'px';

  for(const [key,mod] of Object.entries(S.modules)){
    const W=mod.span*TILE, H=mod.span*TILE;
    const wrap=document.createElement('div');
    wrap.className='tw';
    wrap.style.cssText=`left:${mod.col*TILE}px;top:${mod.row*TILE}px;width:${W}px;height:${H}px;`;

    if(mod.has_png){
      const img=document.createElement('img');
      img.className='ti'; img.draggable=false;
      img.src=`/tile/${encodeURIComponent(S.map)}/${encodeURIComponent(key)}.png`;
      wrap.appendChild(img);
    } else {
      const ph=document.createElement('div');
      ph.className='tp';
      ph.innerHTML=`<span>${esc(mod.label||key)}</span>`;
      wrap.appendChild(ph);
    }

    if(S.showLbls){
      const l=document.createElement('div');
      l.className='tl'; l.textContent=mod.label||key;
      wrap.appendChild(l);
    }

    if(S.showMks) wrap.appendChild(mkOverlay(mod,W,H,key,S.markerScale));
    wrap.addEventListener('click',e=>{
      if(e.target.closest('.mk')||_dragMoved) return;
      openFocus(key);
    });
    mc.appendChild(wrap);
  }
  applyX();
}

const NS='http://www.w3.org/2000/svg';
function mkOverlay(mod,W,H,key,scale=1.0){
  const svg=document.createElementNS(NS,'svg');
  svg.setAttribute('width',W); svg.setAttribute('height',H);
  svg.setAttribute('overflow','visible'); svg.classList.add('ov');
  const bb=mod.bbox;
  const xr=bb.xmax-bb.xmin, yr=bb.ymax-bb.ymin;

  for(const item of (mod.items||[])){
    const cfg=CATS[item.cat];
    if(!cfg||!S.visible.has(item.cat)) continue;
    const px=((item.x-bb.xmin)/xr)*W;
    const py=((bb.ymax-item.y)/yr)*H;   // flip Y
    const r=cfg.r*scale;

    const g=document.createElementNS(NS,'g');
    g.classList.add('mk');
    g.setAttribute('transform',`translate(${px.toFixed(1)},${py.toFixed(1)})`);

    if(cfg.ring){
      const ring=document.createElementNS(NS,'circle');
      ring.setAttribute('r',(r+4*scale).toFixed(1)); ring.setAttribute('fill','none');
      ring.setAttribute('stroke',cfg.color); ring.setAttribute('stroke-width',(1.2*scale).toFixed(1));
      ring.setAttribute('stroke-opacity','0.4');
      g.appendChild(ring);
    }
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('r',r.toFixed(1)); c.setAttribute('fill',cfg.color);
    c.setAttribute('fill-opacity','0.92');
    c.setAttribute('stroke','#000'); c.setAttribute('stroke-width',Math.max(0.5,scale).toFixed(1));
    g.appendChild(c);

    g.addEventListener('mouseenter', e=>showTT(e,item,cfg));
    g.addEventListener('mouseleave', hideTT);
    svg.appendChild(g);
  }
  return svg;
}

function showTT(e,item,cfg){
  const name=item.name||item.id||'?';
  tt.innerHTML=`<div class="tn">${esc(name)}</div><div class="tc">${esc(cfg.label)}</div><div class="tp2">x:${Math.round(item.x)}  y:${Math.round(item.y)}</div>`;
  tt.style.display='block'; moveTT(e);
}
function hideTT(){ tt.style.display='none'; }
function moveTT(e){
  const mx=e.clientX+14, my=e.clientY+12;
  tt.style.left=(mx+tt.offsetWidth >window.innerWidth ?mx-tt.offsetWidth -22:mx)+'px';
  tt.style.top =(my+tt.offsetHeight>window.innerHeight?my-tt.offsetHeight-20:my)+'px';
}
document.addEventListener('mousemove',e=>{ if(tt.style.display!=='none') moveTT(e); });
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function buildFilters(){
  fArea.innerHTML='';
  for(const gn of GROUPS){
    const cats=Object.entries(CATS).filter(([,c])=>c.group===gn).sort((a,b)=>b[1].pri-a[1].pri);
    if(!cats.length) continue;
    const div=document.createElement('div'); div.className='fg';
    const hd=document.createElement('div'); hd.className='fg-hd open';
    hd.innerHTML=`<span class="arr">▶</span><span style="flex:1">${gn}</span><button class="fg-all" data-g="${gn}">all</button>`;
    hd.addEventListener('click',e=>{ if(e.target.classList.contains('fg-all')){ toggleGroup(gn); return; } hd.classList.toggle('open'); });
    div.appendChild(hd);
    const rows=document.createElement('div'); rows.className='fg-rows';
    for(const [cat,cfg] of cats){
      const r=document.createElement('div'); r.className='fi'; r.id='fi_'+cat; r.dataset.cat=cat;
      r.innerHTML=`<div class="dot" style="background:${cfg.color};color:${cfg.color}"></div><span class="fn">${esc(cfg.label)}</span><span class="fc" id="fc_${cat}">0</span>`;
      r.addEventListener('click',()=>toggleCat(cat));
      rows.appendChild(r);
    }
    div.appendChild(rows); fArea.appendChild(div);
  }
  syncUI();
}

function toggleCat(cat){ S.visible.has(cat)?S.visible.delete(cat):S.visible.add(cat); syncUI(); rebuildOvs(); }
function toggleGroup(gn){
  const cats=Object.entries(CATS).filter(([,c])=>c.group===gn).map(([k])=>k);
  const allOn=cats.every(c=>S.visible.has(c));
  cats.forEach(c=>allOn?S.visible.delete(c):S.visible.add(c));
  syncUI(); rebuildOvs();
}
function syncUI(){ for(const cat of Object.keys(CATS)){ const el=id('fi_'+cat); if(el) el.classList.toggle('off',!S.visible.has(cat)); } }
function refreshCounts(){
  const cnt={};
  for(const m of Object.values(S.modules)) for(const item of (m.items||[])) cnt[item.cat]=(cnt[item.cat]||0)+1;
  for(const [cat,n] of Object.entries(cnt)){ const el=id('fc_'+cat); if(el) el.textContent=n; }
}
function rebuildOvs(){
  if(!S.showMks) return;
  const tiles=mc.querySelectorAll('.tw'), keys=Object.keys(S.modules);
  tiles.forEach((wrap,i)=>{
    const key=keys[i]; if(!key) return;
    const mod=S.modules[key];
    const W=mod.span*TILE, H=mod.span*TILE;
    const old=wrap.querySelector('.ov');
    const svg=mkOverlay(mod,W,H,key,S.markerScale);
    if(old) wrap.replaceChild(svg,old); else wrap.appendChild(svg);
  });
}

function populateModList(){
  const ml=id('mod-list'); if(!ml) return;
  ml.innerHTML='';
  for(const [key,mod] of Object.entries(S.modules)){
    const d=document.createElement('div'); d.className='ml-item';
    d.textContent=mod.label||key; d.title=key;
    d.addEventListener('click',()=>openFocus(key));
    ml.appendChild(d);
  }
}

function openFocus(key){
  const mod=S.modules[key]; if(!mod) return;
  S.focusKey=key;
  id('fm-title').textContent=mod.label||key;
  const span=mod.span||1;
  const FW=Math.min(500*span,580), FH=FW;
  const tw=document.createElement('div');
  tw.className='fm-tw';
  tw.style.width=FW+'px'; tw.style.height=FH+'px';
  if(mod.has_png){
    const img=document.createElement('img');
    img.src=`/tile/${encodeURIComponent(S.map)}/${encodeURIComponent(key)}.png`;
    img.style.cssText='position:absolute;inset:0;width:100%;height:100%';
    img.draggable=false;
    tw.appendChild(img);
  } else {
    const ph=document.createElement('div');
    ph.className='tp'; ph.style.cssText='position:absolute;inset:0';
    ph.innerHTML=`<span>${esc(mod.label||key)}</span>`;
    tw.appendChild(ph);
  }
  if(S.focusMarkers){
    const sc=S.markerScale*2.0;
    const svg=mkOverlay(mod,FW,FH,key,sc);
    svg.style.cssText='position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none';
    tw.appendChild(svg);
  }
  const body=id('fm-body'); body.innerHTML=''; body.appendChild(tw);
  const vis=(mod.items||[]).filter(i=>S.visible.has(i.cat)).length;
  const tot=(mod.items||[]).length;
  id('fm-info').textContent=`${key}  •  ${vis} markers shown  (${tot} total items)`;
  id('fm').style.display='flex';
}

function closeFocus(){ id('fm').style.display='none'; S.focusKey=null; }

function toggleSettings(){
  const sp=id('sp');
  sp.style.display=sp.style.display==='flex'?'none':'flex';
}

function applyX(){
  mc.style.transform=`translate(${S.panX}px,${S.panY}px) scale(${S.zoom})`;
  zlbl.textContent=Math.round(S.zoom*100)+'%';
}
function fit(){
  const vw=vp.clientWidth, vh=vp.clientHeight;
  const cw=parseInt(mc.style.width)||800, ch=parseInt(mc.style.height)||600;
  const s=Math.min((vw-32)/cw,(vh-32)/ch,1.5);
  S.zoom=Math.max(0.08,s);
  S.panX=(vw-cw*S.zoom)/2; S.panY=(vh-ch*S.zoom)/2;
  applyX();
}
function zoomAt(f,cx,cy){
  const nz=Math.max(0.05,Math.min(6,S.zoom*f));
  S.panX=cx-(cx-S.panX)*(nz/S.zoom);
  S.panY=cy-(cy-S.panY)*(nz/S.zoom);
  S.zoom=nz; applyX();
}

function bindAll(){
  msel.addEventListener('change',()=>loadMap(msel.value));
  document.querySelectorAll('.pill').forEach(b=>{
    b.addEventListener('click',()=>{
      document.querySelectorAll('.pill').forEach(x=>x.classList.remove('on'));
      b.classList.add('on'); S.mode=b.dataset.mode;
      if(S.map) loadMap(S.map);
    });
  });
  id('cb-lbl').addEventListener('change',e=>{ S.showLbls=e.target.checked; render(); });
  id('cb-mk') .addEventListener('change',e=>{ S.showMks =e.target.checked; render(); });
  id('btn-gear').addEventListener('click', toggleSettings);
  id('sp-cls').addEventListener('click',()=>{ id('sp').style.display='none'; });
  id('sld-ms').addEventListener('input',e=>{
    S.markerScale=parseFloat(e.target.value);
    id('sld-ms-val').textContent=S.markerScale.toFixed(1);
    render();
  });
  id('cb-mk-focus').addEventListener('change',e=>{ S.focusMarkers=e.target.checked; });
  id('cb-lbl-dark').addEventListener('change',e=>{
    document.documentElement.style.setProperty('--lbl-color', e.target.checked?LBL_COLOR_DARK:LBL_COLOR_LIGHT);
    render();
  });
  id('fm-cls').addEventListener('click', closeFocus);
  id('fm-bg').addEventListener('click', closeFocus);
  id('btn-tall').addEventListener('click',()=>{
    const allOn=Object.keys(CATS).every(c=>S.visible.has(c));
    allOn?S.visible.clear():Object.keys(CATS).forEach(c=>S.visible.add(c));
    syncUI(); rebuildOvs();
  });
  id('bzi').addEventListener('click',()=>zoomAt(1.25,vp.clientWidth/2,vp.clientHeight/2));
  id('bzo').addEventListener('click',()=>zoomAt(0.80,vp.clientWidth/2,vp.clientHeight/2));
  id('bft').addEventListener('click',fit);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){ closeFocus(); return; }
    if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)) return;
    if(e.key==='+'||e.key==='=') zoomAt(1.2, vp.clientWidth/2, vp.clientHeight/2);
    if(e.key==='-')              zoomAt(0.83,vp.clientWidth/2, vp.clientHeight/2);
    if(e.key==='f'||e.key==='F') fit();
  });
  vp.addEventListener('wheel',e=>{
    e.preventDefault();
    const rect=vp.getBoundingClientRect();
    zoomAt(e.deltaY<0?1.1:0.91, e.clientX-rect.left, e.clientY-rect.top);
  },{passive:false});
  vp.addEventListener('mousedown',e=>{
    if(e.target.closest('.mk')) return;
    _dragMoved=false;
    S.drag=true; S.dx=e.clientX; S.dy=e.clientY; S.px=S.panX; S.py=S.panY;
    vp.classList.add('gb');
  });
  window.addEventListener('mousemove',e=>{
    if(!S.drag) return;
    if(Math.abs(e.clientX-S.dx)+Math.abs(e.clientY-S.dy)>5) _dragMoved=true;
    S.panX=S.px+(e.clientX-S.dx); S.panY=S.py+(e.clientY-S.dy); applyX();
  });
  window.addEventListener('mouseup',()=>{ S.drag=false; vp.classList.remove('gb'); });
  let ltd=0,ltc=null;
  vp.addEventListener('touchstart',e=>{
    if(e.touches.length===1){ S.drag=true; S.dx=e.touches[0].clientX; S.dy=e.touches[0].clientY; S.px=S.panX; S.py=S.panY; }
    if(e.touches.length===2){ const a=e.touches[0],b=e.touches[1]; ltd=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY); ltc={x:(a.clientX+b.clientX)/2,y:(a.clientY+b.clientY)/2}; }
  },{passive:true});
  vp.addEventListener('touchmove',e=>{
    if(e.touches.length===1&&S.drag){ S.panX=S.px+(e.touches[0].clientX-S.dx); S.panY=S.py+(e.touches[0].clientY-S.dy); applyX(); }
    if(e.touches.length===2&&ltc){ const a=e.touches[0],b=e.touches[1]; const nd=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY); const cx=(a.clientX+b.clientX)/2,cy=(a.clientY+b.clientY)/2; zoomAt(nd/ltd,cx,cy); ltd=nd; }
  },{passive:true});
  vp.addEventListener('touchend',()=>{ S.drag=false; ltc=null; });
}
function setS(m){ sbar.textContent=m; }
init();
