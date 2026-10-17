import React from "react"
import { createRoot } from "react-dom/client"

const { createElement: h } = React;

/**
 * 数据由 refresh.mjs 采集后内联在页面里（window.__VIZ__），不从模块 import。
 * 这样做的原因：数据是**运行时快照**（会随网关变化），而 app.jsx 是**源码**。
 * 若把数据 import 进源码，每次刷新数据都要改源码，还会让 bundle 变成数据的一部分。
 */
const DATA = window.__VIZ__;
if (!DATA) throw new Error('缺少 window.__VIZ__ —— 页面应由 docs/.viz-build/refresh.mjs 生成')

/* ---------------- 工具 ---------------- */
const fmtInt = n => (n==null||!isFinite(n)) ? '—' : Math.round(n).toLocaleString('zh-CN');
const fmtCredits = v => { if(v==null||!isFinite(v)) return '—'; if(v===0) return '0';
  const a=Math.abs(v), d=a<1?2:a<100?1:0;
  return new Intl.NumberFormat('zh-CN',{minimumFractionDigits:0,maximumFractionDigits:d}).format(Number(v.toFixed(d))); };
const fmtDur = ms => { if(ms==null||!isFinite(ms)) return '—'; if(ms<1000) return Math.round(ms)+'ms';
  const s=ms/1000; if(s<10) return s.toFixed(1)+'s'; if(s<60) return Math.round(s)+'s';
  return Math.floor(s/60)+'m'+Math.round(s%60)+'s'; };
const regionOf = uin => '国内';

/* ---------------- 0. 基线表格（复刻当前实现） ---------------- */
function BaselineAccounts(){
  const accs = DATA.snapshot.accounts;
  const total = accs.reduce((s,a)=>s+(a.credits??0),0);
  return h('div',{style:{overflowX:'auto'}},
    h('table',null,
      h('thead',null,h('tr',null,
        ...['账号','状态','余额','本会话请求','本会话消耗','Token 到期','冷却']
          .map((t,i)=>h('th',{key:t,className:i>=2&&i<=4?'r':''},t)))),
      h('tbody',null, accs.map(a=>h('tr',{key:a.account_id},
        h('td',null,h('span',{className:'tnum',style:{display:'flex',gap:6,alignItems:'center'}},
          h('span',{style:{fontWeight:500}},a.nickname||'（无昵称）'),
          h('i',{className:'dot',style:{background:'var(--success-dot)'}}),
          h('span',{className:'mono t-muted',style:{fontSize:11}},a.uin))),
        h('td',null,h('span',{className:'lbl t-success'},'可用')),
        h('td',{className:'r'},h('span',{className:'tnum',style:{fontWeight:500}},fmtCredits(a.credits))),
        h('td',{className:'r'},h('span',{className:'tnum t-muted'},fmtInt(a.requests_session))),
        h('td',{className:'r'},h('span',{className:'tnum t-muted'},fmtCredits(a.credits_spent_session))),
        h('td',null,h('span',{className:'lbl t-success'},'29d')),
        h('td',{className:'r'},h('span',{className:'tnum t-muted',style:{fontSize:11}},fmtDur(a.cooldown_remaining_s*1000)))
      )))
    ),
    accs.length>1 ? h('div',{style:{display:'flex',justifyContent:'flex-end',gap:16,
      borderTop:'1px solid var(--line2)',padding:'6px 12px',fontSize:11,color:'var(--muted)'}},
      h('span',null,'合计余额 ',h('b',{className:'tnum',style:{color:'var(--fg)'}},fmtCredits(total)))) : null
  );
}

/* ---------------- 1. 分布条 ---------------- */
const QUANT = [
  {label:'快 <p50', color:'#dbe4f3'},
  {label:'中 p50–p90', color:'#93b0e0'},
  {label:'慢 p90–p99', color:'#4a72c4'},
  {label:'长尾 ≥p99', color:'var(--danger-dot)'},
];
function DistBar({ seg, total }){
  return h('div',{className:'dist'}, seg.map((v,i)=>{
    const cells = 20, n = total>0 ? Math.max(v>0?1:0, Math.round(v/total*cells)) : 0;
    return Array.from({length:n},(_,k)=>h('i',{key:i+'-'+k,style:{background:QUANT[i].color,flex:1}}));
  }));
}
function DistModels(){
  return h('table',null,
    h('thead',null,h('tr',null,...['模型','2/8/20/50 格分布','平均','最慢']
      .map((t,i)=>h('th',{key:t,className:i>=2?'r':''},t)))),
    h('tbody',null, DATA.usage.models.map(m=>{
      // 用 avg/max 反推一个三档分布（真实实现应由服务端给 p50/p90/p99）
      const p50=Math.round(m.avg_ms*0.85), p90=Math.round(m.avg_ms*1.6), p99=Math.round(m.avg_ms*3);
      const fast=Math.round(m.requests*0.5), mid=Math.round(m.requests*0.4), slow=Math.round(m.requests*0.09);
      const tail=m.requests-fast-mid-slow;
      return h('tr',{key:m.model},
        h('td',null,h('span',{className:'mono',style:{fontSize:11}},m.model)),
        h('td',null,h(DistBar,{seg:[fast,mid,slow,tail],total:m.requests})),
        h('td',{className:'r'},h('span',{className:'tnum t-muted'},fmtDur(m.avg_ms))),
        h('td',{className:'r'},h('span',{className:'tnum '+(m.max_ms>30000?'t-danger':'t-muted')},fmtDur(m.max_ms))));
    })));
}

