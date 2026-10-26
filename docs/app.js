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
let sb, currentUser, opportunities=[], runs=[], contacts=[], marketSources=[];

function nav(section){const target=['overview','history','quotes','alerts','system'].includes(section)?section:'overview';for(const name of ['overview','history','quotes','alerts','system'])show(`view-${name}`,name===target);for(const a of document.querySelectorAll('.nav'))a.classList.toggle('active',a.dataset.section===target);$('page-title').textContent={overview:'Panoramica',history:'Storico opportunità',quotes:'Quote bookmaker',alerts:'Notifiche',system:'Stato acquisizione'}[target];}
function prettyState(row){return row.status==='active'?'Attiva':'Conclusa';}
function renderOpRows(id,arr,history=false){const body=$(id);body.replaceChildren();if(!arr.length){empty(body,history?8:6,'Nessuna opportunità registrata per questi filtri');return;}for(const r of arr){const tr=document.createElement('tr');if(history){textCell(tr,r.event_name);textCell(tr,r.outcome);const state=textCell(tr,prettyState(r));state.className='pill '+(r.status==='active'?'':'closed');textCell(tr,dt(r.first_seen_at));textCell(tr,dt(r.last_seen_at));textCell(tr,fmtDur(duration(r)));textCell(tr,`${money(r.best_margin_pct)}%`,'profit');textCell(tr,r.observation_count);}else{textCell(tr,`${r.event_name} · ${r.outcome}`);textCell(tr,money(r.snai_odds));textCell(tr,money(r.betfair_lay));textCell(tr,`${money(r.last_margin_pct)}%`,'profit');textCell(tr,fmtDur(duration(r)));textCell(tr,r.observation_count);}body.append(tr);}}
function renderRecent(arr){const body=$('recent-body');body.replaceChildren();if(!arr.length){empty(body,6,'Nessun episodio concluso');return;}for(const r of arr){const tr=document.createElement('tr');textCell(tr,r.event_name);textCell(tr,r.outcome);textCell(tr,dt(r.first_seen_at));textCell(tr,dt(r.last_seen_at));textCell(tr,fmtDur(duration(r)));textCell(tr,`${money(r.best_margin_pct)}%`,'profit');body.append(tr);}}
function renderChart(closed){const edges=[15,30,60,180,Infinity],labels=['0–14s','15–29s','30–59s','1–3 min','> 3 min'];const counts=[0,0,0,0,0];for(const r of closed){const s=duration(r);counts[edges.findIndex(x=>s<x)]++;}const max=Math.max(...counts,1);const host=$('duration-chart');host.replaceChildren();counts.forEach((num,i)=>{const row=document.createElement('div');row.className='bar-row';const label=document.createElement('span');label.textContent=labels[i];const track=document.createElement('div');track.className='bar-track';const fill=document.createElement('div');fill.className='bar-fill';fill.style.width=`${100*num/max}%`;track.append(fill);const count=document.createElement('span');count.textContent=num;row.append(label,track,count);host.append(row);});}
function render(){renderMarketSources();const now=Date.now(),last24=now-86400000,active=opportunities.filter(r=>r.status==='active'),lastDay=opportunities.filter(r=>Date.parse(r.first_seen_at)>=last24),closed=opportunities.filter(r=>r.status==='closed'&&Date.parse(r.last_seen_at)>=last24);
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

async function refresh(){if(!currentUser)return;const uid=currentUser.id;const [o,r,c,q]=await Promise.all([
 sb.from('opportunity_episodes').select('*').eq('owner_id',uid).order('first_seen_at',{ascending:false}).limit(1000),
 sb.from('scanner_runs').select('*').eq('owner_id',uid).order('checked_at',{ascending:false}).limit(100),
 sb.from('notification_contacts').select('*').eq('owner_id',uid).order('created_at',{ascending:false}),
 sb.from('bookmaker_market_snapshots').select('bookmaker,competition_name,competition_key,captured_at,events_count,selections_count').eq('owner_id',uid).order('captured_at',{ascending:false}).limit(250)]);
 const error=o.error||r.error||c.error||q.error;if(error){$('system-pill').textContent='● Errore accesso dati';$('system-pill').className='status-pill bad';console.error(error.message);return;}
 opportunities=o.data||[];runs=r.data||[];contacts=c.data||[];marketSources=q.data||[];render();}
async function setEnabled(c,enabled){const r=await sb.from('notification_contacts').update({enabled}).eq('id',c.id).eq('owner_id',currentUser.id);if(r.error)alert('Salvataggio non riuscito: '+r.error.message);await refresh();}
async function removeContact(c){if(!confirm(`Eliminare ${c.label}?`))return;const r=await sb.from('notification_contacts').delete().eq('id',c.id).eq('owner_id',currentUser.id);if(r.error)alert('Eliminazione non riuscita: '+r.error.message);await refresh();}
function csv(rows){if(!rows.length)return;const keys=['event_name','outcome','status','first_seen_at','last_seen_at','observation_count','snai_odds','betfair_lay','lay_available_eur','back_stake_eur','lay_stake_eur','liability_eur','last_margin_pct','best_margin_pct'];const safe=s=>{let val=String(s??'');if(/^[\s]*[=+@-]/.test(val))val="'"+val;return '"'+val.replaceAll('"','""')+'"';};const content='\uFEFF'+[keys.join(';'),...rows.map(r=>keys.map(k=>safe(r[k])).join(';'))].join('\r\n');const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='quotescope_opportunita_'+new Date().toISOString().slice(0,10)+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function init(){if(!ready){show('setup');return;}sb=createClient(cfg.SUPABASE_URL,cfg.SUPABASE_PUBLISHABLE_KEY,{auth:{persistSession:true,autoRefreshToken:true}});const {data:{session}}=await sb.auth.getSession();await setSession(session);sb.auth.onAuthStateChange((_event,session)=>{setTimeout(()=>setSession(session),0);});}
async function setSession(session){currentUser=session?.user||null;show('login',!currentUser);show('shell',!!currentUser);if(currentUser){nav((location.hash||'#overview').slice(1));await refresh();}}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('login-msg').textContent='Accesso in corso…';try{const {error}=await sb.auth.signInWithPassword({email:$('email').value.trim(),password:$('password').value});$('login-msg').textContent=error?'Accesso non riuscito. Verifica le credenziali.':'';}catch(_e){$('login-msg').textContent='Connessione non disponibile.';}finally{b.disabled=false;}});
$('logout').addEventListener('click',()=>sb.auth.signOut());$('refresh').addEventListener('click',refresh);$('filter-state').addEventListener('change',filterHistory);$('filter-margin').addEventListener('input',filterHistory);$('download-csv').addEventListener('click',()=>csv(opportunities));$('history-export').addEventListener('click',()=>csv(opportunities));window.addEventListener('hashchange',()=>nav((location.hash||'#overview').slice(1)));
$('contact-form').addEventListener('submit',async e=>{e.preventDefault();const msg=$('contact-msg');const phone=$('contact-phone').value.trim();if(!/^\+[1-9]\d{7,14}$/.test(phone)){msg.textContent='Numero non valido: usa il formato +393331234567.';return;}const row={owner_id:currentUser.id,label:$('contact-name').value.trim(),phone_e164:phone,min_margin_pct:Number($('contact-margin').value),min_confirmations:Number($('contact-confirm').value),consent_confirmed_at:new Date().toISOString(),enabled:true};const {error}=await sb.from('notification_contacts').insert(row);msg.textContent=error?'Impossibile salvare: '+error.message:'Destinatario salvato. SMS disponibili solo dopo configurazione del provider.';if(!error){$('contact-form').reset();await refresh();}});
setInterval(()=>{if(currentUser)refresh();},30000);init().catch(err=>{$('setup').hidden=false;console.error('Initialization error',err.message)});
