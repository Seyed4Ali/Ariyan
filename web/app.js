'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (value, digits=2) => value === null || value === undefined ? '—' : new Intl.NumberFormat('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(value);
const fa = value => new Intl.NumberFormat('fa-IR').format(value);
const money = value => value === null || value === undefined ? '—' : `${value < 0 ? '−' : ''}$${num(Math.abs(value))}`;
const date = ts => ts ? new Date(ts*1000).toLocaleString('fa-IR',{timeZone:'Asia/Tehran',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
const time = ts => ts ? new Date(ts*1000).toLocaleTimeString('en-GB',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
const titles = {overview:['دید کلی','هر معامله، با یک دلیل.','بازار را ببین. ریسک را محدود کن. از نتیجه یاد بگیر.'],agent:['ایجنت و یادگیری','از نتیجه یاد بگیر؛ با شواهد تغییر کن.','قانون تازه باید از آزمون داده‌های دیده‌نشده عبور کند.'],journal:['ژورنال معاملات','هیچ تصمیمی، بدون سابقه.','ورود، خروج، هزینه و دلیل هر تصمیم، در یک دفتر ماندگار.'],tools:['سشن و اخبار','زمان درست را هم ببین.','سشن‌های جهانی و تقویم اقتصادی، در کنار تصمیم‌های ربات.'],settings:['اتصال و تنظیمات','کنترل در دست توست.','اتصال محلی، اجرای دمو و محدودیت‌های روشن.']};
let state, token, selectedSymbol='EURUSD', selectedTimeframe='M1', fetching=false, toastTimer;
const priceDigits=()=>state?.markets[selectedSymbol]?.spec?.digits ?? (selectedSymbol==='EURUSD'?5:2);

function setText(id,value){ $(id).textContent=value; }
function signed(id,value,format=money){const el=$(id);el.textContent=format(value);el.classList.toggle('positive',value>0);el.classList.toggle('negative',value<0);}
function notify(text,error=false){$('toast').textContent=text;$('toast').className=error?'error':'';$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
function show(view){document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==view);document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));setText('view-name',titles[view][0]);setText('page-title',titles[view][1]);setText('page-subtitle',titles[view][2]);if(view==='tools')loadMarketTools();}

let toolsLoaded=false,toolsLoading=false;
async function loadMarketTools(force=false){
  if(toolsLoading||toolsLoaded&&!force)return;
  toolsLoading=true;
  try{
    const response=await fetch('/api/market-tools',{cache:'no-store'});
    if(!response.ok)throw new Error('ابزارهای بازار دریافت نشد؛ صفحه را تازه کن.');
    const config=await response.json();
    for(const widget of config.widgets){
      const url=new URL(widget.embed_url);
      if(url.origin!=='https://widget.financekit.org'||!['/embed/forex-sessions','/embed/economic-calendar'].includes(url.pathname))throw new Error('آدرس ابزار معتبر نیست.');
      const target=widget.key==='forex-sessions'?$('sessions-widget'):$('calendar-widget');
      const iframe=document.createElement('iframe');
      iframe.title=widget.title+' · به وقت تهران';iframe.src=url.href;
      iframe.loading='lazy';iframe.referrerPolicy='no-referrer';
      iframe.setAttribute('sandbox','allow-scripts allow-same-origin');
      iframe.setAttribute('allow','camera \'none\'; microphone \'none\'; geolocation \'none\'');
      target.replaceChildren(iframe);
    }
    toolsLoaded=true;
  }catch(error){notify(error.message,true);}
  finally{toolsLoading=false;}
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.view)));
document.querySelectorAll('[data-go]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.go)));
document.querySelectorAll('[data-tool]').forEach(el=>el.addEventListener('click',()=>{show('tools');$('tool-'+el.dataset.tool).scrollIntoView({behavior:'smooth',block:'start'});}));
$('reload-market-tools').addEventListener('click',()=>loadMarketTools(true));
document.querySelectorAll('[data-symbol]').forEach(el=>el.addEventListener('click',()=>{selectedSymbol=el.dataset.symbol;document.querySelectorAll('[data-symbol]').forEach(x=>x.classList.toggle('selected',x===el));if(state) renderMarket();}));
$('chart-timeframe').addEventListener('change',()=>{selectedTimeframe=$('chart-timeframe').value;if(state)renderMarket();});
$('timeframe-summary').addEventListener('click',event=>{const button=event.target.closest('[data-timeframe]');if(!button)return;selectedTimeframe=button.dataset.timeframe;$('chart-timeframe').value=selectedTimeframe;if(state)renderMarket();});

async function control(action,extra={}){
  try {
    if(!token) token=(await (await fetch('/api/session')).json()).token;
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Arian-Token':token},body:JSON.stringify({action,...extra})});
    const result=await response.json();
    if(!response.ok) throw new Error(result.error || 'فرمان اجرا نشد');
    notify(action==='flatten'?'ورود متوقف شد؛ خروج پوزیشن‌های ربات با اولین قیمت معتبر انجام می‌شود.':'تنظیم ذخیره شد.');
    await refresh();return true;
  } catch(error){notify(error.message,true);return false;}
}
$('pause-button').addEventListener('click',()=>control(state?.paused?'resume':'pause'));
$('flatten-button').addEventListener('click',()=>control('flatten'));
$('rollback-button').addEventListener('click',()=>control('rollback'));
$('demo-arm').addEventListener('click',async()=>{if(await control('demo_arm',{confirmation:$('demo-confirm').value.trim()})) $('demo-confirm').value='';});
$('demo-disarm').addEventListener('click',()=>control('demo_disarm'));
function openCalendar(){
  const until=state?.news.valid_until || (Date.now()/1000+8*3600);
  $('news-until').value=new Date(until*1000).toISOString().slice(0,16);
  $('news-events').value=(state?.news.events||[]).map(e=>`${new Date(e.time*1000).toISOString().slice(0,16)} | ${e.currency} | ${e.title}`).join('\n');
  $('news-reviewed').checked=false;$('calendar-dialog').showModal();
}
$('open-calendar').addEventListener('click',openCalendar);
document.querySelectorAll('[data-open-calendar]').forEach(el=>el.addEventListener('click',openCalendar));
$('close-calendar').addEventListener('click',()=>$('calendar-dialog').close());
$('calendar-form').addEventListener('submit',async event=>{
  event.preventDefault();
  try {
    const events=$('news-events').value.split('\n').map(x=>x.trim()).filter(Boolean).map(line=>{
      const [stamp,currency,...title]=line.split('|').map(x=>x.trim());
      const timestamp=Date.parse(stamp+'Z')/1000;
      if(!Number.isFinite(timestamp)||!['EUR','USD'].includes(currency)||!title.join('|')) throw new Error('قالب خبر: 2026-10-30T12:30 | USD | نام خبر');
      return {time:timestamp,currency,title:title.join('|')};
    });
    const valid_until=Date.parse($('news-until').value+'Z')/1000;
    if(await control('calendar',{reviewed:$('news-reviewed').checked,valid_until,events})) $('calendar-dialog').close();
  }catch(error){notify(error.message,true);}
});

function drawChart(bars,tick){
  if(!bars?.length){$('chart').innerHTML='<div class="chart-empty"><span>⌁</span><strong>منتظر اولین دادهٔ معتبر بازار</strong><p>متاتریدر را باز کن و به حساب بروکر وارد شو.<br>نمودار با قیمت‌های واقعی همان بروکر نمایش داده می‌شود.</p></div>';return;}
  const width=900,height=280,pad=18,right=85,plotWidth=width-right-pad;
  let low=Math.min(...bars.map(b=>b.low),tick?.bid??Infinity),high=Math.max(...bars.map(b=>b.high),tick?.bid??-Infinity);
  const span=Math.max(high-low,1e-8);low-=span*.08;high+=span*.08;
  const y=value=>pad+(height-2*pad)*(high-value)/(high-low);
  const dx=plotWidth/bars.length;
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="نمودار کندلی واقعی ${esc(selectedSymbol)} ${esc(selectedTimeframe)}">`;
  for(let i=0;i<5;i++){const price=high-(high-low)*i/4;const yy=y(price);svg+=`<line x1="0" y1="${yy}" x2="${plotWidth+pad}" y2="${yy}" stroke="#27333f" stroke-dasharray="2 5"/><text x="${width-5}" y="${yy+4}" fill="#75879a" font-size="11" font-family="Segoe UI" text-anchor="end">${num(price,priceDigits())}</text>`;}
  bars.forEach((bar,i)=>{const x=pad+i*dx+dx/2;const color=bar.close>=bar.open?'#62c5a2':'#d78383';svg+=`<line x1="${x}" x2="${x}" y1="${y(bar.high)}" y2="${y(bar.low)}" stroke="${color}"/><rect x="${x-dx*.3}" y="${Math.min(y(bar.open),y(bar.close))}" width="${Math.max(2,dx*.6)}" height="${Math.max(1.4,Math.abs(y(bar.open)-y(bar.close)))}" rx=".6" fill="${color}"/>`;});
  if(tick){const yy=y(tick.bid);svg+=`<line x1="0" y1="${yy}" x2="${plotWidth+pad}" y2="${yy}" stroke="#d7b676" stroke-dasharray="5 4"/><rect x="0" y="${yy-11}" width="100" height="22" rx="3" fill="#3a3020"/><text x="8" y="${yy+4}" fill="#e9c990" font-size="11" font-family="Segoe UI">${num(tick.bid,priceDigits())}</text>`;}
  svg+='</svg>';$('chart').innerHTML=svg;
}

function renderMarket(){
  const market=state.markets[selectedSymbol],tick=market?.tick,digits=priceDigits();
  const frame=market?.timeframes?.[selectedTimeframe];
  setText('chart-symbol',`${selectedSymbol} · ${market?.spec?.symbol||''} · ${selectedTimeframe}`);setText('chart-trend','روند: '+(frame?.trend||'—'));
  setText('bid',tick?num(tick.bid,digits):'—');setText('ask',tick?num(tick.ask,digits):'—');
  setText('spread',tick?num(tick.ask-tick.bid,digits):'—');
  setText('tick-age',tick?`${fa(Math.max(0,Math.round(state.now-tick.time)))} ثانیه قبل`:'—');
  let bars=[...(frame?.bars|| (selectedTimeframe==='M1'?market?.bars||[]:[]))];
  if(frame?.current){bars=bars.filter(b=>b.time<frame.current.time);bars.push(frame.current);}
  setText('chart-bar-label',frame?.current?'آخرین کندل در حال تشکیل است':'کندل‌های بسته‌شده');
  drawChart(bars,tick);
  $('timeframe-summary').innerHTML=(state.supported_timeframes||[]).map(tf=>{const f=market?.timeframes?.[tf];return `<button data-timeframe="${esc(tf)}" class="tf-card ${tf===selectedTimeframe?'selected':''}"><strong>${esc(tf)}</strong><span class="${f?.trend==='صعودی'?'positive':f?.trend==='نزولی'?'negative':''}">${esc(f?.trend||'—')}</span></button>`;}).join('');
  const checks=market?.checks?.length ? [...market.checks] : [
    {label:'قیمت و کندل تازه از بروکر',ok:false},{label:'روند تأییدشده در M5',ok:false},
    {label:'شکار نقدینگی و تأیید در M1',ok:false},{label:'اسپرد و فاصلهٔ حد ضرر مجاز',ok:false}];
  if(!checks.some(c=>c.id==='news')) checks.push({id:'news',label:'بررسی تقویم اخبار',ok:state.news.ok});
  if(!checks.some(c=>c.id==='pause')) checks.push({id:'pause',label:'اجازهٔ ورود فعال',ok:!state.paused});
  $('checklist').innerHTML=checks.map(c=>`<div class="check ${c.ok?'ok':''}"><span>${c.ok?'✓':'·'}</span><div>${esc(c.label)}</div></div>`).join('');
  setText('check-count',`${fa(checks.filter(c=>c.ok).length)} / ${fa(checks.length)}`);
  setText('decision',state.paused?'PAUSED':!state.connection.connected?'BLOCK':market?.decision||'WAIT');
  setText('decision-reason',!state.connection.connected?'اتصال به متاتریدر برقرار نیست؛ ورود مسدود است.':market?.reason||'در انتظار دریافت کندل‌های بروکر');
  setText('news-note',state.news.reason);
}

function tradeTable(trades,compact=false){
  if(!trades.length)return '<div class="empty"><span>▱</span>هنوز معامله‌ای ثبت نشده.<br>ورود فقط بعد از عبور از چک‌لیست انجام می‌شود.</div>';
  const statuses={OPEN:'باز',STOP:'حد ضرر',TARGET:'حد سود',TIME:'خروج زمانی',FLATTEN:'خروج دستی',BROKER_CLOSED:'خروج بروکر',REJECTED:'رد بروکر',SUBMITTING:'در حال ارسال',UNCERTAIN:'نیازمند بررسی'};
  return `<table><thead><tr><th>نماد / جهت</th><th>محیط</th><th>ورود</th><th>حد ضرر / هدف</th><th>حجم</th><th>${compact?'سود شناور':'نتیجه'}</th><th>وضعیت</th><th>زمان تهران</th></tr></thead><tbody>${[...trades].reverse().map(t=>`<tr><td><b>${esc(t.symbol)}</b> <span class="badge ${t.direction==='SELL'?'sell':''}">${esc(t.direction)}</span></td><td>${t.stream.startsWith('mt5-demo-')?'دموی بروکر':'شبیه‌سازی'}</td><td class="numeric">${num(t.entry,t.symbol==='EURUSD'?5:2)}</td><td class="numeric">${num(t.sl,t.symbol==='EURUSD'?5:2)} / ${num(t.tp,t.symbol==='EURUSD'?5:2)}</td><td class="numeric">${num(t.volume)}</td><td class="numeric ${(compact?t.floating:t.pnl)>0?'positive':(compact?t.floating:t.pnl)<0?'negative':''}">${money(compact?t.floating:t.pnl)}${!compact&&t.status!=='OPEN'?` · ${num(t.r)}R`:''}</td><td>${esc(statuses[t.status]||t.status)}</td><td>${esc(date(t.opened))}</td></tr>`).join('')}</tbody></table>`;
}

function render(){
  const a=state.account,l=state.learner,c=state.connection;
  setText('broker-account-name',c.connected?`${c.server} · ${c.account_login||'—'}`:'حساب متاتریدر متصل نیست');
  setText('broker-account-status',c.connected?`${c.demo?'حساب دمو':'حساب واقعی · فقط خواندن'} · ${c.currency} · ${c.trade_allowed?'Algo Trading روشن':'Algo Trading خاموش'}`:'برای خواندن موجودی و قیمت، متاتریدر را متصل نگه دار');
  setText('broker-balance',c.connected?money(c.balance):'—');setText('broker-equity',c.connected?money(c.equity):'—');setText('broker-free-margin',c.connected?money(c.margin_free):'—');
  setText('mode',state.mode==='DEMO'?'BROKER DEMO':'SHADOW MODE');
  setText('pause-button',state.paused?'ادامهٔ ورود':'توقف ورود');
  setText('equity',state.capital_binding?money(a.equity):'—');setText('balance-detail',state.capital_binding?`مبنای دمو: ${money(state.capital_binding.initial)} · بالانس شبیه‌سازی: ${money(a.balance)}`:'در انتظار هم‌اندازه‌سازی با موجودی حساب دمو');
  signed('daily-pnl',a.daily_pnl);setText('drawdown',`${num(a.drawdown_percent)}%`);setText('trade-count',fa(a.closed_count));
  setText('win-rate',a.win_rate===null?'نرخ برد پس از اولین خروج':`نرخ برد ${num(a.win_rate,1)}٪ · ${num(a.expectancy_r)}R میانگین`);
  $('feed-dot').classList.toggle('connected',!!c.connected);
  setText('feed-label',c.connected?'متاتریدر متصل است':c.error||'متاتریدر متصل نیست');
  setText('feed-detail',c.connected?`${c.server} · ${c.demo?'DEMO':'READ-ONLY LIVE'} · ${c.currency}`:'باز کردن متاتریدر و ورود به حساب');
  setText('execution-label',state.flatten_pending?'خروج در انتظار قیمت تازه':state.mode==='DEMO'?'ورود خودکار فقط روی دمو فعال است':'اجرای واقعی خاموش');
  const stale=state.updated&&Date.now()/1000-state.updated>35;
  $('api-error').hidden=!(state.error||stale);setText('api-error',state.error||'موتور بیش از ۳۵ ثانیه به‌روزرسانی نشده است؛ وضعیت اتصال را بررسی کن.');
  for(const symbol of ['EURUSD','XAUUSD','BTCUSD']){const tick=state.markets[symbol]?.tick;setText('quote-'+symbol,tick?num(tick.bid,state.markets[symbol]?.spec?.digits??2):'—');}
  setText('risk-used',`${num(a.daily_drawdown_percent)}%`);$('risk-progress').value=Math.min(1,a.daily_drawdown_percent);
  setText('day-trades',`${fa(a.trades_today)} / ۶`);setText('loss-streak',`${fa(a.consecutive_losses)} / ۳`);
  setText('agent-summary',l.message);setText('policy-version',`CHECKLIST v${l.version}`);setText('agent-version',`v${l.version}`);
  const progress=l.phase==='forward'?l.forward_count||0:l.collection_count||0,goal=l.phase==='forward'?50:100;
  setText('learning-count',`${fa(progress)} از ${fa(goal)} معامله${l.phase==='forward'?'ٔ آینده':''}`);$('learning-progress').max=goal;$('learning-progress').value=progress;
  setText('agent-phase',l.phase==='forward'?'آزمون آینده':'جمع‌آوری داده');setText('agent-message',l.message);
  const policy=l.active||l.candidate;
  setText('learned-rule',policy?`${l.active?'فیلتر فعال در شبیه‌سازی':'فرضیه در حال آزمایش'}: ورود در ${policy.rule.feature} = ${policy.rule.value} حذف شود. شناسه: ${policy.id}`:'هنوز قانون تازه‌ای تأیید نشده است. قواعد پایه فعال‌اند.');
  const rows=[['سرمایه',money(state.baseline.equity),money(a.equity)],['معاملات بسته',fa(state.baseline.closed_count),fa(a.closed_count)],['میانگین نتیجه',num(state.baseline.expectancy_r)+'R',num(a.expectancy_r)+'R'],['افت سرمایه',num(state.baseline.drawdown_percent)+'%',num(a.drawdown_percent)+'%']];
  $('comparison').innerHTML=rows.map(r=>`<tr><td>${esc(r[0])}</td><td class="numeric">${esc(r[1])}</td><td class="numeric">${esc(r[2])}</td></tr>`).join('');
  const all=[...state.trades,...state.demo_trades].sort((x,y)=>x.opened-y.opened);
  $('positions').innerHTML=`<div class="table-wrap">${tradeTable(all.filter(t=>['OPEN','SUBMITTING','UNCERTAIN'].includes(t.status)),true)}</div>`;
  $('trade-history').innerHTML=tradeTable(all);
  const eventNames={START:'شروع موتور',CAPITAL_SYNC:'هماهنگ‌سازی سرمایهٔ دمو',CONTROL:'کنترل',DECISION:'تصمیم',PAPER_OPEN:'ورود شبیه‌سازی',PAPER_CLOSE:'خروج شبیه‌سازی',DEMO_SUBMIT:'ارسال دمو',DEMO_CLOSE:'خروج دمو',DEMO_EXIT_REQUEST:'درخواست خروج دمو',NEWS_MANUAL_REVIEW:'بررسی اخبار',ENGINE_ERROR:'خطای موتور',POLICY_APPLIED_PAPER_ONLY:'چک‌لیست تازه',POLICY_ROLLBACK:'بازگشت چک‌لیست'};
  $('events').innerHTML=state.events.map(e=>`<div class="event"><time>${time(e.ts)}</time><div><b>${esc(eventNames[e.kind]||e.kind)}</b><p>${esc([e.payload.symbol,e.payload.decision,e.payload.reason,e.payload.message,e.payload.action,e.payload.status,e.payload.pnl!==undefined?money(e.payload.pnl):''].filter(Boolean).join(' · '))}</p></div></div>`).join('');
  const details=[['وضعیت',c.connected?'متصل':'قطع'],['سرور',c.server||'—'],['شماره حساب',c.account_login||'—'],['نوع حساب',c.connected?(c.demo?'Demo':'Real · فقط خواندن'):'—'],['ارز',c.currency||'—'],['بالانس بروکر',money(c.balance)],['اکوئیتی بروکر',money(c.equity)],['سرمایهٔ اولیهٔ هم‌اندازه',money(state.capital_binding?.initial)],['Algo Trading',c.trade_allowed?'روشن':'خاموش'],['مجوز ورود بروکر',state.mode==='DEMO'?'فعال · فقط دمو':'خاموش']];
  $('connection-details').innerHTML=details.map(([key,value])=>`<div><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');
  const cfg=state.config,configs=[['ریسک هر معامله',cfg.risk_percent+'٪'],['توقف روزانه',cfg.daily_loss_percent+'٪'],['افت مجاز',cfg.max_drawdown_percent+'٪'],['حداکثر نگهداری',cfg.max_hold_seconds/60+' دقیقه'],['نسبت هدف به ریسک',cfg.reward_risk+'R'],['هزینهٔ فرضی رفت‌وبرگشت',cfg.commission_per_lot_roundtrip_usd+' USD / lot'],['لغزش هر سمت',cfg.slippage_points+' point'],['نماد یورو',cfg.symbols.EURUSD],['نماد طلا',cfg.symbols.XAUUSD],['نماد بیت‌کوین',cfg.symbols.BTCUSD],['بازهٔ ورود FX و طلا','07:00–17:00 UTC'],['بازهٔ بررسی بیت‌کوین','تمام روزها · تابع خوراک بروکر'],['فاصله از خبر','±20 دقیقه'],['سقف ورود روزانه',cfg.max_trades_day]];
  $('config-details').innerHTML=configs.map(([key,value])=>`<div class="config-item"><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');
  if(state.research){const r=state.research;const values=[['تعداد کندل',fa(r.bars)],['معاملات بسته',fa(r.metrics.closed_count)],['سود و زیان خالص',money(r.metrics.pnl)],['بیشترین افت',num(r.max_drawdown_percent)+'%'],['نرخ برد',num(r.metrics.win_rate,1)+'%'],['میانگین نتیجه',num(r.metrics.expectancy_r)+'R'],['شروع داده',date(r.data_start)],['پایان داده',date(r.data_end)]];$('research-results').innerHTML=values.map(([key,value])=>`<div class="config-item"><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');}
  setText('last-update',state.updated?'آخرین بررسی '+time(state.updated):'در انتظار موتور');renderMarket();
}

async function refresh(){
  if(fetching)return;fetching=true;
  try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error('API '+response.status);state=await response.json();render();}
  catch(error){$('api-error').hidden=false;setText('api-error','سرور محلی در دسترس نیست. برنامه را با Start-Arian.cmd اجرا کن.');setText('feed-label','ارتباط با سرور محلی قطع شد');$('feed-dot').classList.remove('connected');}
  finally{fetching=false;}
}
setInterval(()=>setText('clock',new Date().toLocaleTimeString('fa-IR',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit'})+' · تهران'),1000);
refresh();setInterval(refresh,1000);
