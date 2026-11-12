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
let metricsSource='broker-demo', calendarData;
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
      if(widget.key==='economic-calendar')continue;
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
    await loadCalendarFeed();
  }catch(error){notify(error.message,true);}
  finally{toolsLoading=false;}
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.view)));
document.querySelectorAll('[data-go]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.go)));
document.querySelectorAll('[data-tool]').forEach(el=>el.addEventListener('click',()=>{show('tools');$('tool-'+el.dataset.tool).scrollIntoView({behavior:'smooth',block:'start'});}));
$('reload-market-tools').addEventListener('click',()=>loadMarketTools(true));
$('metrics-source').addEventListener('change',()=>{metricsSource=$('metrics-source').value;if(state)render();});

async function loadCalendarFeed(){
  try{
    const response=await fetch('/api/calendar',{cache:'no-store'});
    if(!response.ok)throw new Error('دریافت تقویم از سرور محلی ناموفق بود');
    calendarData=await response.json();renderCalendarFeed();
  }catch(error){setText('calendar-status',error.message);}
}
function renderCalendarFeed(){
  const c=calendarData;if(!c)return;
  const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tehran'});
  const rows=(c.events||[]).filter(e=>($('calendar-range').value==='week'||new Date(e.time*1000).toLocaleDateString('en-CA',{timeZone:'Asia/Tehran'})===today)
    &&($('calendar-currency').value==='all'||['EUR','USD'].includes(e.currency)));
  setText('calendar-status',`${c.fresh?'دادهٔ معتبر هفتهٔ جاری':'دادهٔ معتبر در دسترس نیست'} · آخرین دریافت: ${date(c.fetched_at)}${c.error?' · خطای دریافت: '+c.error:''}`);
  $('calendar-status').classList.toggle('negative',!c.fresh||!!c.error);
  const impact={High:'زیاد',Medium:'متوسط',Low:'کم',Holiday:'تعطیلی'};
  $('calendar-events').innerHTML=rows.length?`<table><thead><tr><th>زمان تهران</th><th>ارز</th><th>اهمیت</th><th>رویداد</th><th>پیش‌بینی</th><th>قبلی</th></tr></thead><tbody>${rows.map(e=>`<tr><td>${esc(date(e.time))}</td><td>${esc(e.currency)}</td><td>${esc(impact[e.impact]||e.impact)}</td><td>${esc(e.title)}</td><td>${esc(e.forecast)}</td><td>${esc(e.previous)}</td></tr>`).join('')}</tbody></table>`:'<p class="empty">رویدادی در فیلتر انتخاب‌شده موجود نیست.</p>';
  $('calendar-import').disabled=!c.fresh||!!c.error;
}
$('calendar-range').addEventListener('change',renderCalendarFeed);
$('calendar-currency').addEventListener('change',renderCalendarFeed);
$('calendar-refresh').addEventListener('click',async()=>{await control('calendar_refresh');await loadCalendarFeed();});
$('calendar-import').addEventListener('click',()=>{
  if(!calendarData?.fresh||calendarData.error)return;
  openCalendar();
  const now=Date.now()/1000,until=Math.min(now+24*3600,calendarData.week_end);
  $('news-until').value=new Date(until*1000).toISOString().slice(0,16);
  $('news-events').value=(calendarData.events||[]).filter(e=>['USD','EUR'].includes(e.currency)&&e.impact==='High'&&e.time>=now-1200&&e.time<=until+1200)
    .map(e=>`${new Date(e.time*1000).toISOString().slice(0,16)} | ${e.currency} | ${e.title}`).join('\n');
});
setInterval(()=>{if(!$('tools').hidden)loadCalendarFeed();},60000);
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
$('pause-button').addEventListener('click',()=>control(state?.monitoring===false?'monitor_resume':state?.paused?'resume':'pause'));
$('flatten-button').addEventListener('click',()=>control('flatten'));
$('demo-daily-stops-toggle').addEventListener('click',()=>control('demo_daily_stops',{enabled:state?.execution?.daily_stops?.enabled===false}));
$('fast-profile').addEventListener('click',()=>control('set_profile',{profile:'fast-demo'}));
$('ea-start').addEventListener('click',()=>control('ea_resume'));
$('risk-budget-toggle').addEventListener('click',()=>control('risk_budget',{enabled:state?.ea_control?.risk_budget_enabled===false}));
$('rollback-button').addEventListener('click',()=>control('rollback'));
$('demo-arm').addEventListener('click',async()=>{if(await control('demo_arm',{confirmation:$('demo-confirm').value.trim()})) $('demo-confirm').value='';});
$('new-demo-session').addEventListener('click',()=>control('demo_new_session',{confirmation:$('new-demo-confirm').value.trim()}));
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
  const age=tick?Math.round(state.now-tick.time):null;
  setText('tick-age',age===null?'—':age<0?`${fa(-age)} ثانیه جلوتر از ساعت سیستم`:`${fa(age)} ثانیه قبل`);
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
  if(state.monitoring===false)setText('decision-reason','پایش به درخواست شما متوقف شده؛ ادامهٔ پایش فقط خواندن داده را فعال می‌کند.');
  setText('news-note',state.news.reason);
}