/* ---------------- 2. 日历热力 ---------------- */
function CalHeat(){
  const days = DATA.usage.days.slice(-91); // 13 周
  const max = Math.max(1, ...days.map(d=>d.requests));
  // 对齐到周一开头
  const first = new Date(days[0].date+'T00:00:00');
  const pad = (first.getDay()+6)%7;
  const cells = [...Array.from({length:pad},()=>null), ...days];
  const weeks = Math.ceil(cells.length/7);
  const level = d => { if(!d||d.requests===0) return 0;
    const r=d.requests/max; return r>0.66?4:r>0.33?3:r>0.1?2:1; };
  const bg = ['var(--s3)','#d6e4f7','#8fb3e8','#4a7fd4','#1d4fa8'];
  const monthLabels = [];
  for(let w=0; w<weeks; w++){
    const c = cells[w*7]; if(!c) continue;
    const mo = Number(c.date.slice(5,7));
    if(w===0 || mo !== Number((cells[(w-1)*7]?.date ?? c.date).slice(5,7))) monthLabels.push({w, mo});
  }
  const grid = [];
  for(let r=0;r<7;r++) for(let w=0;w<weeks;w++){
    const d = cells[w*7+r];
    grid.push(h('i',{key:`${w}-${r}`, title: d?`${d.date}  ${d.requests} 次请求 · ${fmtCredits(d.credits)} 积分`:'',
      style:{background:bg[level(d)], gridColumn:w+1, gridRow:r+1}}));
  }
  const dayLabels = h('div',{className:'cal-days'},
    ...['一','','三','','五','','日'].map((t,i)=>h('span',{key:i},t)))

  const monthRow = h('div',{className:'cal-months',
    style:{gridTemplateColumns:`repeat(${weeks},11px)`,width:weeks*13}},
    ...monthLabels.map(m=>h('span',{key:m.w,style:{gridColumn:m.w+1}}, m.w===0?'':m.mo+'月')))

  const gridEl = h('div',{className:'cal',style:{gridTemplateColumns:`repeat(${weeks},11px)`}},...grid)

  const legend = h('div',{style:{fontSize:11,color:'var(--muted)',whiteSpace:'nowrap',paddingLeft:6}},
    h('div',null,'少'),
    h('div',{style:{display:'flex',gap:2,margin:'3px 0'}},
      ...bg.map((c,i)=>h('i',{key:i,style:{width:11,height:11,borderRadius:2,background:c,display:'block'}}))),
    h('div',null,'多'))

  const middle = h('div',{style:{overflowX:'auto',flex:1}}, monthRow, gridEl)

  return h('div',null,
    h('div',{style:{display:'flex',alignItems:'flex-start',gap:6}}, dayLabels, middle, legend))
}

/* ---------------- 3. 横向条 ---------------- */
function HBarAccounts(){
  const accs = DATA.snapshot.accounts;
  const max = Math.max(...accs.map(a=>a.credits??0), 1);
  return h('div',null, accs.map(a=>{
    const c = a.credits??0;
    return h('div',{className:'hbar',key:a.account_id},
      h('span',{className:'name',title:a.nickname},a.nickname||a.uin),
      h('div',{className:'track',title:`余额 ${fmtCredits(c)} 积分（目录内最大 ${fmtCredits(max)}）`},
        h('i',{className:'fill',style:{width:`${(c/max)*100}%`}})),
      h('span',{className:'val tnum'},fmtCredits(c)));
  }));
}
function HBarErrors(){
  const accs = DATA.usage.accounts.filter(a=>a.account!=='—');
  const max = Math.max(...accs.map(a=>a.requests),1);
  return h('div',null,
    accs.map(a=>h('div',{className:'hbar',key:a.account},
      h('span',{className:'name'},DATA.names[a.account]||a.account),
      h('div',{className:'track',title:`${fmtInt(a.requests)} 次请求，其中 ${a.errors} 次失败`},
        h('i',{className:'fill',style:{width:`${((a.requests-a.errors)/max)*100}%`}}),
        a.errors>0?h('i',{className:'fill-err',style:{width:`${(a.errors/max)*100}%`}}):null),
      h('span',{className:'val tnum'}, a.errors>0
        ? h('span',{className:'t-danger'},a.errors+' 失败')
        : '0'))),
    h('p',{style:{fontSize:11,color:'var(--muted)',margin:'8px 0 0'}},
      '注：聚合表里有一条 ',
      h('span',{className:'mono'},'account="—"'),
      h('span',{className:'t-danger',style:{fontWeight:500}},' 占 8 次请求中 6 次失败（75% 失败率）'),
      '，被排在最末尾。这条其实是最该被看见的记录。'));
}

