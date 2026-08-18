"""
Build a self-contained HTML exploration dashboard from viz_data_page{N}.json.

Embeds the data directly in the page (no fetch/server needed) — open
analysis/dashboard.html straight in a browser.

Usage:
    python analysis/export_for_viz.py   # produces the JSON first
    python analysis/build_dashboard.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
DATA_PATH = HERE / "viz_data_page28.json"
OUT_PATH = HERE / "dashboard.html"

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Belief-System — data exploration</title>
<style>
  :root{
    --bg:#fcfcfb; --panel:#fff; --border:#e3e1dc; --text:#1f1d1a; --text-dim:#6b6862;
    --accent:#2b5797;
    --p:#FF99CC; --p-dk:#c44e83; --b:#9EE8E8; --b-dk:#3d9c9c; --g:#8FD98F; --g-dk:#3f9142;
    --y:#F5E050; --y-dk:#a68a0a; --o:#F5A85C; --o-dk:#b56a1f; --u:#CC99FF; --u-dk:#7c3fbf;
    --none:#c7c3ba; --none-dk:#8a867d;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
  header{padding:18px 24px;border-bottom:1px solid var(--border);background:var(--panel)}
  header h1{margin:0 0 4px;font-size:18px;font-weight:600}
  header p{margin:0;color:var(--text-dim);font-size:12px}
  main{padding:20px 24px;display:flex;flex-direction:column;gap:20px;max-width:1200px;margin:0 auto}
  .stats-row{display:flex;gap:12px;flex-wrap:wrap}
  .stat{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:10px 16px;min-width:120px}
  .stat .v{font-size:20px;font-weight:600;color:var(--accent)}
  .stat .l{font-size:11px;color:var(--text-dim);margin-top:2px}
  .panel{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:16px}
  .panel h2{margin:0 0 3px;font-size:13px;font-weight:600}
  .panel .sub{margin:0 0 14px;font-size:11px;color:var(--text-dim)}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  svg text{font-family:inherit}
  .bar{stroke-width:1.5px}
  .bar-lbl{font-size:10px;fill:var(--text-dim)}
  .axis-lbl{font-size:10px;fill:var(--text-dim)}
  .tick{stroke:var(--border);stroke-width:1px}
  #tm-crumb{font-size:11px;color:var(--text-dim);margin-bottom:8px;display:flex;gap:4px;flex-wrap:wrap;align-items:center}
  #tm-crumb span{cursor:pointer;color:var(--accent)}
  #tm-crumb span:hover{text-decoration:underline}
  #tm-crumb .sep{color:var(--text-dim);cursor:default;text-decoration:none}
  #tm-controls{margin-left:auto;display:flex;gap:8px;align-items:center;font-size:11px;color:var(--text-dim)}
  #tm-controls input{width:160px}
  #treemap{width:100%;height:75vh;min-height:560px;position:relative;border:1px solid var(--border);border-radius:4px;overflow:hidden}
  .tile{position:absolute;overflow:hidden;cursor:pointer;border:1px solid rgba(255,255,255,0.6);box-sizing:border-box;transition:filter .1s}
  .tile:hover{filter:brightness(0.94)}
  .tile.other-tile{background:repeating-linear-gradient(135deg,var(--panel),var(--panel) 6px,#ececea 6px,#ececea 12px)!important}
  .tile .cap{position:absolute;top:3px;left:5px;right:5px;font-size:11px;line-height:1.25;color:#2a2a2a;font-weight:600;text-shadow:0 1px 1px rgba(255,255,255,0.55);pointer-events:none;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
  .tile .sz{position:absolute;bottom:3px;right:5px;font-size:9px;color:#4a4a4a;pointer-events:none;background:rgba(255,255,255,0.55);padding:0 3px;border-radius:2px}
  .legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:var(--text-dim)}
  .legend .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px;border:1px solid rgba(0,0,0,0.15)}
  table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--border)}
  th{color:var(--text-dim);font-weight:600;font-size:11px}
  tr.row-click{cursor:pointer}
  tr.row-click:hover{background:#f7f6f3}
  #tm-other-list{margin-top:10px;max-height:300px;overflow:auto;display:none;border-top:1px solid var(--border);padding-top:10px}
</style>
</head>
<body>
<header>
  <h1>Belief-System — chunking &amp; structure exploration</h1>
  <p id="hdr-sub"></p>
</header>
<main>
  <div class="stats-row" id="stats"></div>

  <div class="row2">
    <div class="panel">
      <h2>Knowledge-unit size distribution</h2>
      <p class="sub">Character length of each retrievable fragment — the unit embeddings are built from</p>
      <svg id="hist" width="100%" height="220" viewBox="0 0 560 220"></svg>
    </div>
    <div class="panel">
      <h2>Fragments by highlight color</h2>
      <p class="sub">Count per color, with average retrieval weight and average length</p>
      <svg id="colorbar" width="100%" height="220" viewBox="0 0 560 220"></svg>
      <div class="legend" id="color-legend"></div>
    </div>
  </div>

  <div class="panel">
    <h2>Tree structure (treemap)</h2>
    <p class="sub">Sized by character count (text only — embedded images don't inflate this), colored by each note's dominant highlight color. Click a tile to drill in; a level with many small children is capped and the rest grouped into a hatched "+N other" tile — click it for a plain list.</p>
    <div id="tm-crumb">
      <span id="tm-crumb-path"></span>
      <div id="tm-controls">
        <input type="text" id="tm-search" placeholder="Find a note by caption…" />
        <span id="tm-search-result"></span>
      </div>
    </div>
    <div id="treemap"></div>
    <div id="tm-other-list"></div>
    <div class="legend">
      <span><span class="sw" style="background:var(--p)"></span>pink</span>
      <span><span class="sw" style="background:var(--b)"></span>blue</span>
      <span><span class="sw" style="background:var(--g)"></span>green</span>
      <span><span class="sw" style="background:var(--y)"></span>yellow</span>
      <span><span class="sw" style="background:var(--o)"></span>orange</span>
      <span><span class="sw" style="background:var(--u)"></span>purple</span>
      <span><span class="sw" style="background:var(--none)"></span>unmarked</span>
      <span><span class="sw other-tile" style="background:repeating-linear-gradient(135deg,#fff,#fff 3px,#ccc 3px,#ccc 6px)"></span>grouped "other"</span>
    </div>
  </div>

  <div class="panel">
    <h2>Top 30 notes by salience</h2>
    <p class="sub">Salience = weighted combination of highlight colors (purple/pink highest, orange lowest). Click a row to jump the treemap to it.</p>
    <table id="salience-table"></table>
  </div>
</main>

<script>
const DATA = __DATA_JSON__;
const COLOR_HEX = {p:'var(--p)',b:'var(--b)',g:'var(--g)',y:'var(--y)',o:'var(--o)',u:'var(--u)',none:'var(--none)',g2:'var(--g)'};
const COLOR_DK  = {p:'var(--p-dk)',b:'var(--b-dk)',g:'var(--g-dk)',y:'var(--y-dk)',o:'var(--o-dk)',u:'var(--u-dk)',none:'var(--none-dk)',g2:'var(--g-dk)'};
const COLOR_NAME= {p:'pink',b:'blue',g:'green',y:'yellow',o:'orange',u:'purple',none:'unmarked',g2:'dark green'};

// ── Header + stat tiles ──────────────────────────────
document.getElementById('hdr-sub').textContent =
  `${DATA.page.caption} — ${DATA.page.note_count.toLocaleString()} notes, ${DATA.chunks.total_fragments.toLocaleString()} retrievable fragments`;

const stats = [
  ['Fragments', DATA.chunks.total_fragments.toLocaleString()],
  ['Median length', DATA.chunks.p50 + ' chars'],
  ['Mean length', DATA.chunks.mean + ' chars'],
  ['90th pct length', DATA.chunks.p90 + ' chars'],
  ['Max length', DATA.chunks.max.toLocaleString() + ' chars'],
  ['Highlight ratio', (DATA.page.highlight_ratio*100).toFixed(1) + '%'],
];
document.getElementById('stats').innerHTML = stats.map(([l,v])=>
  `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

// ── Histogram (single series → single hue, per dataviz marks spec) ──
(function(){
  const svg=document.getElementById('hist');
  const W=560,H=220,ML=36,MB=34,MT=10,MR=10;
  const data=DATA.chunks.histogram;
  const max=Math.max(...data.map(d=>d.count));
  const bw=(W-ML-MR)/data.length;
  let s=`<g>`;
  // gridlines
  for(let i=0;i<=4;i++){
    const y=MT+(H-MT-MB)*(1-i/4);
    s+=`<line class="tick" x1="${ML}" x2="${W-MR}" y1="${y}" y2="${y}"/>`;
    s+=`<text class="axis-lbl" x="${ML-6}" y="${y+3}" text-anchor="end">${Math.round(max*i/4)}</text>`;
  }
  data.forEach((d,i)=>{
    const h=(H-MT-MB)*(d.count/max);
    const x=ML+i*bw+3, y=H-MB-h, w=bw-6;
    s+=`<rect class="bar" x="${x}" y="${y}" width="${w}" height="${h}" rx="3" fill="var(--accent)" fill-opacity="0.75" stroke="var(--accent)"/>`;
    s+=`<text class="bar-lbl" x="${x+w/2}" y="${H-MB+14}" text-anchor="middle">${d.range}</text>`;
  });
  s+=`</g>`;
  svg.innerHTML=s;
})();

// ── Color bar chart (categorical, domain-authentic hues + labels) ───
(function(){
  const svg=document.getElementById('colorbar');
  const W=560,H=220,ML=36,MB=34,MT=10,MR=10;
  const data=DATA.chunks.by_color;
  const max=Math.max(...data.map(d=>d.count));
  const bw=(W-ML-MR)/data.length;
  let s=`<g>`;
  for(let i=0;i<=4;i++){
    const y=MT+(H-MT-MB)*(1-i/4);
    s+=`<line class="tick" x1="${ML}" x2="${W-MR}" y1="${y}" y2="${y}"/>`;
    s+=`<text class="axis-lbl" x="${ML-6}" y="${y+3}" text-anchor="end">${Math.round(max*i/4)}</text>`;
  }
  data.forEach((d,i)=>{
    const h=(H-MT-MB)*(d.count/max);
    const x=ML+i*bw+4, y=H-MB-h, w=bw-8;
    const fill=COLOR_HEX[d.color]||'var(--none)', stroke=COLOR_DK[d.color]||'var(--none-dk)';
    s+=`<rect class="bar" x="${x}" y="${y}" width="${w}" height="${Math.max(h,1)}" rx="3" fill="${fill}" stroke="${stroke}"/>`;
    s+=`<text class="bar-lbl" x="${x+w/2}" y="${H-MB+14}" text-anchor="middle">${COLOR_NAME[d.color]||d.color}</text>`;
    s+=`<text class="bar-lbl" x="${x+w/2}" y="${y-4}" text-anchor="middle" font-weight="600" fill="var(--text)">${d.count.toLocaleString()}</text>`;
  });
  s+=`</g>`;
  svg.innerHTML=s;
  document.getElementById('color-legend').innerHTML = data.map(d=>
    `<span><span class="sw" style="background:${COLOR_HEX[d.color]}"></span>${COLOR_NAME[d.color]||d.color}: avg weight ${d.avg_weight}, avg len ${d.avg_len}</span>`
  ).join('');
})();

// ── Treemap (squarified, drill-down) ─────────────────
// Classic Bruls/Huizing/van Wijk squarified layout, recursive form.
// items must already be sorted descending by area before calling.
function squarify(nodes, x, y, w, h){
  const total = nodes.reduce((s,n)=>s+n._size,0) || 1;
  const items = nodes.map(n=>({n, area: (n._size/total)*w*h})).filter(i=>i.area>0);
  const rects=[];

  function worstRatio(row, sideLen){
    if(!row.length) return Infinity;
    const sum = row.reduce((a,b)=>a+b.area,0);
    const maxA = Math.max(...row.map(r=>r.area));
    const minA = Math.min(...row.map(r=>r.area));
    return Math.max((sideLen*sideLen*maxA)/(sum*sum), (sum*sum)/(sideLen*sideLen*minA));
  }

  // Lays out `row` to fill either a full-height column (wide container) or
  // full-width band (tall container); returns the remaining rect.
  function layoutRow(row, x, y, w, h){
    const sum = row.reduce((a,b)=>a+b.area,0);
    if(w >= h){
      const colW = sum / h;
      let cy = y;
      row.forEach(r=>{
        const itemH = r.area / colW;
        rects.push({n:r.n, x, y:cy, w:colW, h:itemH});
        cy += itemH;
      });
      return {x: x+colW, y, w: Math.max(w-colW,0), h};
    } else {
      const rowH = sum / w;
      let cx = x;
      row.forEach(r=>{
        const itemW = r.area / rowH;
        rects.push({n:r.n, x:cx, y, w:itemW, h:rowH});
        cx += itemW;
      });
      return {x, y: y+rowH, w, h: Math.max(h-rowH,0)};
    }
  }

  function recurse(items, x, y, w, h){
    if(!items.length || w<=0 || h<=0) return;
    if(items.length===1){ rects.push({n:items[0].n, x, y, w, h}); return; }
    const sideLen = Math.min(w,h);
    let i=1;
    while(i<items.length && worstRatio(items.slice(0,i),sideLen) >= worstRatio(items.slice(0,i+1),sideLen)) i++;
    const row=items.slice(0,i), rest=items.slice(i);
    const remaining = layoutRow(row, x, y, w, h);
    recurse(rest, remaining.x, remaining.y, remaining.w, remaining.h);
  }

  recurse(items, x, y, w, h);
  return rects;
}

let tmStack=[];
const MAX_TILES=28; // cap per level so tiles stay readable; rest bucket into "other"
function tmRoot(){ return {id:-1, caption:DATA.page.caption, children:DATA.tree, _size: DATA.page.total_chars}; }
function findNode(id, node){
  if(node.id===id) return node;
  for(const c of (node.children||[])){ const f=findNode(id,c); if(f) return f; }
  return null;
}
function findByCaption(node, needle, out){
  if(node.caption && node.caption.toLowerCase().includes(needle)) out.push(node);
  for(const c of (node.children||[])) findByCaption(c, needle, out);
  return out;
}
function pathTo(id, node, trail){
  if(node.id===id) return trail;
  for(const c of (node.children||[])){
    const r=pathTo(id, c, [...trail, c]);
    if(r) return r;
  }
  return null;
}

function renderTreemap(node){
  const el=document.getElementById('treemap');
  document.getElementById('tm-other-list').style.display='none';
  const W=el.clientWidth||900, H=el.clientHeight||600;
  let kids=(node.children||[]).filter(c=>!c.is_separator);
  kids.forEach(k=>k._size = Math.max(k.st_chars||k.chars||0, 1));
  kids.sort((a,b)=>b._size-a._size);

  let other=null;
  if(kids.length>MAX_TILES){
    other = kids.slice(MAX_TILES-1);
    const otherSize = other.reduce((s,k)=>s+k._size,0);
    kids = kids.slice(0,MAX_TILES-1);
    kids.push({id:'other', caption:`+${other.length} other note${other.length>1?'s':''}`,
               _size: Math.max(otherSize,1), _isOther:true, _others:other});
  }

  const rects = squarify(kids, 0, 0, W, H);
  el.innerHTML = rects.map(r=>{
    const c=r.n;
    if(c._isOther){
      return `<div class="tile other-tile" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px"
        onclick="tmShowOther()" title="${c.caption} — ${c._size.toLocaleString()} chars combined">
        ${r.w<40||r.h<20?'':`<div class="cap">${c.caption}</div><div class="sz">${c._size.toLocaleString()}</div>`}
      </div>`;
    }
    const color = COLOR_HEX[c.dominant_color] || 'var(--none)';
    const tooSmall = r.w<26 || r.h<18;
    return `<div class="tile" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;background:${color}"
      onclick="tmDrill(${c.id})" title="${(c.caption||'(untitled)').replace(/"/g,'&quot;')} — ${(c.st_chars||c.chars||0).toLocaleString()} chars">
      ${tooSmall?'':`<div class="cap">${(c.caption||'(untitled)').slice(0,80)}</div><div class="sz">${(c.st_chars||c.chars||0).toLocaleString()}</div>`}
    </div>`;
  }).join('');
  window._tmOther = other;
  renderCrumb();
}
function tmShowOther(){
  const box=document.getElementById('tm-other-list');
  const items=(window._tmOther||[]).sort((a,b)=>b._size-a._size);
  box.style.display='block';
  box.innerHTML = `<table><tr><th>Caption</th><th>Chars</th><th>Salience</th></tr>` +
    items.map(c=>`<tr class="row-click" onclick="tmDrillFromOther(${c.id})">
      <td>${(c.caption||'(untitled)').slice(0,90)}</td>
      <td>${(c.st_chars||c.chars||0).toLocaleString()}</td>
      <td>${c.st_salience||c.salience||0}</td></tr>`).join('') + `</table>`;
}
function tmDrillFromOther(id){ tmDrill(id); }
function tmDrill(id){
  const cur = tmStack.length ? tmStack[tmStack.length-1] : tmRoot();
  const found = findNode(id, cur);
  if(found && found.children && found.children.length){
    tmStack.push(found);
    renderTreemap(found);
  }
}
function tmGoto(i){
  tmStack = tmStack.slice(0,i+1);
  renderTreemap(tmStack.length?tmStack[tmStack.length-1]:tmRoot());
}
function tmGotoNode(target){
  const trail = pathTo(target.id, tmRoot(), []);
  if(!trail) return;
  tmStack = trail.slice(0,-1); // stop one level up so the target tile is visible
  renderTreemap(tmStack.length?tmStack[tmStack.length-1]:tmRoot());
}
function renderCrumb(){
  const crumb=document.getElementById('tm-crumb-path');
  const parts=[{caption:DATA.page.caption}, ...tmStack];
  crumb.innerHTML = parts.map((p,i)=>
    `<span onclick="tmGoto(${i-1})">${(p.caption||'(root)').slice(0,40)}</span>` + (i<parts.length-1?'<span class="sep"> / </span>':'')
  ).join('');
}
renderTreemap(tmRoot());
window.addEventListener('resize', ()=>renderTreemap(tmStack.length?tmStack[tmStack.length-1]:tmRoot()));

// ── Search ────────────────────────────────────────
document.getElementById('tm-search').addEventListener('keydown', e=>{
  if(e.key!=='Enter') return;
  const q=e.target.value.trim().toLowerCase();
  const res=document.getElementById('tm-search-result');
  if(!q){res.textContent='';return}
  const hits=findByCaption(tmRoot(), q, []);
  if(!hits.length){res.textContent='No match';return}
  res.textContent=`${hits.length} match${hits.length>1?'es':''} — showing first`;
  tmGotoNode(hits[0]);
});

// ── Top-30 by salience (walked from leaf-level notes, not subtree agg) ──
(function(){
  const all=[];
  (function walk(n){
    if(!n.is_separator && !n.is_marker && (n.chars||0)>0) all.push(n);
    (n.children||[]).forEach(walk);
  })(tmRoot());
  const top = all.sort((a,b)=>(b.salience||0)-(a.salience||0)).slice(0,30);
  const tbl=document.getElementById('salience-table');
  tbl.innerHTML = `<tr><th>Caption</th><th>Salience</th><th>Highlight%</th><th>Color</th><th>Chars</th></tr>` +
    top.map(n=>`<tr class="row-click" onclick='tmGotoNode({id:${n.id}})'>
      <td>${(n.caption||'(untitled)').slice(0,90)}</td>
      <td>${n.salience}</td>
      <td>${(n.hl_ratio*100).toFixed(0)}%</td>
      <td><span class="sw" style="background:${COLOR_HEX[n.dominant_color]||'var(--none)'}"></span>${COLOR_NAME[n.dominant_color]||'—'}</td>
      <td>${(n.chars||0).toLocaleString()}</td>
    </tr>`).join('');
})();
</script>
</body>
</html>
"""


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    html = TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Built {OUT_PATH} ({OUT_PATH.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