function tradeTable(trades,compact=false){
  if(!trades.length)return `<div class="empty"><span>▱</span>${compact?'پوزیشن بازی برای نمایش وجود ندارد.':'هنوز معامله‌ای در ژورنال ثبت نشده است.'}</div>`;
  const statuses={OPEN:'باز',STOP:'حد ضرر',TARGET:'حد سود',TIME:'خروج زمانی',FLATTEN:'خروج دستی',BROKER_CLOSED:'خروج بروکر',REJECTED:'رد بروکر',SUBMITTING:'در حال ارسال',UNCERTAIN:'نیازمند بررسی'};
  return `<table><thead><tr><th>نماد / جهت</th><th>محیط</th><th>ورود</th><th>حد ضرر / هدف</th><th>حجم</th><th>${compact?'سود شناور':'نتیجه'}</th><th>وضعیت</th><th>زمان تهران</th></tr></thead><tbody>${[...trades].reverse().map(t=>`<tr><td><b>${esc(t.symbol)}</b> <span class="badge ${t.direction==='SELL'?'sell':''}">${esc(t.direction)}</span></td><td>${t.stream.startsWith('mt5-ea-')?'اکسپرت آریان':t.stream.startsWith('mt5-demo-')?'دموی پایتون':'شبیه‌سازی'}</td><td class="numeric">${num(t.entry,t.symbol==='EURUSD'?5:2)}</td><td class="numeric">${num(t.sl,t.symbol==='EURUSD'?5:2)} / ${num(t.tp,t.symbol==='EURUSD'?5:2)}</td><td class="numeric">${num(t.volume)}</td><td class="numeric ${(compact?t.floating:t.pnl)>0?'positive':(compact?t.floating:t.pnl)<0?'negative':''}">${money(compact?(t.floating+(t.stream.startsWith('mt5-ea-')?t.pnl:0)):t.pnl)}${!compact&&t.status!=='OPEN'&&t.r!=null?` · ${num(t.r)}R`:''}</td><td>${esc(statuses[t.status]||t.status)}${t.execution_audit?.status==='quote_mismatch'?'<small class="negative"> · مغایرت اجرا</small>':''}</td><td>${esc(date(t.opened))}</td></tr>`).join('')}</tbody></table>`;
}

