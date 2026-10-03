'use strict';
const $=id=>document.getElementById(id);
const token=location.hash.slice(1)||sessionStorage.getItem('genesisToken')||'';
if(location.hash){sessionStorage.setItem('genesisToken',token);history.replaceState(null,'',location.pathname)}
let state={},pending=false;
const format=v=>v==null?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:3});
function values(){const text=$('values').value.trim();if(!text)throw Error('Введите числа');const result=text.split(/[\s,;]+/).map(Number);if(result.length<4||result.length>64||result.some(x=>!Number.isFinite(x)||Math.abs(x)>1e40))throw Error('Нужны 4–64 конечных числа в диапазоне ±1e40');return result}
function error(message){$('error').hidden=!message;$('error').textContent=message||''}
function render(s){
 if(s.busy&&s.nodes===undefined){pending=true;$('phase').textContent='Операция выполняется…';controls();return}
 state=s;pending=s.busy;
 $('connection').textContent='● Локальная панель подключена';
 const node=(s.nodes||[]).find(n=>n.endpoint===s.active),binding=node?.binding,cursor=binding?.next_record||0;
 $('phase').textContent=s.busy?'Операция выполняется…':s.active?(cursor===s.total?'Поток завершён':'Ожидает данных'):'Ожидает запуска';
 $('stateDot').className='dot'+(s.active?'':' muted');$('worker').textContent=s.active?(s.active.endsWith('8443')?'WORKER A / STRICT':'WORKER B / STRICT'):'ЯДРО НЕ ВЫБРАНО';
 $('timer').textContent=format(s.elapsed_s)+' с';$('tls').textContent=s.active?'MTLS / ПОДКЛЮЧЕНО':'MTLS / ОЖИДАНИЕ';$('sources').textContent=s.active?'2 доступны':'Выкл.';
 for(const [id,port] of [['nodeA','8443'],['nodeB','8444']]){const el=$(id),n=(s.nodes||[]).find(n=>n.endpoint.endsWith(port));el.classList.toggle('active',!!s.active&&s.active.endsWith(port));el.querySelector('.dot').className='dot'+(n&&!n.error?'':' muted');el.querySelector('.node-state').textContent=n?(n.error?'Нет ответа':(n.status.mode==='paused'?'Приостановлен':n.binding?'Активная связка · '+n.binding.next_record:'Ожидает связки')):'Отключено'}
 $('cursor').textContent=cursor+' / '+(s.total||0);$('progress').max=s.total||4;$('progress').value=cursor;
 const result=s.results?.at(-1)?.result;$('count').textContent=format(result?.count);$('mean').textContent=format(result?.mean);$('mae').textContent=format(s.quality?.mae);$('rmse').textContent=format(s.quality?.rmse);
 if(s.quality)$('qualityNote').textContent='Baseline = '+format(s.quality.baseline)+' · первая половина: '+s.quality.train_count+' записей · holdout: '+s.quality.holdout_count+' записей. Модель не обучалась; targets holdout не использованы для baseline.';
 else $('qualityNote').textContent='MAE/RMSE появятся после завершения. Baseline: среднее первой половины; оценка только на второй половине. Обучение не выполняется.';
 $('manifestHash').textContent=s.passport?.manifest_sha256||'После утверждения';$('runId').textContent=s.run_id?s.run_id.slice(0,8)+' / CORRELATION ID':'НЕТ ЗАПУСКА';
 $('logs').replaceChildren();for(const e of (s.events||[]).slice().reverse()){const div=document.createElement('div');div.className='log';const time=document.createElement('time');time.textContent=e.time;const span=document.createElement('span');span.textContent=e.message;div.append(time,span);$('logs').append(div)}
 if(!s.events?.length){const p=document.createElement('p');p.className='empty';p.textContent='Здесь появятся события запуска и переноса.';$('logs').append(p)}
 error(s.error);controls();
}
function controls(){const active=!!state.active,n=state.nodes?.find(n=>n.endpoint===state.active),cursor=n?.binding?.next_record||0;for(const b of document.querySelectorAll('[data-action]')){const a=b.dataset.action;b.disabled=pending||(a==='start'?(active||!$('approve').checked):!active||(a==='test'&&cursor!==0)||(a==='move'&&!state.active.endsWith('8443'))||((a==='step'||a==='finish')&&cursor>=state.total))}$('values').disabled=active||pending;$('approve').disabled=active||pending}
async function request(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Genesis-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(data.error||'Ошибка запроса');return data}
for(const button of document.querySelectorAll('[data-action]'))button.addEventListener('click',async()=>{try{error(null);const action=button.dataset.action,body={action};if(action==='start'){if(!$('approve').checked)throw Error('Утвердите набор');body.values=values()}pending=true;controls();await request('/api/action',body);await poll()}catch(e){pending=false;error(e.message);controls()}});
$('values').addEventListener('input',()=>{try{$('recordCount').textContent=values().length+' записей / 2 части'}catch(e){$('recordCount').textContent='Проверьте числа'}});$('approve').addEventListener('change',controls);
$('export').addEventListener('click',()=>{const blob=new Blob([JSON.stringify({...state,exported_at:new Date().toISOString()},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='genesis-run-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
async function poll(){try{render(await request('/api/state'))}catch(e){$('connection').textContent='Нет соединения';error(token?e.message:'Откройте адрес с ключом доступа, напечатанный launch.py');controls()}}
controls();poll();setInterval(poll,1500);