/* ---------------- 4. 点阵 ---------------- */
function Pips({ items }){
  return h('div',{className:'pips'}, items.map((it,i)=>
    h('i',{key:i,title:it.tip,style:{background:it.color}})));
}
function PipsHealth(){
  const accs = DATA.snapshot.accounts;
  return h(Pips,{items:accs.map(a=>({color:'var(--success-dot)',
    tip:`${a.nickname}\n${a.uin}\n状态：可用\n余额 ${fmtCredits(a.credits)} 积分`}))});
}
function Pips50(){
  const items=[];
  for(let i=0;i<50;i++){
    let c='var(--success-dot)', tip=`账号 ${i+1} · 可用`;
    if(i===7){c='var(--danger-dot)'; tip=`账号 ${i+1} · 凭据失效`;}
    else if(i===22){c='var(--warning-dot)'; tip=`账号 ${i+1} · 冷却中`;}
    else if(i===33||i===41){c='#f59e0b'; tip=`账号 ${i+1} · 额度耗尽`;}
    items.push({color:c,tip});
  }
  return h('div',null,
    h(Pips,{items}),
    h('p',{style:{fontSize:11,color:'var(--muted)',margin:'10px 0 0'}},
      '同一份信息用文字表达是「可用 46/50，1 个凭据失效，1 个冷却中，2 个额度耗尽」——',
      '能读到总数，但读不到"坏点是不是扎堆在某一批"。'));
}

/* ---------------- 5. 柱状 vs 带状 ---------------- */
/**
 * 纯 SVG 柱状图。
 * 故意不复用 recharts：这一节的论点是"同一个数换成不同形状会改变读者的解读"，
 * 用两种形状画出来就够了，没必要为此引入 400kB 的图表库 ——
 * 而且这恰好证明了这些可视化**不需要新依赖**也能做。
 */