function render(){
  const observed=metricsSource==='external-ea',demo=metricsSource==='broker-demo',tracking=state.tracking;
  setText('execution-health',`${state.execution?.profile||'conservative'} · ${state.mode} · ${(state.execution?.blockers||[]).join(' · ')||'موتور فعال؛ در انتظار ستاپ معتبر'}`);
  const a=observed?(tracking?.metrics||{daily_pnl:null,drawdown_percent:null,closed_count:0,win_rate:null,daily_drawdown_percent:0,trades_today:0,consecutive_losses:0}):demo?(state.demo_account||state.account):state.account;
  const dailyStopsEnabled=state.execution?.daily_stops?.enabled!==false;
  const l=observed?(tracking?.learner||{phase:'collecting',version:0,message:'در انتظار دریافت معاملات اکسپرت آریان'}):state.learner,c=state.connection;
  setText('broker-account-name',c.connected?`${c.server} · ${c.account_login||'—'}`:'حساب متاتریدر متصل نیست');
  setText('broker-account-status',c.connected?`${c.demo?'حساب دمو':'حساب واقعی · فقط خواندن'} · ${c.currency} · ${c.trade_allowed?'Algo Trading روشن':'Algo Trading خاموش'}`:'برای خواندن موجودی و قیمت، متاتریدر را متصل نگه دار');
  if(state.monitoring===false){setText('broker-account-name','پایش حساب متوقف است');setText('broker-account-status','سوابق محلی حفظ شده‌اند؛ ترمینال خودکار باز نمی‌شود.');}
  setText('broker-balance',c.connected?money(c.balance):'—');setText('broker-equity',c.connected?money(c.equity):'—');setText('broker-free-margin',c.connected?money(c.margin_free):'—');
  setText('mode',observed?'MT5 EA MONITOR':state.mode==='DEMO'?'BROKER DEMO':'SHADOW MODE');
  setText('pause-button',state.monitoring===false?'ادامهٔ پایش (بدون ورود)':state.paused?'ادامهٔ ورود پایتون':'توقف ورود');
  setText('equity',state.capital_binding?money(a.equity):'—');setText('balance-detail',state.capital_binding?`مبنای دمو: ${money(state.capital_binding.initial)} · بالانس شبیه‌سازی: ${money(a.balance)}`:'در انتظار هم‌اندازه‌سازی با موجودی حساب دمو');
  setText('equity-label',observed?'سرمایهٔ منتسب به اکسپرت':demo?'اکوئیتی حساب دمو':'سرمایهٔ شبیه‌سازی');
  if(demo)setText('balance-detail',`دفتر ریسک ${state.risk_session?'آزمون تازه':'حساب'} · مبنا: ${money(a.initial)} · بالانس بروکر: ${money(a.balance)}`);
  if(observed)setText('balance-detail',`مبنای رصد + نتیجهٔ اکسپرت · بالانس دفتر: ${money(a.balance)}`);
  $('demo-daily-stops-toggle').disabled=!(c.connected&&c.demo&&c.currency==='USD'&&state.clock?.ready);
  $('demo-daily-stops-toggle').setAttribute('aria-checked',String(dailyStopsEnabled));
  setText('demo-daily-stops-toggle',dailyStopsEnabled?'لغو توقف روزانه و زیان متوالی':'فعال‌کردن دوبارهٔ توقف روزانه و زیان متوالی');
  setText('demo-daily-stops-note',(dailyStopsEnabled?'توقف زیان روزانه و زیان متوالی روشن است.':'هر دو توقف برای این نشست حساب دمو خاموش‌اند؛ زیان روزانه و تعداد زیان‌ها همچنان ثبت می‌شوند.')+' حد ضرر هر معامله، سقف افت کل، سقف تعداد ورود و فاصلهٔ بین معاملات برقرارند. پس از قطع اتصال، تغییر حساب یا راه‌اندازی مجدد، توقف‌ها روشن می‌شوند. این دکمه به‌تنهایی مجوز معامله را فعال نمی‌کند.');
  const bridge=state.ea_control;
  const budgetEnabled=bridge?.risk_budget_enabled!==false;
  $('risk-budget-toggle').hidden=!observed;
  $('risk-budget-toggle').setAttribute('aria-checked',String(budgetEnabled));
  setText('risk-budget-toggle',budgetEnabled?'سقف زیان و افت: روشن — خاموش کن':'سقف زیان و افت: خاموش — روشن کن');
  setText('risk-budget-note',observed?(budgetEnabled?'سقف زیان روزانه و افت سرمایه فعال است.':'سقف زیان روزانه و افت سرمایه برای این نشست دمو خاموش است؛ زیان بیشتر مانع ورود نمی‌شود.')+' تغییر کلید ورود را متوقف می‌کند؛ برای ادامه «شروع کنترل‌شده» را بزن. با راه‌اندازی مجدد، سقف‌ها روشن می‌شوند.':'این کلید فقط روی اکسپرت دمو اثر دارد؛ محدودیت‌های موتور پایتون مستقل‌اند.');
  const ready=bridge?.readiness;
  setText('ea-readiness',ready?.allowed?'محدودیت عمومی ریسک باز است؛ ورود به تأیید چارت، قیمت تازه و ستاپ معتبر نیاز دارد.':(ready?.reasons||['وضعیت آماده نیست']).join(' · '));
  $('ea-start').disabled=!!bridge?.running_requested;
  setText('ea-start',bridge?.running_requested?'درخواست شروع فعال است':'شروع کنترل‌شدهٔ اکسپرت دمو');
  const chartStates={READY:'آمادهٔ جست‌وجوی ستاپ',RISK_BLOCK:'مسدود با محدودیت ریسک',PAUSE:'متوقف',FLATTEN:'توقف و خروج',NO_COMMAND:'بدون فرمان معتبر',LEASE_EXPIRED:'اجازه منقضی شده',INPUT_DISABLED:'EnableEntries خاموش',EXECUTION_LATCH:'قفل مغایرت اجرا',ALGO_DISABLED:'Algo Trading خاموش'};
  const entryReasons={WAIT_SETUP:'منتظر ستاپ معتبر',ACCOUNT_BUSY:'حساب سفارش یا پوزیشن دیگری دارد',INVALID_QUOTE:'قیمت نامعتبر',STALE_QUOTE:'تیک قدیمی',INVALID_TICK_SIZE:'گام قیمت نامعتبر',SPREAD_LIMIT:'اسپرد بالاتر از حد تنظیم‌شده',BUY_LIMIT_LEVELS:'سطوح خرید معلق هنوز معتبر نیستند',SELL_LIMIT_LEVELS:'سطوح فروش معلق هنوز معتبر نیستند',VOLUME_BELOW_MIN_OR_RISK:'حجم مجاز ریسک کمتر از حداقل لات بروکر است یا محاسبهٔ حجم نامعتبر است',PENDING_ACCEPTED:'سفارش معلق پذیرفته شد',PENDING_ACTIVE:'سفارش معلق فعال است',CYCLE_ALREADY_USED:'این سایکل قبلاً مصرف شده است',ENTRY_LOCK_BUSY:'نوبت ارسال در اختیار چارت دیگری است'};
  $('ea-charts').replaceChildren();
  for(const symbol of ['EURUSD','GOLD','BITCOIN']){
    const charts=(bridge?.charts||[]).filter(c=>c.symbol===symbol);
    const row=document.createElement('p');row.textContent=`${symbol}: `+(charts.length?charts.map(c=>`نسخه ${c.version} · ${(c.timeframe||'').replace('PERIOD_','')} · ${chartStates[c.state]||c.state}${c.entry_reason&&c.state==='READY'?' · '+(entryReasons[c.entry_reason]||(c.entry_reason.startsWith('BROKER_REJECT_')?'بروکر سفارش را رد کرد؛ کد '+c.entry_reason.slice(14):c.entry_reason)):''}`).join(' / '):'تأیید زنده از اکسپرت دریافت نشده است');$('ea-charts').append(row);
  }
  setText('metrics-note',observed?`منبع: اکسپرت آریان · magic ${(tracking?.magic_numbers||[]).join('، ')} · فرمان: ${bridge?.requested||'—'} · ${bridge?.acknowledged?'دریافت تأیید شده':'در انتظار تأیید'}${bridge?.error?' · خطای پل: '+bridge.error:''}`:demo?'منبع: حساب دموی بروکر و دفتر ریسک حساب':'منبع: معاملات شبیه‌سازی پایتون؛ مستقل از معاملات اکسپرت متاتریدر');
  setText('execution-warning',observed&&a.review_count?`${fa(a.review_count)} معامله با مغایرت قیمت اجرا و تیک هم‌زمان نیازمند بررسی است. سود زیر، ثبت حساب دمو است؛ این معاملات وارد آموزش ایجنت نمی‌شوند.`:observed&&a.pending_audit_count?`${fa(a.pending_audit_count)} معامله در انتظار تطبیق قیمت اجراست؛ تا تکمیل بررسی وارد آموزش نمی‌شود.`:'');
  $('execution-warning').hidden=!(observed&&(a.review_count||a.pending_audit_count));
  signed('daily-pnl',a.daily_pnl);setText('drawdown',`${num(a.drawdown_percent)}%`);setText('trade-count',fa(a.closed_count));
  setText('win-rate',a.win_rate===null?'نرخ برد پس از اولین خروج':`نرخ برد ${num(a.win_rate,1)}٪ · ${num(a.expectancy_r)}R میانگین`);
  if(observed&&(a.review_count||a.pending_audit_count))setText('win-rate',`نرخ برد ثبت‌شدهٔ دمو: ${num(a.win_rate,1)}٪ · کیفیت اجرا تأیید نشده`);
  $('feed-dot').classList.toggle('connected',!!c.connected);
  setText('feed-label',c.connected?'متاتریدر متصل است':c.error||'متاتریدر متصل نیست');
  setText('feed-detail',c.connected?`${c.server} · ${c.demo?'DEMO':'READ-ONLY LIVE'} · ${c.currency}`:'باز کردن متاتریدر و ورود به حساب');
  setText('execution-label',state.flatten_pending?'خروج در انتظار قیمت تازه':state.mode==='DEMO'?'ورود خودکار فقط روی دمو فعال است':'اجرای واقعی خاموش');
  const stale=state.monitoring!==false&&state.updated&&state.now-state.updated>35;
  $('api-error').hidden=!(state.error||stale);setText('api-error',state.error||'موتور بیش از ۳۵ ثانیه به‌روزرسانی نشده است؛ وضعیت اتصال را بررسی کن.');
  for(const symbol of ['EURUSD','XAUUSD','BTCUSD']){const tick=state.markets[symbol]?.tick;setText('quote-'+symbol,tick?num(tick.bid,state.markets[symbol]?.spec?.digits??2):'—');}
  setText('risk-used',`${num(a.daily_drawdown_percent)}%`);$('risk-progress').value=Math.min(1,a.daily_drawdown_percent||0);
  setText('open-risk',observed?`ریسک اولیهٔ باز: ${money(a.open_risk_money)} · آخرین ورود: ${money(a.last_risk_money)} (${num(a.last_risk_percent)}٪ از موجودی دفتر پیش از ورود)`:'ریسک هر ورود شبیه‌سازی حداکثر ۰٫۲۵٪');
  setText('drawdown-note',observed?(budgetEnabled?'افت دفتر اکسپرت از اوج رصدشده؛ سقف ورود: ۵٪':'افت دفتر اکسپرت؛ سقف افت خاموش است'):'سقف توقف کل: ۵٪');
  setText('day-trades',observed?fa(a.trades_today):`${fa(a.trades_today)} / ${fa(state.config.max_trades_day)}`);setText('loss-streak',observed?fa(a.consecutive_losses):`${fa(a.consecutive_losses)} / ${demo&&!dailyStopsEnabled?'توقف خاموش':fa(state.config.max_consecutive_losses)}`);
  setText('risk-per-trade',observed?`${num(a.open_risk_percent)}٪`:fa(state.config.risk_percent)+'٪');
  setText('risk-per-trade-label',observed?'ریسک اولیهٔ باز':'هر معامله');
  setText('risk-cap-note',observed?(budgetEnabled?'زیان خالص امروز؛ سقف ورود: ۱٪':'زیان خالص امروز؛ سقف زیان خاموش است'):demo&&!dailyStopsEnabled?'توقف زیان روزانه خاموش است':'از سقف زیان روزانهٔ ۱٪');
  setText('execution-label',state.monitoring===false?'پایش متوقف است؛ ترمینال خودکار باز نمی‌شود':state.flatten_pending?'خروج در انتظار تأیید اکسپرت یا بسته‌شدن پوزیشن‌ها':observed?(bridge?.running_requested?(ready?.allowed?'شروع درخواست شده؛ وضعیت هر چارت را بررسی کن':'شروع درخواست شده؛ ورود با محدودیت ریسک مسدود است'):bridge?.acknowledged?'توقف اکسپرت تأیید شده':'منتظر تأیید اکسپرت'):state.mode==='DEMO'?'ورود خودکار فقط روی دمو فعال است':'اجرای واقعی پایتون خاموش');
  setText('agent-summary',l.message);setText('policy-version',`CHECKLIST v${l.version}`);setText('agent-version',`v${l.version}`);
  const progress=l.phase==='forward'?l.forward_count||0:l.collection_count||0,goal=l.phase==='forward'?50:100;
  setText('learning-count',`${fa(progress)} از ${fa(goal)} معامله${l.phase==='forward'?'ٔ آینده':''}`);$('learning-progress').max=goal;$('learning-progress').value=progress;
  setText('learning-source',observed?`اکسپرت: ${fa(l.observed_closed||0)} خروج · ${fa(l.eligible_count||0)} نمونهٔ قابل آموزش · ${fa(l.excluded_count||0)} کنارگذاشته‌شده به‌دلیل کمبود داده یا مغایرت اجرا؛ یادگیری فقط پژوهشی است`:`مبنای شبیه‌سازی: ${fa(l.total_trades||0)} خروج؛ فقط معاملات بسته‌شده وارد آموزش می‌شوند`);
  if(observed)setText('agent-summary',`${l.message} · این ایجنت پارامترهای اکسپرت MT5 را تغییر نمی‌دهد.`);
  setText('agent-phase',l.phase==='forward'?'آزمون آینده':'جمع‌آوری داده');setText('agent-message',l.message);
  const policy=l.active||l.candidate;
  setText('learned-rule',policy?`${l.active?'فیلتر فعال در شبیه‌سازی':'فرضیه در حال آزمایش'}: ورود در ${policy.rule.feature} = ${policy.rule.value} حذف شود. شناسه: ${policy.id}`:'هنوز قانون تازه‌ای تأیید نشده است. قواعد پایه فعال‌اند.');
  const paper=state.account;
  const rows=[['سرمایه',money(state.baseline.equity),money(paper.equity)],['معاملات بسته',fa(state.baseline.closed_count),fa(paper.closed_count)],['میانگین نتیجه',num(state.baseline.expectancy_r)+'R',num(paper.expectancy_r)+'R'],['افت سرمایه',num(state.baseline.drawdown_percent)+'%',num(paper.drawdown_percent)+'%']];
  $('comparison').innerHTML=rows.map(r=>`<tr><td>${esc(r[0])}</td><td class="numeric">${esc(r[1])}</td><td class="numeric">${esc(r[2])}</td></tr>`).join('');
  const all=[...state.trades,...state.demo_trades,...(state.tracking?.trades||[])].sort((x,y)=>x.opened-y.opened);
  $('positions').innerHTML=`<div class="table-wrap">${tradeTable(all.filter(t=>['OPEN','SUBMITTING','UNCERTAIN'].includes(t.status)),true)}</div>`;
  $('trade-history').innerHTML=tradeTable(all);
  const eventNames={START:'شروع موتور',CAPITAL_SYNC:'هماهنگ‌سازی سرمایهٔ دمو',CONTROL:'کنترل',DECISION:'تصمیم',PAPER_OPEN:'ورود شبیه‌سازی',PAPER_CLOSE:'خروج شبیه‌سازی',DEMO_SUBMIT:'ارسال دمو',DEMO_CLOSE:'خروج دمو',DEMO_EXIT_REQUEST:'درخواست خروج دمو',NEWS_MANUAL_REVIEW:'بررسی اخبار',ENGINE_ERROR:'خطای موتور',POLICY_APPLIED_PAPER_ONLY:'چک‌لیست تازه',POLICY_ROLLBACK:'بازگشت چک‌لیست'};
  $('events').innerHTML=state.events.map(e=>`<div class="event"><time>${time(e.ts)}</time><div><b>${esc(eventNames[e.kind]||e.kind)}</b><p>${esc([e.payload.symbol,e.payload.decision,e.payload.reason,e.payload.message,e.payload.action,e.payload.status,e.payload.pnl!==undefined?money(e.payload.pnl):''].filter(Boolean).join(' · '))}</p></div></div>`).join('');
  const details=[['وضعیت',c.connected?'متصل':'قطع'],['سرور',c.server||'—'],['شماره حساب',c.account_login||'—'],['نوع حساب',c.connected?(c.demo?'Demo':'Real · فقط خواندن'):'—'],['ارز',c.currency||'—'],['بالانس بروکر',money(c.balance)],['اکوئیتی بروکر',money(c.equity)],['سرمایهٔ اولیهٔ هم‌اندازه',money(state.capital_binding?.initial)],['Algo Trading',c.trade_allowed?'روشن':'خاموش'],['مجوز ورود بروکر',state.mode==='DEMO'?'فعال · فقط دمو':'خاموش']];
  $('connection-details').innerHTML=details.map(([key,value])=>`<div><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');
  const cfg=state.config,configs=[['ریسک هر معامله',cfg.risk_percent+'٪'],['توقف روزانه',dailyStopsEnabled?cfg.daily_loss_percent+'٪':'خاموش برای نشست دمو'],['توقف زیان متوالی',dailyStopsEnabled?cfg.max_consecutive_losses:'خاموش برای نشست دمو'],['افت مجاز',cfg.max_drawdown_percent+'٪'],['حداکثر نگهداری',cfg.max_hold_seconds/60+' دقیقه'],['نسبت هدف به ریسک',cfg.reward_risk+'R'],['هزینهٔ فرضی رفت‌وبرگشت',cfg.commission_per_lot_roundtrip_usd+' USD / lot'],['لغزش هر سمت',cfg.slippage_points+' point'],['نماد یورو',cfg.symbols.EURUSD],['نماد طلا',cfg.symbols.XAUUSD],['نماد بیت‌کوین',cfg.symbols.BTCUSD],['بازهٔ ورود FX و طلا',`${cfg.session_start_utc}:00–${cfg.session_end_utc}:00 UTC`],['بازهٔ بررسی بیت‌کوین','تمام روزها · تابع خوراک بروکر'],['فاصله از خبر','±20 دقیقه'],['سقف ورود روزانه',cfg.max_trades_day],['فاصلهٔ بعد از خروج',cfg.cooldown_seconds+' ثانیه'],['پروفایل',cfg.execution_profile]];
  $('config-details').innerHTML=configs.map(([key,value])=>`<div class="config-item"><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');
  if(state.research){const r=state.research;const values=[['تعداد کندل',fa(r.bars)],['معاملات بسته',fa(r.metrics.closed_count)],['سود و زیان خالص',money(r.metrics.pnl)],['بیشترین افت',num(r.max_drawdown_percent)+'%'],['نرخ برد',num(r.metrics.win_rate,1)+'%'],['میانگین نتیجه',num(r.metrics.expectancy_r)+'R'],['شروع داده',date(r.data_start)],['پایان داده',date(r.data_end)]];$('research-results').innerHTML=values.map(([key,value])=>`<div class="config-item"><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('');}
  setText('hold-label','خروج زمانی پایتون: '+fa(state.config.max_hold_seconds/60)+' دقیقه؛ اکسپرت مستقل است');
  setText('last-update',state.updated?'آخرین بررسی '+time(state.updated):'در انتظار موتور');renderMarket();
  if(state.monitoring===false)setText('last-update','پایش متوقف است · سوابق محلی حفظ شده‌اند');
}

async function refresh(){
  if(fetching)return;fetching=true;
  try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error('API '+response.status);state=await response.json();render();}
  catch(error){$('api-error').hidden=false;setText('api-error','سرور محلی در دسترس نیست. برنامه را با Start-Arian.cmd اجرا کن.');setText('feed-label','ارتباط با سرور محلی قطع شد');$('feed-dot').classList.remove('connected');}
  finally{fetching=false;}
}
setInterval(()=>setText('clock',new Date().toLocaleTimeString('fa-IR',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit'})+' · تهران'),1000);
refresh();setInterval(refresh,1000);
