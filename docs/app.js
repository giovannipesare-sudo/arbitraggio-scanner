import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.57.0/+esm';

const cfg = window.QUOTESCOPE_CONFIG || {};
const ready = /^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(cfg.SUPABASE_URL || '') &&
              !!cfg.SUPABASE_PUBLISHABLE_KEY && !cfg.SUPABASE_PUBLISHABLE_KEY.startsWith('INSERISCI');
const $ = id => document.getElementById(id);
const show = (id, value=true) => { $(id).hidden = !value; };
const money = n => Number.isFinite(Number(n)) ? Number(n).toLocaleString('it-IT', {maximumFractionDigits:2}) : '—';
const dt = s => s ? new Date(s).toLocaleString('it-IT', {dateStyle:'short',timeStyle:'short'}) : '—';
const fmtDur = s => {if(!Number.isFinite(s))return '—';s=Math.max(0, Math.floor(s)); if(s<60)return `${s}s`;if(s<3600)return `${Math.floor(s/60)}m ${s%60}s`;return `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;};
const duration = row => Math.max(0,(Date.parse(row.last_seen_at) - Date.parse(row.first_seen_at)) / 1000);
const textCell = (tr, value, className='') => {const cell = document.createElement('td');cell.textContent=String(value ?? '—');if(className)cell.className=className;tr.appendChild(cell);return cell;};
const empty = (target, count, message) => {const tr=document.createElement('tr');const td=textCell(tr, message, 'empty');td.colSpan=count;target.replaceChildren(tr);};
const median = nums => {if(!nums.length)return null;const a=[...nums].sort((a,b)=>a-b);const mid=Math.floor(a.length/2);return a.length%2?a[mid]:(a[mid-1]+a[mid])/2;};
let sb, currentUser, opportunities=[], runs=[], contacts=[], marketSources=[], comparisonReport=null;

function nav(section){const target=['overview','history','quotes','comparison-all','best-quote','found','compare','alerts','system'].includes(section)?section:'overview';for(const name of ['overview','history','quotes','comparison-all','best-quote','found','compare','alerts','system'])show(`view-${name}`,name===target);for(const a of document.querySelectorAll('.nav'))a.classList.toggle('active',a.dataset.section===target);$('page-title').textContent={overview:'Panoramica',history:'Storico opportunità',quotes:'Quote bookmaker','comparison-all':'Tutti i confronti','best-quote':'Migliore quota',found:'Opportunità trovate',compare:'Diagnostica mercati',alerts:'Notifiche',system:'Stato acquisizione'}[target];}
function prettyState(row){return row.status==='active'?'Attiva':'Conclusa';}
function renderOpRows(id,arr,history=false){const body=$(id);body.replaceChildren();if(!arr.length){empty(body,history?8:6,'Nessuna opportunità registrata per questi filtri');return;}for(const r of arr){const tr=document.createElement('tr');if(history){textCell(tr,r.event_name);textCell(tr,r.outcome);const state=textCell(tr,prettyState(r));state.className='pill '+(r.status==='active'?'':'closed');textCell(tr,dt(r.first_seen_at));textCell(tr,dt(r.last_seen_at));textCell(tr,fmtDur(duration(r)));textCell(tr,`${money(r.best_margin_pct)}%`,'profit');textCell(tr,r.observation_count);}else{textCell(tr,`${r.event_name} · ${r.outcome}`);textCell(tr,money(r.snai_odds));textCell(tr,money(r.betfair_lay));textCell(tr,`${money(r.last_margin_pct)}%`,'profit');textCell(tr,fmtDur(duration(r)));textCell(tr,r.observation_count);}body.append(tr);}}
function renderRecent(arr){const body=$('recent-body');body.replaceChildren();if(!arr.length){empty(body,6,'Nessun episodio concluso');return;}for(const r of arr){const tr=document.createElement('tr');textCell(tr,r.event_name);textCell(tr,r.outcome);textCell(tr,dt(r.first_seen_at));textCell(tr,dt(r.last_seen_at));textCell(tr,fmtDur(duration(r)));textCell(tr,`${money(r.best_margin_pct)}%`,'profit');body.append(tr);}}
function renderChart(closed){const edges=[15,30,60,180,Infinity],labels=['0–14s','15–29s','30–59s','1–3 min','> 3 min'];const counts=[0,0,0,0,0];for(const r of closed){const s=duration(r);counts[edges.findIndex(x=>s<x)]++;}const max=Math.max(...counts,1);const host=$('duration-chart');host.replaceChildren();counts.forEach((num,i)=>{const row=document.createElement('div');row.className='bar-row';const label=document.createElement('span');label.textContent=labels[i];const track=document.createElement('div');track.className='bar-track';const fill=document.createElement('div');fill.className='bar-fill';fill.style.width=`${100*num/max}%`;track.append(fill);const count=document.createElement('span');count.textContent=num;row.append(label,track,count);host.append(row);});}
function render(){renderMarketSources();renderUniversalComparison();renderNewPages();const now=Date.now(),last24=now-86400000,active=opportunities.filter(r=>r.status==='active'),lastDay=opportunities.filter(r=>Date.parse(r.first_seen_at)>=last24),closed=opportunities.filter(r=>r.status==='closed'&&Date.parse(r.last_seen_at)>=last24);
 $('m-active').textContent=active.length;$('m-day').textContent=lastDay.length;$('m-duration').textContent=closed.length?fmtDur(median(closed.map(duration))):'—';
 const last=runs[0];$('m-last').textContent=last?new Date(last.checked_at).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'}):'—';$('m-last-sub').textContent=last?dt(last.checked_at):'In attesa dello scanner';
 let state='offline',label='● Scanner non attivo';if(last){const age=(now-Date.parse(last.checked_at))/1000;if(age<180&&last.status==='ok'){state='ok';label='● Scanner operativo';}else if(age<180){state='bad';label='● Acquisizione incompleta';}else{label='● Scanner offline / inattivo';}}
 $('system-pill').textContent=label;$('system-pill').className='status-pill '+(state==='ok'?'':state==='bad'?'bad':'muted');renderOpRows('active-body',active.slice(0,20));renderRecent(opportunities.filter(r=>r.status==='closed').slice(0,20));renderChart(closed);filterHistory();
 const rb=$('runs-body');rb.replaceChildren();if(!runs.length)empty(rb,5,'Nessuna acquisizione registrata');for(const r of runs.slice(0,30)){const tr=document.createElement('tr');textCell(tr,dt(r.checked_at));textCell(tr,r.status);textCell(tr,r.snai_events);textCell(tr,r.betfair_events);textCell(tr,r.matched_events);rb.append(tr);}
 $('q-frequency').textContent=runs.length>1?fmtDur((Date.parse(runs[0].checked_at)-Date.parse(runs[1].checked_at))/1000):'—';$('q-status').textContent=last?.status||'—';$('q-matched').textContent=last?.matched_events??'—';$('q-error').textContent=last?.error_message||'Nessuno';renderContacts();}
function filterHistory(){const filter=$('filter-state').value,min=Number($('filter-margin').value||0);const filtered=opportunities.filter(r=>(filter==='all'||r.status===filter)&&Number(r.best_margin_pct)>=min);renderOpRows('history-body',filtered,true);}
function renderContacts(){const tbody=$('contacts-body');tbody.replaceChildren();if(!contacts.length){empty(tbody,6,'Nessun destinatario configurato');return;}for(const c of contacts){const tr=document.createElement('tr');textCell(tr,c.label);textCell(tr,c.phone_e164);textCell(tr,`${money(c.min_margin_pct)}%`);textCell(tr,c.min_confirmations);const cb=document.createElement('input');cb.type='checkbox';cb.checked=!!c.enabled;cb.setAttribute('aria-label','Abilita destinatario '+c.label);cb.addEventListener('change',()=>setEnabled(c,cb.checked));const td=document.createElement('td');td.append(cb);tr.append(td);const btn=document.createElement('button');btn.textContent='Elimina';btn.className='outline small';btn.addEventListener('click',()=>removeContact(c));const td2=document.createElement('td');td2.append(btn);tr.append(td2);tbody.append(tr);}}

function renderMarketSources(){
 const list=marketSources, body=$('quotes-body');body.replaceChildren();
 $('qs-comp').textContent=list.length;
 $('qs-events').textContent=list.reduce((a,r)=>a+(Number(r.events_count)||0),0).toLocaleString('it-IT');
 $('qs-selections').textContent=list.reduce((a,r)=>a+(Number(r.selections_count)||0),0).toLocaleString('it-IT');
 if(!list.length){empty(body,6,'Nessuna acquisizione multi-bookmaker ancora disponibile');return;}
 for(const r of [...list].sort((a,b)=>a.bookmaker.localeCompare(b.bookmaker)||a.competition_name.localeCompare(b.competition_name))){
  const tr=document.createElement('tr');
  textCell(tr,r.bookmaker);textCell(tr,r.competition_name);
  textCell(tr,r.events_count);textCell(tr,r.selections_count);textCell(tr,dt(r.captured_at));
  const td=document.createElement('td'),btn=document.createElement('button');btn.className='outline small';btn.textContent='Quote';
  btn.addEventListener('click',()=>loadMarketDetails(r));td.append(btn);tr.append(td);body.append(tr);
 }
}
async function loadMarketDetails(r){
 $('qs-details-title').textContent=`${r.bookmaker} · ${r.competition_name} · caricamento quote…`;
 const target=$('qs-details-body');target.replaceChildren();
 const {data,error}=await sb.from('bookmaker_market_snapshots').select('snapshot,captured_at')
  .eq('owner_id',currentUser.id).eq('bookmaker',r.bookmaker).eq('competition_key',r.competition_key).single();
 if(error){$('qs-details-title').textContent='Impossibile caricare le quote';return;}
 const events=data?.snapshot?.events||[];
 $('qs-details-title').textContent=`${r.bookmaker} · ${r.competition_name} · ${events.length} eventi · ${dt(data.captured_at)}`;
 let rows=0;
 for(const ev of events){
  for(const market of ev.all_markets||[]){
   for(const sel of market.outcomes||[]){
    if(rows>=250)break;
    const tr=document.createElement('tr');
    textCell(tr,ev.event);textCell(tr,market.market_name);textCell(tr,market.line??'—');
    textCell(tr,sel.selection);textCell(tr,money(sel.odds));target.append(tr);rows++;
   }
   if(rows>=250)break;
  }
  if(rows>=250)break;
 }
 if(!rows)empty(target,5,'Nessuna quota nell’istantanea');
 $('qs-details-note').textContent=rows>=250?'Prime 250 selezioni mostrate per leggibilità. La raccolta completa è conservata nel database.':'Le quote sono una fotografia osservata, non garantiscono disponibilità o eseguibilità.';
}

function marketLabel(key){if(key==='1x2')return '1X2 finale';if(key==='btts')return 'Goal / No Goal';if(/^ou_\d+\.\d+$/.test(key))return 'Under/Over '+key.slice(3);return key||'—';}
function sortReportMarkets(a,b){return (a.starts_at||'').localeCompare(b.starts_at||'') || (a.event||'').localeCompare(b.event||'');}
function displayAction(action){return action==='BACK'?'PUNTATA':action==='LAY'?'BANCATA':action;}
function pairMarketName(item){
 const key=item?.market||'';
 if(key!=='1x2_dc')return marketLabel(key);
 const usesDoubleChance=[item?.a?.selection,item?.b?.selection].some(s=>['1X','X2','12'].includes(String(s||'').toUpperCase()));
 const label=usesDoubleChance?'1X2 / Doppia Chance':'1X2 finale';
 return item?.kind==='BACK/LAY'?`${label} · PUNTATA/BANCATA`:label;
}
function renderTwoLeg(){
 const body=$('pair-body');body.replaceChildren();
 const r=comparisonReport;
 const props=[['pair-count','two_leg_pairs_evaluated'],['pair-positive','two_leg_positive_count'],['pair-backback','two_leg_back_back_positive'],['pair-backlay','two_leg_back_lay_positive']];
 for(const [id,key] of props)$(id).textContent=Number.isFinite(r?.[key])?r[key].toLocaleString('it-IT'):'—';
 if(!r || !Number.isFinite(r.two_leg_pairs_evaluated)){
  $('pair-note').textContent='Motore a due operazioni non ancora attivo. Avvia lo scanner aggiornato e attendi una nuova rilevazione.';
  empty(body,7,'Report a due operazioni non ancora disponibile');return;
 }
 const positives=Array.isArray(r.two_leg_opportunities)?r.two_leg_opportunities:[];
 const diagnostics=Array.isArray(r.two_leg_best_comparisons)?r.two_leg_best_comparisons:[];
 const chosen=positives.length?positives:diagnostics.slice(0,25);
 $('pair-note').textContent=positives.length
  ? `Combinazioni solo teoriche con profitto positivo: ${r.two_leg_positive_count}. Nessuna eseguibilità certificata.`
  : `Nessun arbitraggio a due operazioni teoricamente positivo. Di seguito le 25 combinazioni più vicine alla soglia, anche in perdita.`;
 if(!chosen.length){empty(body,7,'Non ci sono coppie confrontabili nel report');return;}
 for(const item of chosen.slice(0,100)){
  const tr=document.createElement('tr');
  const eventTd=document.createElement('td');
  const eventName=document.createElement('div');eventName.className='pair-event-name';eventName.textContent=item.event||'—';
  const eventDate=document.createElement('div');eventDate.className='pair-event-date';eventDate.textContent=dt(item.starts_at);
  eventTd.append(eventName,eventDate);tr.append(eventTd);
  textCell(tr,pairMarketName(item));
  for(const key of ['a','b']){
   const op=item[key]||{};
   const td=document.createElement('td');td.className='pair-op-cell';
   const head=document.createElement('div');head.className='pair-op-head';head.textContent=`${op.operator||'—'} · ${displayAction(op.action)||'—'} ${op.selection||'—'}`;
   const odds=document.createElement('div');odds.textContent=`Quota ${money(op.odds)} · importo ${money(op.stake_eur)} €`;
   td.append(head,odds);
   if(op.action==='LAY'){
    const risk=document.createElement('div');risk.className='pair-risk';risk.textContent=`Responsabilità ${money(op.liability_eur)} €`;
    td.append(risk);
   }
   tr.append(td);
  }
  const outcomes=Object.entries(item.scenario_profits_eur||{}).map(([k,v])=>`${k}: ${money(v)} €`).join(' | ');
  textCell(tr,outcomes);
  const td=textCell(tr,`${money(item.min_profit_eur)} €`);
  if(Number(item.min_profit_eur)>0)td.className='profit';
  textCell(tr,`${money(item.roi_pct)}%${item.kind==='BACK/LAY'?' · DELAYED':''}`);
  body.append(tr);
 }
}

function renderUniversalComparison(){
 renderTwoLeg();
 const body=$('cmp-markets-body'),layBody=$('cmp-lay-body'),r=comparisonReport;
 const status=$('cmp-last');
 for(const [id,field] of [['cmp-events','matched_events'],['cmp-three','events_with_three_sources'],['cmp-markets','markets_compared'],['cmp-three-markets','markets_with_three_sources'],['cmp-backlay','betfair_lay_comparisons']])$(id).textContent=Number.isFinite(r?.[field])?Number(r[field]).toLocaleString('it-IT'):'—';
 body.replaceChildren();layBody.replaceChildren();$('cmp-sources').replaceChildren();
 if(!r){status.textContent='Nessun report universale pubblicato: attendi una nuova acquisizione.';empty(body,7,'Report non ancora disponibile');empty(layBody,7,'Nessun dato BACK/LAY disponibile');return;}
 const age=(Date.now()-Date.parse(r.captured_at))/1000;
 const fresh=Number.isFinite(age)&&age>=-60&&age<=900;
 status.textContent=`Ultimo report: ${dt(r.captured_at)} · ${fresh?'Diagnostico, non eseguibile':'DATI NON RECENTI — non utilizzare per valutazioni attuali'}`;
 status.className='note'+(fresh?'':' cmp-stale');
 const statuses=r.source_status||{};
 for(const [name, info] of Object.entries(statuses)){
  const chip=document.createElement('span');chip.className='cmp-source';
  const captured=info&&typeof info==='object'?info.captured_at:null;
  const sourceAge=captured?(Date.now()-Date.parse(captured))/1000:Infinity;
  const limit=name==='BETFAIR'?180:900;
  const validAge=Number.isFinite(sourceAge)&&sourceAge>=-60;
  const recent=validAge&&sourceAge<limit;
  const elapsed=!validAge?'orario da verificare':sourceAge<60?`${Math.max(0,Math.round(sourceAge))} s fa`:`${Math.floor(sourceAge/60)} min fa`;
  if(!recent)chip.classList.add('cmp-source-stale');
  const label=name==='BETFAIR'?'BETFAIR · dati Exchange ritardati':name;
  const note=recent?'':` · oltre ${Math.round(limit/60)} min`;
  chip.textContent=captured?`${label}: rilevazione ${dt(captured)} · ${elapsed}${note}`:`${label}: nessuna rilevazione disponibile`;
  $('cmp-sources').append(chip);
 }
 const marketFilter=$('cmp-market-filter').value;
 const threeOnly=$('cmp-three-filter').checked;
 const all=(Array.isArray(r.comparisons)?r.comparisons:[]).slice().sort(sortReportMarkets);
 let displayed=0;
 for(const event of all){
  const entries=Array.isArray(event.markets)?event.markets:[];
  for(const m of entries){
   if(marketFilter!=='all'&&m.market!==marketFilter)continue;
   if(threeOnly&&!(m.operators_compared||[]).includes('SNAI'))continue; // fallback checked below
   if(threeOnly&&(m.operators_compared||[]).length<3)continue;
   if(displayed++>=400)break;
   const tr=document.createElement('tr');
   textCell(tr,event.event);textCell(tr,dt(event.starts_at));textCell(tr,marketLabel(m.market));
   textCell(tr,(m.operators_compared||[]).join(' · '));
   const best=m.best_odds||{};
   const info=Object.entries(best).map(([sel,v])=>`${sel}: ${money(v.odds)} (${v.bookmaker}${v.bookmaker==='BETFAIR'?' delayed':''})`).join(' | ');
   const bestTd=textCell(tr,info);bestTd.className='cmp-best';
   textCell(tr,Number.isFinite(Number(m.inverse_sum))?Number(m.inverse_sum).toFixed(4):'—');
   const margin=Number(m.theoretical_margin_pct);
   textCell(tr,Number.isFinite(margin)?`${money(margin)}%`:'—');
   body.append(tr);
  }
 }
 if(!displayed)empty(body,7,'Nessun mercato soddisfa questi filtri');
 if(displayed>400){const tr=document.createElement('tr');textCell(tr,'Mostrati i primi 400 mercati','empty').colSpan=7;body.append(tr);}
 let layCount=0;
 for(const event of all){
  for(const x of (event.betfair_lay_comparisons||[])){
   if(layCount++>=100)break;
   const tr=document.createElement('tr');
   textCell(tr,event.event);textCell(tr,x.selection);textCell(tr,`${x.back_bookmaker} · ${money(x.back_odds)}`);
   textCell(tr,`Betfair BANCATA · ${money(x.lay_odds)}`);
   textCell(tr,`${money(x.lay_available_eur)} €`);
   textCell(tr,`${money(x.liability_eur)} €`);
   const win=Number(x.profit_if_win_eur),lose=Number(x.profit_if_lose_eur);
   textCell(tr,`${money(win)} € / ${money(lose)} €`);
   layBody.append(tr);
  }
 }
 if(!layCount)empty(layBody,7,'Nessun confronto BACK/LAY nel report attuale');
}

async function refresh(){if(!currentUser)return;const uid=currentUser.id;const [o,r,c,q,k]=await Promise.all([
 sb.from('opportunity_episodes').select('*').eq('owner_id',uid).order('first_seen_at',{ascending:false}).limit(1000),
 sb.from('scanner_runs').select('*').eq('owner_id',uid).order('checked_at',{ascending:false}).limit(100),
 sb.from('notification_contacts').select('*').eq('owner_id',uid).order('created_at',{ascending:false}),
 sb.from('bookmaker_market_snapshots').select('bookmaker,competition_name,competition_key,captured_at,events_count,selections_count').eq('owner_id',uid).order('captured_at',{ascending:false}).limit(250),
 sb.from('universal_comparison_reports').select('captured_at,report').eq('owner_id',uid).maybeSingle()]);
 const error=o.error||r.error||c.error||q.error;if(error){$('system-pill').textContent='● Errore accesso dati';$('system-pill').className='status-pill bad';console.error(error.message);return;}
 opportunities=o.data||[];runs=r.data||[];contacts=c.data||[];marketSources=q.data||[];comparisonReport=k.error?null:(k.data?.report||null);if(k.error)console.warn('Confronto universale: '+k.error.message);render();}
async function setEnabled(c,enabled){const r=await sb.from('notification_contacts').update({enabled}).eq('id',c.id).eq('owner_id',currentUser.id);if(r.error)alert('Salvataggio non riuscito: '+r.error.message);await refresh();}
async function removeContact(c){if(!confirm(`Eliminare ${c.label}?`))return;const r=await sb.from('notification_contacts').delete().eq('id',c.id).eq('owner_id',currentUser.id);if(r.error)alert('Eliminazione non riuscita: '+r.error.message);await refresh();}
function csv(rows){if(!rows.length)return;const keys=['event_name','outcome','status','first_seen_at','last_seen_at','observation_count','snai_odds','betfair_lay','lay_available_eur','back_stake_eur','lay_stake_eur','liability_eur','last_margin_pct','best_margin_pct'];const safe=s=>{let val=String(s??'');if(/^[\s]*[=+@-]/.test(val))val="'"+val;return '"'+val.replaceAll('"','""')+'"';};const content='\uFEFF'+[keys.join(';'),...rows.map(r=>keys.map(k=>safe(r[k])).join(';'))].join('\r\n');const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='quotescope_opportunita_'+new Date().toISOString().slice(0,10)+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function init(){if(!ready){show('setup');return;}sb=createClient(cfg.SUPABASE_URL,cfg.SUPABASE_PUBLISHABLE_KEY,{auth:{persistSession:true,autoRefreshToken:true}});const {data:{session}}=await sb.auth.getSession();await setSession(session);sb.auth.onAuthStateChange((_event,session)=>{setTimeout(()=>setSession(session),0);});}
async function setSession(session){currentUser=session?.user||null;show('login',!currentUser);show('shell',!!currentUser);if(currentUser){nav((location.hash||'#overview').slice(1));await refresh();}}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('login-msg').textContent='Accesso in corso…';try{const {error}=await sb.auth.signInWithPassword({email:$('email').value.trim(),password:$('password').value});$('login-msg').textContent=error?'Accesso non riuscito. Verifica le credenziali.':'';}catch(_e){$('login-msg').textContent='Connessione non disponibile.';}finally{b.disabled=false;}});
$('logout').addEventListener('click',()=>sb.auth.signOut());$('refresh').addEventListener('click',refresh);$('filter-state').addEventListener('change',filterHistory);$('filter-margin').addEventListener('input',filterHistory);$('cmp-market-filter').addEventListener('change',renderUniversalComparison);$('cmp-three-filter').addEventListener('change',renderUniversalComparison);$('download-csv').addEventListener('click',()=>csv(opportunities));$('history-export').addEventListener('click',()=>csv(opportunities));window.addEventListener('hashchange',()=>nav((location.hash||'#overview').slice(1)));
$('contact-form').addEventListener('submit',async e=>{e.preventDefault();const msg=$('contact-msg');const phone=$('contact-phone').value.trim();if(!/^\+[1-9]\d{7,14}$/.test(phone)){msg.textContent='Numero non valido: usa il formato +393331234567.';return;}const row={owner_id:currentUser.id,label:$('contact-name').value.trim(),phone_e164:phone,min_margin_pct:Number($('contact-margin').value),min_confirmations:Number($('contact-confirm').value),consent_confirmed_at:new Date().toISOString(),enabled:true};const {error}=await sb.from('notification_contacts').insert(row);msg.textContent=error?'Impossibile salvare: '+error.message:'Destinatario salvato. SMS disponibili solo dopo configurazione del provider.';if(!error){$('contact-form').reset();await refresh();}});
setInterval(()=>{if(currentUser)refresh();},30000);init().catch(err=>{$('setup').hidden=false;console.error('Initialization error',err.message)});


/* QuoteScope unified UI: 3 user-oriented views. All calculations remain server-side. */
const qsState={key:'roi_pct',direction:-1,page:0,size:70,all:[]};
const qsNorm=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('it-IT').replace(/[^a-z0-9]+/g,' ').trim();
const qsAction=op=>op==='LAY'?'BANCATA':op==='BACK'?'PUNTATA':(op||'—');
const qsName=m=>m==='dc'?'Doppia Chance':m==='1x2_dc'?'1X2 / Doppia Chance':marketLabel(m);
const qsFormatOp=op=>{op=op||{};const risk=op.action==='LAY'?` · responsabilità ${money(op.liability_eur)} €`:'';return `${op.operator||'—'} · ${qsAction(op.action)} ${op.selection||'—'} · quota ${money(op.odds)} · importo ${money(op.stake_eur)} €${risk}`;};
const qsCandidates=()=>Array.isArray(comparisonReport?.two_leg_all_comparisons)?comparisonReport.two_leg_all_comparisons:[];
const qsFresh=(operator)=>{const x=(comparisonReport?.source_status||{})[operator];if(!x||typeof x!=='object')return false;const age=(Date.now()-Date.parse(x.captured_at))/1000;return Number.isFinite(age)&&age>=-60&&age< (operator==='BETFAIR'?180:900);};
function qsResultsFiltered(){
 const q=qsNorm($('qs-all-query').value),market=$('qs-all-market').value,kind=$('qs-all-kind').value;
 return qsCandidates().filter(r=>{
  if(market!=='all'&&r.market!==market)return false;
  if(kind!=='all'&&r.kind!==kind)return false;
  if(!q)return true;
  const data=qsNorm(`${r.event||''} ${r.a?.operator||''} ${r.b?.operator||''} ${r.a?.selection||''} ${r.b?.selection||''}`);
  return q.split(' ').every(token=>data.includes(token));
 }).sort((a,b)=>{
  const k=qsState.key;const av=a[k],bv=b[k];
  if(k==='roi_pct'||k==='min_profit_eur')return qsState.direction*(Number(av)-Number(bv));
  return qsState.direction*String(av||'').localeCompare(String(bv||''),'it-IT',{numeric:true});
 });
}
function qsRenderAll(){
 const rows=qsResultsFiltered();qsState.all=rows;
 $('qs-all-total').textContent=Number(comparisonReport?.two_leg_pairs_evaluated||0).toLocaleString('it-IT');
 $('qs-all-positive').textContent=Number(comparisonReport?.two_leg_positive_count||0).toLocaleString('it-IT');
 const allPresent=Array.isArray(comparisonReport?.two_leg_all_comparisons);
 $('qs-all-note').textContent=allPresent?`Mostrate ${rows.length.toLocaleString('it-IT')} combinazioni su ${qsCandidates().length.toLocaleString('it-IT')} disponibili nel report. Clicca su Data, Evento, Mercato, Profitto o Redditività per ordinare.`:'Attendere il primo report prodotto dallo scanner aggiornato: il vecchio report non contiene tutte le coppie.';
 qsState.page=Math.min(qsState.page,Math.max(0,Math.ceil(rows.length/qsState.size)-1));
 const start=qsState.page*qsState.size,chunk=rows.slice(start,start+qsState.size),body=$('qs-all-body');body.replaceChildren();
 if(!chunk.length){empty(body,8,allPresent?'Nessun confronto corrisponde ai filtri':'Nessun report completo disponibile');}
 for(const r of chunk){
  const tr=document.createElement('tr');
  textCell(tr,dt(r.starts_at));textCell(tr,r.event);textCell(tr,qsName(r.market));
  textCell(tr,qsFormatOp(r.a));textCell(tr,qsFormatOp(r.b));
  textCell(tr,Object.entries(r.scenario_profits_eur||{}).map(([k,v])=>`${k}: ${money(v)} €`).join(' · '));
  const td=textCell(tr,`${money(r.min_profit_eur)} €`);if(r.min_profit_eur>0)td.className='profit';
  const roi=textCell(tr,`${money(r.roi_pct)}%${r.kind==='BACK/LAY'?' · DELAYED':''}`);if(r.roi_pct>0)roi.className='profit';
  body.append(tr);
 }
 $('qs-all-page-info').textContent=rows.length?`${start+1}–${Math.min(rows.length,start+qsState.size)} di ${rows.length}`:'0 risultati';
 $('qs-all-prev').disabled=qsState.page<=0;$('qs-all-next').disabled=start+qsState.size>=rows.length;
}
function qsSelections(){
 const m=$('qs-best-market').value;
 const vals=m==='1x2'?['1','X','2']:m==='dc'?['1X','12','X2']:m==='btts'?['GOAL','NOGOAL']:['OVER','UNDER'];
 const sel=$('qs-best-selection');sel.replaceChildren();
 for(const value of vals){const opt=document.createElement('option');opt.value=value;opt.textContent=value==='NOGOAL'?'No Goal':value;sel.append(opt);}
}
function qsRenderBest(){
 const q=qsNorm($('qs-best-event').value),m=$('qs-best-market').value,sel=$('qs-best-selection').value;
 const body=$('qs-best-body');body.replaceChildren();$('qs-best-winner').replaceChildren();
 const list=Array.isArray(comparisonReport?.best_quote_offers)?comparisonReport.best_quote_offers:[];
 if(!list.length){$('qs-best-note').textContent='Attendere una nuova acquisizione: indice delle quote non ancora disponibile.';empty(body,5,'Nessuna quota disponibile');return;}
 if(!q){$('qs-best-note').textContent='Digita il nome di una partita (anche senza trattino), poi scegli mercato ed esito.';empty(body,5,'Inserire una partita');return;}
 const tokens=q.split(' ').filter(Boolean);
 const rows=list.filter(r=>r.market===m&&r.selection===sel&&tokens.every(t=>qsNorm(r.event).includes(t))&&qsFresh(r.operator)).sort((a,b)=>Number(b.odds)-Number(a.odds));
 $('qs-best-note').textContent=`${rows.length} quote corrispondenti · solo fonti aggiornate · prezzo non garantito`;
 if(!rows.length){empty(body,5,'Nessuna corrispondenza. Verifica il nome della partita o l’aggiornamento delle fonti.');return;}
 const best=rows[0];const host=$('qs-best-winner');
 const title=document.createElement('strong');title.textContent=`Migliore PUNTATA: ${best.operator} @ ${money(best.odds)}`;
 const note=document.createElement('div');note.textContent=`${best.event} · ${qsName(best.market)} · ${best.selection}${best.delayed?' · BETFAIR DELAYED':''}`;
 host.append(title,note);
 for(const r of rows.slice(0,150)){
  const tr=document.createElement('tr');textCell(tr,`${r.event} · ${dt(r.starts_at)}`);textCell(tr,r.operator);
  const v=textCell(tr,money(r.odds));if(r===best)v.className='profit';
  textCell(tr,dt(r.source_captured_at));textCell(tr,r.delayed?'Exchange ritardato':'Puntata bookmaker');body.append(tr);
 }
}
function qsRenderFound(){
 const rows=qsCandidates().filter(r=>Number(r.min_profit_eur)>0).sort((a,b)=>b.roi_pct-a.roi_pct);
 $('qs-found-count').textContent=rows.length.toLocaleString('it-IT');
 $('qs-found-updated').textContent=dt(comparisonReport?.captured_at);
 $('qs-found-note').textContent=rows.length?`${rows.length} combinazioni teoriche positive. Nessuna quota è garantita eseguibile.`:'Nessuna combinazione positiva rilevata nel report attuale.';
 const body=$('qs-found-body');body.replaceChildren();
 if(!rows.length){empty(body,8,'Nessuna opportunità con profitto minimo positivo');return;}
 for(const r of rows.slice(0,200)){
  const tr=document.createElement('tr');textCell(tr,`${r.event} · ${dt(r.starts_at)}`);textCell(tr,qsName(r.market));
  textCell(tr,qsFormatOp(r.a));textCell(tr,qsFormatOp(r.b));
  textCell(tr,`${money(r.risk_capital_eur??r.stakes_total_eur)} €`);
  textCell(tr,`${money(r.min_profit_eur)} €`,'profit');textCell(tr,`${money(r.roi_pct)}%`,'profit');
  const fresh=qsFresh(r.a?.operator)&&qsFresh(r.b?.operator);
  textCell(tr,`${fresh?'Fonti recenti':'DATI NON RECENTI'}${r.kind==='BACK/LAY'?' · Betfair ritardato':''}`);
  body.append(tr);
 }
}
function renderNewPages(){qsRenderAll();qsRenderBest();qsRenderFound();}
function qsExportAll(){
 const rows=qsState.all;if(!rows.length)return;
 const header=['data','evento','mercato','tipo','operazione1','operazione2','profitti','profitto_minimo','redditivita'];
 const safe=v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
 const content='\uFEFF'+[header,...rows.map(r=>[r.starts_at,r.event,qsName(r.market),r.kind,qsFormatOp(r.a),qsFormatOp(r.b),JSON.stringify(r.scenario_profits_eur),r.min_profit_eur,r.roi_pct])].map(row=>row.map(safe).join(';')).join('\r\n');
 const u=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='quotescope_tutti_confronti.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
for(const id of ['qs-all-query','qs-all-market','qs-all-kind'])$(id).addEventListener(id==='qs-all-query'?'input':'change',()=>{qsState.page=0;qsRenderAll();});
for(const th of document.querySelectorAll('[data-qs-sort]'))th.addEventListener('click',()=>{const key=th.dataset.qsSort;qsState.direction=qsState.key===key?-qsState.direction:(key==='roi_pct'||key==='min_profit_eur'?-1:1);qsState.key=key;qsState.page=0;qsRenderAll();});
$('qs-all-prev').addEventListener('click',()=>{qsState.page=Math.max(0,qsState.page-1);qsRenderAll();});
$('qs-all-next').addEventListener('click',()=>{qsState.page++;qsRenderAll();});
$('qs-all-export').addEventListener('click',qsExportAll);
$('qs-best-event').addEventListener('input',qsRenderBest);
$('qs-best-market').addEventListener('change',()=>{qsSelections();qsRenderBest();});
$('qs-best-selection').addEventListener('change',qsRenderBest);