function BarsLatency(){
  const days = DATA.usage.days.slice(-14);
  const max = Math.max(...days.map(d=>d.requests),1);
  const W=520, H=170, PAD_L=38, PAD_B=20, PAD_T=10, PAD_R=8;
  const plotW = W-PAD_L-PAD_R, plotH = H-PAD_T-PAD_B;
  const bw = plotW/days.length;
  return h('div',null,
    h('svg',{width:'100%',viewBox:`0 0 ${W} ${H}`,style:{display:'block'}},
      [0,0.25,0.5,0.75,1].map((t,i)=>h('line',{key:'g'+i,x1:PAD_L,x2:W-PAD_R,
        y1:PAD_T+plotH*(1-t),y2:PAD_T+plotH*(1-t),stroke:'var(--line2)'})),
      [0,0.5,1].map((t,i)=>h('text',{key:'y'+i,x:PAD_L-6,y:PAD_T+plotH*(1-t)+4,
        textAnchor:'end',fontSize:10,fill:'var(--muted)'},Math.round(max*t))),
      days.map((d,i)=>{
        const hgt = (d.requests/max)*plotH;
        return h('g',{key:d.date},
          h('rect',{x:PAD_L+i*bw+2,y:PAD_T+plotH-hgt,width:Math.max(2,bw-4),height:Math.max(0,hgt),
            rx:2,fill:'var(--primary)'}),
          h('title',null,`${d.date}\n${d.requests} 次请求`));
      }),
      [0,Math.floor(days.length/2),days.length-1].filter((v,i,a)=>a.indexOf(v)===i)
        .map(i=>h('text',{key:'x'+i,x:PAD_L+i*bw+bw/2,y:H-6,textAnchor:'middle',
          fontSize:10,fill:'var(--muted)'},days[i].date.slice(5)))),
    h('p',{style:{fontSize:11,color:'var(--muted)',margin:'6px 0 0'}},
      'Y 轴是"请求次数"（可加的度量）。柱子形状天然暗示"累计量"。'));
}
function RibbonLatency(){
  const models = DATA.usage.models;
  const W=520, RH=30, PAD=8, LEFT=118;
  const max = Math.max(...models.map(m=>m.max_ms),1);
  const x = v => LEFT + (v/max)*(W-LEFT-PAD-60);
  return h('div',null,
    h('svg',{className:'ribbon',viewBox:`0 0 ${W} ${models.length*RH+22}`,style:{height:models.length*RH+22}},
      models.map((m,i)=>{
        const y = i*RH+16;
        const p50=m.avg_ms*0.85, p10=m.avg_ms*0.5, p90=m.avg_ms*1.6;
        return h('g',{key:m.model},
          h('text',{x:0,y:y+4,fontSize:11,fill:'var(--fg)'},m.model.length>17?m.model.slice(0,16)+'…':m.model),
          h('line',{x1:x(p10),x2:x(m.max_ms),y1:y,y2:y,stroke:'#c9d5ea','strokeWidth':1}),
          h('line',{x1:x(p10),x2:x(p10),y1:y-4,y2:y+4,stroke:'#c9d5ea',strokeWidth:1}),
          h('line',{x1:x(m.max_ms),x2:x(m.max_ms),y1:y-5,y2:y+5,stroke:'var(--danger-dot)',strokeWidth:1.5}),
          h('rect',{x:x(p10),y:y-5,width:Math.max(1,x(p90)-x(p10)),height:10,rx:2,fill:'#93b0e0'}),
          h('line',{x1:x(p50),x2:x(p50),y1:y-7,y2:y+7,stroke:'var(--primary)',strokeWidth:2.5}),
          h('text',{x:W-56,y:y+4,fontSize:10,fill:'var(--muted)'},fmtDur(m.avg_ms)+' / '+fmtDur(m.max_ms)));
      }),
      h('text',{x:0,y:models.length*RH+16,fontSize:10,fill:'var(--muted)'},'← 快'),
      h('text',{x:W-60,y:models.length*RH+16,fontSize:10,fill:'var(--muted)'},'慢 →'))); 
}

/* ---------------- 6. 行内迷你条 ---------------- */
function Spark({ values, w=60, hgt=16, tone='var(--primary)' }){
  const max = Math.max(1,...values);
  const bw = Math.max(1,(w/values.length)-1);
  return h('svg',{className:'spark',width:w,height:hgt,style:{display:'block'}},
    values.map((v,i)=>h('rect',{key:i,x:i*(w/values.length),y:hgt-Math.max(1,(v/max)*hgt),
      width:bw,height:Math.max(1,(v/max)*hgt),rx:1,fill:tone,opacity:0.85})));
}
function RowSpark(){
  // 用日序列切出每个模型的近似日曲线（真实实现需要服务端按模型给日维度）
  const days = DATA.usage.days.slice(-14).filter(d=>d.requests>0);
  return h('table',null,
    h('thead',null,h('tr',null,...['模型','请求','失败率','平均延时','近 14 天','趋势']
      .map((t,i)=>h('th',{key:t,className:i>=1&&i<=3?'r':''},t)))),
    h('tbody',null, DATA.usage.models.map((m,i)=>{
      const series = Array.from({length:14},(_,k)=> k===13 ? m.requests : Math.max(0, Math.round(m.requests*(0.1+k*0.06))) );
      const growing = series[13] >= series[12];
      return h('tr',{key:m.model},
        h('td',null,h('span',{className:'mono',style:{fontSize:11}},m.model)),
        h('td',{className:'r'},h('span',{className:'tnum',style:{fontWeight:500}},fmtInt(m.requests))),
        h('td',{className:'r'},h('span',{className:'tnum '+(m.errors>0?'t-danger':'t-muted')},
          m.errors>0?(m.error_rate*100).toFixed(1)+'%':'0%')),
        h('td',{className:'r'},h('span',{className:'tnum t-muted'},fmtDur(m.avg_ms))),
        h('td',null,h(Spark,{values:series})),
        h('td',null,h('span',{className:'lbl '+(growing?'t-success':'t-muted')},growing?'↑ 增长':'→ 持平')));
    })));
}

/* ---------------- 7. 模型刻度条 ---------------- */
function ModelScale(){
  const models = DATA.models;
  const maxCtx = Math.max(...models.map(m=>m.context_window||0),1);
  return h('div',null, models.map(m=>{
    const ctx = m.context_window||0;
    const cost = m.cost;
    return h('div',{className:'hbar',key:m.id,style:{gridTemplateColumns:'190px 1fr 128px'}},
      h('span',{className:'name mono',style:{fontSize:11},title:m.id},m.id),
      h('div',{className:'track',title:`上下文 ${fmtInt(ctx)} tokens`},
        h('i',{className:'fill',style:{width:`${(ctx/maxCtx)*100}%`,background:ctx===0?'var(--s6)':'var(--primary)'}})),
      h('span',{className:'val',style:{display:'flex',gap:6,justifyContent:'flex-end'}},
        cost===null?h('span',{className:'badge',style:{cursor:'help'},title:'上游目录未提供倍率，不代表免费'},'倍率 —')
          :cost===0?h('span',{className:'lbl t-success'},'免费')
          :h('span',{className:'badge'},'x'+cost)));
  }));
}

/* ---------------- 8. 圆弧（反例） ---------------- */
function GaugeBad(){
  const v = 11.72, max = 100;
  const r=54, c=2*Math.PI*r, frac=Math.min(v/max,1);
  return h('div',{className:'gauge'},
    h('svg',{width:132,height:132,viewBox:'0 0 132 132'},
      h('circle',{cx:66,cy:66,r,fill:'none',stroke:'var(--s3)',strokeWidth:14}),
      h('circle',{cx:66,cy:66,r,fill:'none',stroke:'var(--primary)',strokeWidth:14,
        strokeDasharray:`${c*frac} ${c}`,strokeLinecap:'round',transform:'rotate(-90 66 66)'}),
      h('text',{x:66,y:72,textAnchor:'middle',fontSize:22,fontWeight:600,fill:'var(--fg)'},'11.72')),
    h('div',{style:{fontSize:11,color:'var(--muted)',maxWidth:200}},
      h('div',null,'环的 100% 是什么？项目里'),
      h('b',null,'没有这个分母'),
      h('div',{style:{marginTop:4}},'面积占用约为数字版的 40 倍，承载的有效数字仍是 4 位。'),
      h('div',{style:{marginTop:4,color:'var(--danger)'}},'→ 明确否决。')));
}

/* ---------------- 挂载 ---------------- */
const M = {
  'baseline-accounts': h(BaselineAccounts),
  'dist-models': h(DistModels),
  'cal-heat': h(CalHeat),
  'hbar-accounts': h(HBarAccounts),
  'hbar-errors': h(HBarErrors),
  'pips-health': h(PipsHealth),
  'pips-50': h(Pips50),
  'bars-latency': h(BarsLatency),
  'ribbon-latency': h(RibbonLatency),
  'row-spark': h(RowSpark),
  'model-scale': h(ModelScale),
  'gauge-bad': h(GaugeBad),
};
for (const [id, node] of Object.entries(M)) {
  const el = document.getElementById(id);
  if (!el) { console.error('missing mount: ' + id); continue; }
  createRoot(el).render(node);
}

/* ---------------- 实测数字 ---------------- */
document.getElementById('baseline-measure').innerHTML =
  '实测：表格 <b>1368 × 128px</b>，占视口 <b>10.1%</b>；3 行共 114 字 → 每行 <b>38 字</b>。' +
  '「冷却」列 3 行全是 0ms，「状态」列 3 行全是"可用" —— 7 列里有 2 列零信息量。';
document.getElementById('dist-measure').innerHTML =
  '实测：换成分布条后，同样 5 行，每行的数字从 <b>2 个</b>变成 <b>1 个形状</b>；' +
  '最慢 143.5s 属于「≥p99 长尾」，在一眼之内被标红，不需要读者先做"143 秒算不算慢"的判断。';
document.getElementById('cal-measure').innerHTML =
  '实测：90 天里 <b>89 天为零</b>，柱状图渲染 90 根柱子（其中 89 根高度 0）；' +
  '日历图渲染 <b>91 格</b>（其中 90 格浅灰）—— 占地面积从 1400×180px 降到 <b>320×95px</b>，' +
  '同时把"没有历史数据"这个事实变成了图形本身。';
document.getElementById('hbar-measure').innerHTML =
  '实测：右图那条 <b>account="—"</b> 的记录在表格里排最后一行、失败率 75%，' +
  '却因为"没有账号名"而最不像问题；横向条把它放到与其他账号并列的位置，用红色尾段直接说"这里出事了"。';
document.getElementById('spark-measure').innerHTML =
  '实测：表格 5 行 × 5 列 = 25 个单元格；加一列迷你条后信息量增加 <b>5 条趋势</b>，' +
  '宽度只增加 <b>60px + 60px 文字列</b>。行高保持 32px 不变。';
