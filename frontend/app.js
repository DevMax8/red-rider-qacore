/* QACore interface by Dias, connected to the persistent red_rider API. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {lang:'RU',dark:false,groups:[],group:'',dialogs:[],record:null,selected:'',checklists:[],checklist:'default',editor:null,editorNew:false,settings:null,health:null,busy:false,version:'final',validation:{},modal:null};
  const txt = (ru,kz,en) => state.lang==='KZ'?kz:state.lang==='EN'?en:ru;
  const t = key => I18N[state.lang]?.[key] || I18N.RU[key] || key;
  const number = n => n==null?'—':new Intl.NumberFormat(state.lang==='KZ'?'kk-KZ':state.lang==='EN'?'en-US':'ru-RU',{maximumFractionDigits:2}).format(n);
  const button = (action,label,primary=false,attrs='') => '<button type="button" data-action="'+action+'" class="qa-button '+(primary?'qa-primary':'')+'" '+attrs+'>'+escape(label)+'</button>';
  const statusName = s => ({ok:txt('Оценён','Бағаланды','Evaluated'),pending:txt('Ожидает','Күтілуде','Pending'),error:txt('Ошибка','Қате','Error')})[s]||s;
  const endpoint = (url,params={}) => url+'?'+new URLSearchParams(params).toString();
  function message(error){return typeof error==='string'?error:Array.isArray(error)?error.map(x=>x.msg||String(x)).join('; '):'Request failed';}
  async function api(url,options={}) {
    const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
    if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(message(data?.detail)||('HTTP '+response.status));}
    return response.status===204?null:response.json();
  }
  const post=(url,body)=>api(url,{method:'POST',body:JSON.stringify(body)});
  function toast(text,error=false){const el=document.createElement('div');el.className='qa-toast '+(error?'qa-toast-error':'');el.textContent=text;$('toast-container').append(el);setTimeout(()=>el.remove(),error?10000:5500);}
  async function work(label,fn){
    if(state.busy)return;
    state.busy=true;state.validation={};$('connection-status').textContent=label;$('connection-status').classList.add('qa-running');
    document.querySelectorAll('[data-server-action],#btn-evaluate-all,#btn-save-checklist,#btn-download-dev,#btn-download-prod,#btn-run-validation').forEach(b=>b.disabled=true);
    try{await fn();}catch(error){toast(error.message,true);}finally{state.busy=false;$('connection-status').classList.remove('qa-running');render();}
  }
  function setTab(tab){
    ['workspace','checklist','export'].forEach(name=>{
      $('view-'+name).classList.toggle('hidden',tab!==name);
      $('nav-'+name).classList.toggle('qa-active-tab',tab===name);
    });
    state.tab=tab;
    if(tab==='checklist')renderChecklist();
    if(tab==='export')renderExport();
  }
  function currentChecklist(){return state.checklists.find(c=>c.id===state.checklist)||state.checklists[0];}
  function demoBanner(){return state.group==='DEMO'?'<div class="qa-demo" role="note">'+escape(txt('Демонстрационные примеры: оценки заданы заранее, ИИ не вызывался. Экспорт для сдачи запрещён.','Демо мысалдар: бағалар алдын ала берілген, ИИ шақырылмады. Тапсыруға экспорттауға болмайды.','Demo examples: assessments are predefined; no AI was called. They cannot be submitted.'))+'</div>':'';}
  async function refreshBase(){
    [state.groups,state.checklists,state.settings,state.health]=await Promise.all([api('/api/datasets'),api('/api/checklists'),api('/api/settings'),api('/api/health')]);
    if(!state.checklists.some(c=>c.id===state.checklist))state.checklist=state.checklists[0]?.id||'default';
  }
  async function loadGroup(group,keepId=''){
    state.group=group;state.record=null;state.selected='';state.version='final';state.validation={};
    try{localStorage.setItem('qacore-group',group);}catch{}
    state.dialogs=group?await api(endpoint('/api/conversations',{group_id:group})):[];
    const id=state.dialogs.some(d=>d.conversation_id===keepId)?keepId:state.dialogs[0]?.conversation_id;
    if(id)await loadRecord(id);
  }
  async function loadRecord(id){
    state.record=await api(endpoint('/api/conversations/'+encodeURIComponent(id),{group_id:state.group}));
    state.selected=id;state.version='final';
  }
  async function refreshRecord(){
    const id=state.selected;
    state.dialogs=await api(endpoint('/api/conversations',{group_id:state.group}));
    if(id)await loadRecord(id);
  }
  function renderLabels(){
    const mapping={'hdr-subtitle':'hdrSubtitle','tab-label-workspace':'tabWorkspace','tab-label-checklist':'tabChecklist','tab-label-export':'tabExport','kpi-avg-label':'kpiAvg','kpi-appeals-label':'kpiAppeals','sidebar-title':'sidebarTitle','lbl-eval-all':'evalAll','opt-lang-all':'langAll','opt-st-all':'stAll','opt-st-eval':'stEval','opt-st-pend':'stPend','opt-st-err':'stErr','chk-subheading':'chkSubheading','lbl-normalize':'normalizeBtn','lbl-weight-sum-title':'weightSumTitle','th-crit-title':'thTitle','th-crit-weight':'thWeight','th-crit-type':'thType','th-crit-na':'thNa','exp-heading':'expHeading','exp-subheading':'expSubheading','lbl-run-val':'runValBtn','lbl-copy-json':'copyJsonBtn'};
    for(const [id,key]of Object.entries(mapping))if($(id))$(id).textContent=t(key);
    $('hdr-badge').textContent=(currentChecklist()?.criteria.length||8)+' '+txt('критериев','критерий','criteria');
    $('chk-heading').textContent=txt('Редактор чек-листов','Чек-лист редакторы','Checklist editor');
    $('lbl-reset-chk').textContent=txt('Отменить изменения','Өзгерістерден бас тарту','Discard changes');
    $('lbl-save-chk').textContent=txt('Сохранить чек-лист','Чек-листті сақтау','Save checklist');
    $('th-crit-scale').textContent=txt('Ступени шкалы','Шкала сатылары','Scale steps');
    $('lbl-json-preview').textContent=txt('Предпросмотр результата выбранного диалога — не полный набор','Таңдалған диалогтың нәтижесі — толық жиынтық емес','Selected conversation result preview — not a full dataset');
    $('filter-search').placeholder=t('searchPlaceholder');
    $('lbl-eval-all').textContent=txt('Оценить весь набор','Барлық жиынтықты бағалау','Evaluate full dataset');
    $('btn-demo').textContent=txt('Демо-примеры','Демо мысалдар','Demo examples');
    $('btn-import').textContent=txt('Импорт JSON','JSON импорттау','Import JSON');
    $('btn-fetch').textContent=txt('Загрузить из API','API арқылы жүктеу','Load from API');
    $('btn-settings').textContent=txt('Подключения','Қосылымдар','Connections');
    $('btn-add-criterion').textContent=txt('Добавить критерий','Критерий қосу','Add criterion');
    $('btn-new-checklist').textContent=txt('Новый чек-лист','Жаңа чек-лист','New checklist');
    $('connection-status').textContent=state.busy?txt('Обработка…','Өңделуде…','Processing…'):state.health?.model_configured?txt('AI подключён','ИИ қосылған','AI configured'):txt('AI требует настройки','ИИ баптау қажет','AI setup required');
    const values=state.dialogs.filter(d=>d.status==='ok'&&typeof d.final_score==='number');
    $('kpi-avg-val').textContent=values.length?number(values.reduce((sum,d)=>sum+d.final_score,0)/values.length)+' / 100':'—';
    $('kpi-appeals-val').textContent=state.dialogs.reduce((sum,d)=>sum+(d.appeals_pending||0),0);
    $('filter-dataset').innerHTML=state.groups.length?state.groups.map(g=>'<option value="'+escape(g.group_id)+'" '+(g.group_id===state.group?'selected':'')+'>'+escape(g.group_id)+' ('+g.total_records+')</option>').join(''):'<option value="">'+escape(txt('Нет наборов','Жиынтық жоқ','No datasets'))+'</option>';
    $('active-checklist').innerHTML=state.checklists.map(c=>'<option value="'+escape(c.id)+'" '+(c.id===state.checklist?'selected':'')+'>'+escape(c.name)+' · v'+c.version+'</option>').join('');
    ['kz','ru','en'].forEach(lang=>$('lang-btn-'+lang).classList.toggle('qa-active-language',state.lang.toLowerCase()===lang));
    $('btn-evaluate-all').disabled=state.busy||!state.group||state.group==='DEMO';
    $('global-demo').innerHTML=demoBanner();
  }
  function renderList(){
    const search=$('filter-search').value.toLowerCase();
    const language=$('filter-lang').value,status=$('filter-status').value;
    const rows=state.dialogs.filter(d=>(!search||d.conversation_id.toLowerCase().includes(search))&&(language==='all'||language===d.language)&&(status==='all'||(status==='evaluated'?'ok':status)===d.status));
    $('dialog-list-container').innerHTML=rows.length?rows.map(d=>'<button type="button" data-action="select-dialog" data-id="'+escape(d.conversation_id)+'" class="qa-dialog-card '+(d.conversation_id===state.selected?'qa-selected':'')+'"><div class="qa-row"><strong>'+escape(d.conversation_id)+'</strong><span class="qa-pill '+(d.status==='error'?'qa-danger':d.status==='ok'?'qa-success':'')+'">'+escape(statusName(d.status))+'</span></div><div class="qa-row qa-meta"><span>'+escape(d.language)+' · '+escape(d.channel==='voice call'?t('channelCall'):t('channelChat'))+'</span><strong>'+number(d.final_score)+' / 100</strong></div>'+(d.critical_flag?'<div class="qa-critical">'+escape(txt('Грубое нарушение','Өрескел бұзушылық','Critical violation'))+'</div>':'')+'</button>').join(''):'<div class="qa-empty"><strong>'+escape(txt('Диалогов пока нет','Диалогтар жоқ','No conversations yet'))+'</strong><p>'+escape(txt('Импортируйте исходный JSON, загрузите API или откройте демо.','Бастапқы JSON импорттаңыз, API жүктеңіз немесе демоны ашыңыз.','Import source JSON, load the API or open the demo.'))+'</p></div>';
  }
  function safeAudio(value){if(typeof value!=='string'||!value.trim())return '';try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}}
  function timeSeconds(value){
    if(typeof value!=='string')return null;
    if(/^\d{1,3}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value)){const p=value.split(':').map(Number);return p.reduce((sum,n)=>sum*60+n,0);}
    return null;
  }
  function offsetForTurn(index){
    const transcript=state.record?.conversation.transcript||[],timestamp=transcript[index-1]?.timestamp;
    const relative=timeSeconds(timestamp);if(relative!==null)return relative;
    const stamp=Date.parse(timestamp);const first=transcript.map(t=>Date.parse(t.timestamp)).find(Number.isFinite);
    return Number.isFinite(stamp)&&Number.isFinite(first)?Math.max(0,(stamp-first)/1000):null;
  }
  function evidence(index){
    const target=$('utt-'+index);if(target){document.querySelectorAll('.utterance-highlight').forEach(e=>e.classList.remove('utterance-highlight'));target.classList.add('utterance-highlight');target.scrollIntoView({behavior:'smooth',block:'center'});}
    const audio=$('call-audio'),offset=offsetForTurn(index);
    if(audio&&offset!==null){const seek=()=>{try{audio.currentTime=Number.isFinite(audio.duration)?Math.min(offset,audio.duration):offset;}catch{}};if(audio.readyState)seek();else{audio.addEventListener('loadedmetadata',seek,{once:true});audio.load();}}
  }
  function valueLabel(value,config){return value===null?'N/A':typeof value==='boolean'?txt(value?'Да':'Нет',value?'Иә':'Жоқ',value?'Yes':'No'):number(value)+' / '+number(config?.scale?.at(-1));}
  function renderDetail(){
    const record=state.record;
    if(!record){$('dialog-detail-container').innerHTML='<div class="qa-empty qa-welcome"><div class="qa-welcome-icon">✓</div><h1>QACore AI</h1><p>'+escape(txt('Качество диалогов. Проверяемые доказательства. Сохранённые решения.','Диалог сапасы. Тексерілетін дәлелдер. Сақталған шешімдер.','Conversation quality. Verifiable evidence. Persistent decisions.'))+'</p>'+button('demo',txt('Открыть демо','Демоны ашу','Open demo'),true)+button('import',txt('Импорт JSON','JSON импорттау','Import JSON'))+'</div>';return;}
    const conversation=record.conversation;
    const result=state.version==='original'?record.original_result:record.final_result;
    const configs=record.checklist?.criteria||currentChecklist()?.criteria||[];
    const configMap=new Map(configs.map(c=>[c.criterion_id,c]));
    const initial=record.original_result?.score?.final_score,final=record.final_result?.score?.final_score;
    const hasReview=JSON.stringify(record.original_result)!==JSON.stringify(record.final_result);
    const audio=safeAudio(conversation.audio_url);
    const values=new Map((result?.criteria||[]).map(c=>[c.criterion_id,c.value]));
    const weight=configs.reduce((sum,c)=>sum+(values.get(c.criterion_id)!==null?c.weight:0),0);
    const transcript=conversation.transcript.map((turn,i)=>'<article id="utt-'+(i+1)+'" class="qa-turn '+(turn.speaker==='operator'?'qa-operator':'')+'"><div class="qa-row qa-meta"><strong>#'+(i+1)+' · '+escape(turn.speaker==='operator'?t('speakerOperator'):turn.speaker==='client'?t('speakerClient'):turn.speaker)+'</strong><time>'+escape(turn.timestamp)+'</time></div><p>'+escape(turn.text)+'</p></article>').join('');
    let criteria='';
    if(!result)criteria='<div class="qa-empty">'+escape(t('emptyEval'))+'</div>';
    else if(result.status==='error')criteria='<div class="qa-error"><strong>'+escape(t('errBannerTitle'))+'</strong><p>'+escape(result.error)+'</p></div>';
    else criteria=result.criteria.map((item,i)=>{
      const config=configMap.get(item.criterion_id);
      const original=record.original_result?.criteria?.find(c=>c.criterion_id===item.criterion_id);
      const applicable=item.value!==null&&(config?.weight||0)>0;
      const effective=applicable&&weight?(config.weight/weight*100):0;
      const contribution=applicable?effective*(typeof item.value==='boolean'?(item.value?1:0):item.value/config.scale.at(-1)):0;
      const pending=record.appeals.filter(a=>a.criterion_id===item.criterion_id&&a.status==='pending'&&a.evaluation_id===record.evaluation_id);
      const history=record.appeals.filter(a=>a.criterion_id===item.criterion_id&&a.status==='resolved');
      return '<article class="qa-criterion"><div class="qa-row"><h3><span class="qa-index">'+String(i+1).padStart(2,'0')+'</span> '+escape(config?.label||item.criterion_id)+'</h3><span class="qa-pill '+(item.criterion_id==='critical'?(item.value===true?'qa-danger':'qa-success'):item.value===false?'qa-danger':item.value===null?'':'qa-success')+'">'+escape(valueLabel(item.value,config))+'</span></div><div class="qa-meta qa-weight">'+escape(item.criterion_id==='critical'?txt('Отдельный флаг — без баллов','Жеке белгі — ұпайсыз','Separate flag — unscored'):item.value===null?txt('N/A: вес исключён из расчёта','N/A: салмақ есептен шығарылды','N/A: weight excluded'):txt('Базовый вес','Негізгі салмақ','Base weight')+' '+number(config?.weight)+' · '+txt('активный','белсенді','effective')+' '+number(effective)+'% · '+txt('вклад','үлес','contribution')+' '+number(contribution))+'</div><p>'+escape(item.rationale)+'</p><div class="qa-evidence">'+item.evidence_turns.map(n=>button('evidence','#'+n+' · '+(conversation.transcript[n-1]?.timestamp||''),false,'data-turn="'+Number(n)+'"')).join('')+'</div>'+(state.version==='final'?'<div class="qa-appeal-actions">'+button('appeal',txt('Оспорить оценку','Бағаны дауластыру','Appeal criterion'),false,'data-criterion="'+escape(item.criterion_id)+'"')+pending.map(a=>button('review',txt('Рассмотреть апелляцию','Апелляцияны қарау','Review appeal'),true,'data-appeal="'+escape(a.id)+'"')).join('')+'</div>':'')+(history.length?'<details class="qa-review-history"><summary>'+escape(txt('История решений','Шешімдер тарихы','Decision history'))+' ('+history.length+')</summary>'+history.map(a=>'<p><strong>'+escape(a.decision?.action==='change'?txt('Изменено','Өзгертілді','Changed'):txt('Подтверждено','Расталды','Confirmed'))+':</strong> '+escape(a.decision?.reason)+'</p>').join('')+'</details>':'')+'</article>';
    }).join('');
    $('dialog-detail-container').innerHTML='<div class="qa-detail-header"><div class="qa-row"><div><h1>'+escape(conversation.conversation_id)+'</h1><div class="qa-meta">'+escape(conversation.language)+' · '+escape(conversation.channel)+' · '+conversation.transcript.length+' '+escape(txt('реплик','реплика','turns'))+'</div></div><div class="qa-actions">'+button('refresh',txt('Обновить','Жаңарту','Refresh'))+button('evaluate',result?t('reEval'):t('startEval'),true,'data-server-action '+(state.busy||state.group==='DEMO'?'disabled':''))+'</div></div><div class="qa-score-strip"><div><small>'+escape(t('initialScore'))+'</small><strong>'+number(initial)+' / 100</strong></div><div><small>'+escape(t('finalScore'))+'</small><strong class="'+(final<60?'qa-critical':'qa-positive')+'">'+number(final)+' / 100</strong></div>'+(record.final_result?.score?.critical_flag?'<span class="qa-pill qa-danger">'+escape(txt('Грубое нарушение','Өрескел бұзушылық','Critical violation'))+'</span>':'')+(hasReview?'<div class="qa-actions">'+button('show-original',txt('Исходная','Бастапқы','Original'),false,'aria-pressed="'+(state.version==='original')+'"')+button('show-final',txt('Финальная','Соңғы','Final'),false,'aria-pressed="'+(state.version==='final')+'"')+'</div>':'')+'</div></div><div class="qa-detail-columns"><section class="qa-transcript-panel"><div class="qa-panel-heading"><h2>'+escape(t('transcriptTitle'))+'</h2><p>'+escape(t('transcriptHint'))+'</p></div>'+(audio?'<div class="qa-audio"><audio id="call-audio" controls preload="metadata" src="'+escape(audio)+'"></audio><a href="'+escape(audio)+'" target="_blank" rel="noopener">'+escape(t('audioOpenExt'))+'</a></div>':'')+'<div class="qa-transcript-scroll custom-scroll">'+transcript+'</div></section><section class="qa-results-panel"><div class="qa-panel-heading"><h2>'+escape(txt('Оценка по критериям','Критерийлер бойынша бағалау','Criterion assessment'))+'</h2><p>'+escape(record.checklist?record.checklist.name+' · v'+record.checklist.version:txt('Выбранный чек-лист','Таңдалған чек-лист','Selected checklist'))+'</p></div><div class="qa-results-scroll custom-scroll">'+criteria+(record.model?'<div class="qa-meta qa-model">'+escape(record.model.name)+' · '+escape(record.model.mode)+'</div>':'')+'</div></section></div>';
    $('json-preview-box').textContent=JSON.stringify(record.final_result||{conversation_id:conversation.conversation_id,status:'pending'},null,2);
  }
  function editorFromSelected(){state.editor=structuredClone(currentChecklist());state.editorNew=false;}
  function readEditor(){
    if(!state.editor)return;
    state.editor.name=$('checklist-name').value.trim();
    if(state.editorNew)state.editor.id=$('checklist-id').value.trim();
    document.querySelectorAll('[data-config-index]').forEach(row=>{
      const i=Number(row.dataset.configIndex),c=state.editor.criteria[i];
      for(const key of ['criterion_id','label','description'])c[key]=row.querySelector('[data-field="'+key+'"]').value.trim();
      c.weight=Number(row.querySelector('[data-field="weight"]').value);
      c.answer_type=row.querySelector('[data-field="answer_type"]').value;
      c.allow_na=row.querySelector('[data-field="allow_na"]').checked;
      c.scale=c.answer_type==='scale'?row.querySelector('[data-field="scale"]').value.split(/[,;\s]+/).filter(Boolean).map(Number):[];
    });
  }
  function renderChecklist(){
    if(!state.editor)editorFromSelected();
    const editor=state.editor;if(!editor)return;
    $('checklist-select').innerHTML=state.checklists.map(c=>'<option value="'+escape(c.id)+'" '+(editor.id===c.id?'selected':'')+'>'+escape(c.name)+' · v'+c.version+'</option>').join('');
    $('checklist-name').value=editor.name;$('checklist-id').value=editor.id;$('checklist-id').disabled=!state.editorNew;
    $('checklist-table-body').innerHTML=editor.criteria.map((c,i)=>'<tr data-config-index="'+i+'"><td class="qa-index">'+(i+1)+'</td><td><label>'+escape(txt('Код','Код','ID'))+'<input data-field="criterion_id" value="'+escape(c.criterion_id)+'" required></label><label>'+escape(txt('Название','Атауы','Title'))+'<input data-field="label" value="'+escape(c.label)+'" required></label><label>'+escape(txt('Правило оценки','Бағалау ережесі','Assessment rule'))+'<textarea data-field="description" required>'+escape(c.description)+'</textarea></label></td><td><input aria-label="'+escape(txt('Вес','Салмақ','Weight'))+' '+(i+1)+'" data-field="weight" type="number" min="0" step="0.1" value="'+Number(c.weight)+'"></td><td><select data-field="answer_type" aria-label="'+escape(txt('Тип ответа','Жауап түрі','Answer type'))+' '+(i+1)+'"><option value="boolean" '+(c.answer_type==='boolean'?'selected':'')+'>'+escape(txt('Да / Нет','Иә / Жоқ','Yes / No'))+'</option><option value="scale" '+(c.answer_type==='scale'?'selected':'')+'>'+escape(txt('Шкала','Шкала','Scale'))+'</option></select></td><td><input data-field="scale" aria-label="'+escape(txt('Ступени шкалы','Шкала сатылары','Scale steps'))+' '+(i+1)+'" value="'+escape(c.scale.join(', '))+'" placeholder="0, 5, 10, 15, 20"></td><td><input data-field="allow_na" type="checkbox" aria-label="N/A '+(i+1)+'" '+(c.allow_na?'checked':'')+'></td><td>'+button('delete-criterion','×',false,'data-index="'+i+'" aria-label="'+escape(txt('Удалить критерий','Критерийді жою','Remove criterion'))+' '+(i+1)+'"')+'</td></tr>').join('');
    const total=editor.criteria.reduce((sum,c)=>sum+c.weight,0);$('chk-total-weight-badge').textContent=number(total)+' · '+txt('нормализация до 100','100-ге нормалау','normalized to 100');
    $('chk-weight-bar').innerHTML=editor.criteria.filter(c=>c.weight>0).map((c,i)=>'<span title="'+escape(c.label)+'" style="width:'+(total?c.weight/total*100:0)+'%;background:'+['#6366f1','#3b82f6','#14b8a6','#22c55e','#f59e0b','#f97316','#ec4899','#8b5cf6'][i%8]+'"></span>').join('');
    $('btn-save-checklist').disabled=state.busy;
  }
  function exportGroup(subset){const selector=$('export-'+subset+'-group');return selector?.value||'';}
  function exportOptions(selected){return '<option value="">'+escape(txt('Выберите набор','Жиынтық таңдаңыз','Choose dataset'))+'</option>'+state.groups.filter(g=>g.group_id!=='DEMO').map(g=>'<option value="'+escape(g.group_id)+'" '+(g.group_id===selected?'selected':'')+'>'+escape(g.group_id)+' ('+g.total_records+')</option>').join('');}
  function renderExport(){
    const devCurrent=$('export-dev-group')?.value||state.groups.find(g=>g.group_id==='DEV')?.group_id||'';
    const prodCurrent=$('export-prod-group')?.value||state.groups.find(g=>g.group_id!=='DEV'&&g.group_id!=='DEMO'&&g.total_records===10)?.group_id||'';
    $('validation-cards-grid').innerHTML=['dev','prod'].map(subset=>{
      const selected=subset==='dev'?devCurrent:prodCurrent;
      const saved=state.validation[subset];
      const validation=saved?.group_id===selected?saved:null;
      return '<article class="qa-validation-card"><div class="qa-row"><h3>results/'+subset+'/results.json</h3><strong class="qa-pill '+(validation?.ok?'qa-success':'')+'">'+escape(validation?.ok?txt('Проверено','Тексерілді','Verified'):txt('Не проверено','Тексерілмеген','Not checked'))+'</strong></div><p>'+escape(subset==='dev'?txt('65 диалогов: 60 чатов + 5 звонков','65 диалог: 60 чат + 5 қоңырау','65 conversations: 60 chats + 5 calls'):txt('Ровно 10 контрольных чатов','Дәл 10 бақылау чаты','Exactly 10 control chats'))+'</p><label>'+escape(txt('Исходный набор','Бастапқы жиынтық','Source dataset'))+'<select id="export-'+subset+'-group">'+exportOptions(selected)+'</select></label><p class="'+(validation?.ok?'qa-positive':'qa-meta')+'">'+escape(validation?.message||txt('Проверка выполняется сервером по исходным ID, критериям, доказательствам и модели.','Сервер бастапқы ID, критерий, дәлел және модель бойынша тексереді.','The server checks original IDs, criteria, evidence and model.'))+'</p></article>';
    }).join('');
    $('btn-download-dev').disabled=state.busy;$('btn-download-prod').disabled=state.busy;$('btn-run-validation').disabled=state.busy;
  }
  async function validateExports(){
    const groups={dev:exportGroup('dev'),prod:exportGroup('prod')};
    for(const subset of ['dev','prod']){
      const group=groups[subset];if(!group){state.validation[subset]={ok:false,group_id:group,message:txt('Выберите исходный набор','Бастапқы жиынтықты таңдаңыз','Choose the source dataset')};continue;}
      try{const data=await api(endpoint('/api/results/export',{group_id:group,subset,team_name:state.settings.team_name}));state.validation[subset]={ok:true,group_id:group,message:data.results.length+' / '+(subset==='dev'?65:10)+' · '+data.model.name};}
      catch(error){state.validation[subset]={ok:false,group_id:group,message:error.message};}
    }
    renderExport();
  }
  function render(){renderLabels();renderList();renderDetail();if(state.tab==='checklist')renderChecklist();if(state.tab==='export')renderExport();}
  function closeModal(){$('qa-modal').close();state.modal=null;}
  function modal(title,body,footer,id){
    $('qa-modal').innerHTML='<form id="'+id+'" class="qa-modal-form"><div class="qa-row"><h2>'+escape(title)+'</h2>'+button('close-modal','×',false,'aria-label="'+escape(txt('Закрыть','Жабу','Close'))+'"')+'</div>'+body+'<div class="qa-modal-actions">'+button('close-modal',txt('Отмена','Бас тарту','Cancel'))+'<button type="submit" class="qa-button qa-primary">'+escape(footer)+'</button></div></form>';
    $('qa-modal').showModal();
  }
  function openSettings(){
    const s=state.settings||{};
    modal(txt('Подключения и команда','Қосылымдар және команда','Connections and team'),'<div class="qa-modal-grid"><label>'+escape(txt('Название команды','Команда атауы','Team name'))+'<input name="team_name" value="'+escape(s.team_name||'red_rider')+'" required></label><label>'+escape(txt('Название модели','Модель атауы','Model name'))+'<input name="model_name" value="'+escape(s.model_name||'')+'" placeholder="exact model identifier"></label><label>'+escape(txt('Режим','Режим','Mode'))+'<select name="model_mode"><option value="external_api" '+(s.model_mode!=='local'?'selected':'')+'>External API</option><option value="local" '+(s.model_mode==='local'?'selected':'')+'>Local model</option></select></label><label>'+escape(txt('Базовый URL модели','Модельдің негізгі URL','Model base URL'))+'<input name="model_base_url" type="url" required value="'+escape(s.model_base_url||'https://api.openai.com/v1')+'"></label><label>'+escape(txt('API-ключ модели','Модель API кілті','Model API key'))+'<input type="password" name="model_api_key" autocomplete="new-password" placeholder="'+escape(s.api_key_configured?txt('Сохранён. Пустое поле — оставить','Сақталған. Бос — сақтау','Saved. Leave empty to keep'):txt('Не задан','Берілмеген','Not configured'))+'"></label><label>'+escape(txt('Полная GET-ссылка организаторов','Ұйымдастырушылардың толық GET сілтемесі','Full organizer GET URL'))+'<input type="password" name="dataset_api_url" autocomplete="new-password" placeholder="'+escape(s.dataset_api_configured?txt('Сохранена. Пустое поле — оставить','Сақталған. Бос — сақтау','Saved. Leave empty to keep'):txt('Вставьте подписанную ссылку','Қолтаңбалы сілтемені енгізіңіз','Paste the signed URL'))+'"></label></div><p class="qa-meta">'+escape(txt('Секреты сохраняются только в локальном .env и не возвращаются интерфейсу.','Құпия мәндер тек жергілікті .env файлында сақталады, интерфейске қайтарылмайды.','Secrets stay in the local .env file and are never returned to the interface.'))+'</p>',txt('Сохранить','Сақтау','Save'),'settings-form');
  }
  function openFetch(){modal(txt('Загрузить данные из API','API деректерін жүктеу','Load API dataset'),'<label>group_id<input name="group_id" value="DEV" required></label><p class="qa-meta">'+escape(txt('Для контроля используйте ID, выданный организаторами. Ссылка API задаётся в подключениях.','Бақылау үшін ұйымдастырушылар берген ID қолданыңыз. API сілтемесі қосылымдарда беріледі.','For control data, use the ID provided by organizers. Configure the API URL in Connections.'))+'</p>',txt('Загрузить','Жүктеу','Load'),'fetch-form');}
  function openAppeal(criterionId){
    const result=state.record.final_result,criterion=result.criteria.find(c=>c.criterion_id===criterionId),config=state.record.checklist.criteria.find(c=>c.criterion_id===criterionId);
    state.modal={criterionId};
    modal(txt('Подать апелляцию','Апелляция беру','Submit appeal'),'<div class="qa-info"><strong>'+escape(config.label)+'</strong><p>'+escape(criterion.rationale)+'</p></div><label>'+escape(txt('Комментарий и аргументы','Пікір және дәлелдер','Comment and reasoning'))+'<textarea name="comment" required maxlength="4000"></textarea></label>',txt('Отправить','Жіберу','Submit'),'appeal-form');
    state.modal={criterionId};
  }
  function openReview(appealId){
    const appeal=state.record.appeals.find(a=>a.id===appealId),config=state.record.checklist.criteria.find(c=>c.criterion_id===appeal.criterion_id),criterion=state.record.final_result.criteria.find(c=>c.criterion_id===appeal.criterion_id);
    const allowed=config.answer_type==='boolean'?[true,false]:config.scale.slice();if(config.allow_na)allowed.push(null);
    modal(txt('Решение проверяющего','Тексерушінің шешімі','Reviewer decision'),'<div class="qa-info"><strong>'+escape(config.label)+'</strong><p>'+escape(appeal.comment)+'</p><p>'+escape(criterion.rationale)+'</p>'+criterion.evidence_turns.map(n=>'<p>#'+n+' · '+escape(state.record.conversation.transcript[n-1]?.text)+'</p>').join('')+'</div><label>'+escape(txt('Решение','Шешім','Decision'))+'<select name="decision" id="review-decision"><option value="confirm">'+escape(txt('Подтвердить оценку','Бағаны растау','Confirm assessment'))+'</option><option value="change">'+escape(txt('Изменить оценку','Бағаны өзгерту','Change assessment'))+'</option></select></label><div id="review-change-fields" hidden><label>'+escape(txt('Новое значение','Жаңа мән','New value'))+'<select name="value">'+allowed.map(v=>'<option value="'+escape(JSON.stringify(v))+'" '+(JSON.stringify(v)===JSON.stringify(criterion.value)?'selected':'')+'>'+escape(valueLabel(v,config))+'</option>').join('')+'</select></label><label>'+escape(txt('Обоснование новой оценки','Жаңа бағаның негіздемесі','New assessment rationale'))+'<textarea name="rationale">'+escape(criterion.rationale)+'</textarea></label><label>'+escape(txt('Номера реплик через запятую','Реплика нөмірлері, үтір арқылы','Turn numbers, comma separated'))+'<input name="evidence" value="'+criterion.evidence_turns.join(', ')+'"></label></div><label>'+escape(txt('Причина решения','Шешім себебі','Decision reason'))+'<textarea name="reason" required maxlength="4000"></textarea></label>',txt('Сохранить решение','Шешімді сақтау','Save decision'),'review-form');
    state.modal={appealId,criterionId:appeal.criterion_id};
  }
  async function evaluateOne(){
    if(state.group==='DEMO')throw new Error(txt('Демо нельзя выдавать за результат AI. Импортируйте рабочий набор.','Демоны ИИ нәтижесі ретінде көрсетуге болмайды. Жұмыс жиынтығын импорттаңыз.','Demo data is not an AI result. Import a real dataset.'));
    state.record=await post('/api/conversations/'+encodeURIComponent(state.selected)+'/evaluate',{group_id:state.group,checklist_id:state.checklist});
    await refreshRecord();toast(txt('Оценка завершена. Результат сохранён.','Бағалау аяқталды. Нәтиже сақталды.','Assessment completed and saved.'));
  }
  async function download(subset){
    const group=exportGroup(subset);if(!group)throw new Error(txt('Выберите исходный набор','Бастапқы жиынтықты таңдаңыз','Choose the source dataset'));
    await post('/api/results/save',{group_id:group,subset,team_name:state.settings.team_name});
    const response=await fetch(endpoint('/api/results/export',{group_id:group,subset,team_name:state.settings.team_name}));
    if(!response.ok){const body=await response.json();throw new Error(message(body.detail));}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='results.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast(txt('Сохранено: ','Сақталды: ','Saved: ')+'results/'+subset+'/results.json · commit + push');
    state.validation[subset]={ok:true,group_id:group,message:txt('Сохранено в папку проекта','Жоба папкасына сақталды','Saved to the project folder')};
  }
  document.addEventListener('click',async event=>{
    const node=event.target.closest('[data-action]');if(!node)return;
    const action=node.dataset.action;
    if(action==='close-modal'){closeModal();return;}
    if(action==='settings'){openSettings();return;}
    if(action==='fetch'){openFetch();return;}
    if(action==='import'){$('file-input').click();return;}
    if(action==='evidence'){evidence(Number(node.dataset.turn));return;}
    if(action==='show-original'||action==='show-final'){state.version=action==='show-original'?'original':'final';renderDetail();return;}
    if(action==='appeal'){openAppeal(node.dataset.criterion);return;}
    if(action==='review'){openReview(node.dataset.appeal);return;}
    if(action==='select-dialog'){await work(txt('Загружаем диалог…','Диалог жүктелуде…','Loading conversation…'),async()=>loadRecord(node.dataset.id));return;}
    if(action==='refresh'){await work('Loading…',refreshRecord);return;}
    if(action==='evaluate'){await work(t('loadingEval'),evaluateOne);return;}
    if(action==='demo'){await work('Demo…',async()=>{const demo=await post('/api/demo',{});await refreshBase();await loadGroup(demo.group_id);toast(txt('Открыты явно отмеченные демо-примеры, без вызова AI.','ИИ шақырылмаған, анық белгіленген демо мысалдар ашылды.','Explicitly labeled demonstration examples opened; no AI call.'));});return;}
    if(action==='add-criterion'){readEditor();const n=state.editor.criteria.length+1;state.editor.criteria.push({criterion_id:'criterion_'+n,label:txt('Новый критерий','Жаңа критерий','New criterion'),description:txt('Опишите правило оценки.','Бағалау ережесін жазыңыз.','Describe the assessment rule.'),weight:5,answer_type:'boolean',allow_na:false,scale:[]});renderChecklist();return;}
    if(action==='delete-criterion'){readEditor();state.editor.criteria.splice(Number(node.dataset.index),1);renderChecklist();return;}
    if(action==='new-checklist'){state.editor=structuredClone(currentChecklist());state.editor.id='checklist-'+Date.now();state.editor.name=txt('Новый чек-лист','Жаңа чек-лист','New checklist');state.editor.version=1;state.editorNew=true;renderChecklist();return;}
  });
  document.addEventListener('change',event=>{if(event.target.id==='review-decision')$('review-change-fields').hidden=event.target.value!=='change';const subset=/^export-(dev|prod)-group$/.exec(event.target.id)?.[1];if(subset){delete state.validation[subset];renderExport();}});
  document.addEventListener('submit',async event=>{
    const form=event.target;if(!['settings-form','fetch-form','appeal-form','review-form'].includes(form.id))return;event.preventDefault();
    const data=new FormData(form),context=state.modal;
    await work(txt('Сохраняем…','Сақталуда…','Saving…'),async()=>{
      if(form.id==='settings-form'){
        const payload={team_name:String(data.get('team_name')).trim(),model_name:String(data.get('model_name')).trim(),model_mode:data.get('model_mode'),model_base_url:String(data.get('model_base_url')).trim()};
        for(const field of ['model_api_key','dataset_api_url'])if(String(data.get(field)||'').trim())payload[field]=String(data.get(field)).trim();
        state.settings=await api('/api/settings',{method:'PUT',body:JSON.stringify(payload)});state.health=await api('/api/health');closeModal();toast(txt('Настройки сохранены.','Баптаулар сақталды.','Settings saved.'));
      }else if(form.id==='fetch-form'){const imported=await post('/api/datasets/fetch',{group_id:String(data.get('group_id')).trim()});await refreshBase();await loadGroup(imported.group_id);closeModal();}
      else if(form.id==='appeal-form'){await post('/api/conversations/'+encodeURIComponent(state.selected)+'/appeals',{group_id:state.group,criterion_id:context.criterionId,comment:String(data.get('comment')).trim()});await refreshRecord();closeModal();toast(txt('Апелляция сохранена. Рассмотрите её в карточке критерия.','Апелляция сақталды. Оны критерий карточкасында қараңыз.','Appeal saved. Review it in the criterion card.'));}
      else if(form.id==='review-form'){
        const decision=data.get('decision');let replacement=null;
        if(decision==='change'){
          const evidence=String(data.get('evidence')).split(/[,;\s]+/).filter(Boolean).map(Number);
          if(!evidence.length||evidence.some(n=>!Number.isInteger(n)||n<1))throw new Error(txt('Нужны номера исходных реплик с 1.','Бастапқы реплика нөмірлері 1-ден керек.','Original turn numbers start at 1.'));
          replacement={criterion_id:context.criterionId,value:JSON.parse(data.get('value')),rationale:String(data.get('rationale')).trim(),evidence_turns:evidence};
        }
        const response=await post('/api/appeals/'+encodeURIComponent(context.appealId)+'/decision',{decision,reason:String(data.get('reason')).trim(),replacement});state.record=response.record;await refreshRecord();closeModal();toast(txt('Решение сохранено; исходная оценка сохранена отдельно.','Шешім сақталды; бастапқы баға бөлек сақталды.','Decision saved; the original assessment is retained separately.'));
      }
    });
  });
  $('file-input').addEventListener('change',async event=>{const file=event.target.files?.[0];if(!file)return;await work('Import…',async()=>{if(file.size>20_000_000)throw new Error('JSON exceeds 20 MB');const payload=JSON.parse(await file.text());const imported=await post('/api/datasets/import',{payload});await refreshBase();await loadGroup(imported.group_id);toast(txt('Набор импортирован без изменения ID и реплик.','ID және репликалар өзгертілмей импортталды.','Dataset imported with original IDs and turns.'));});event.target.value='';});
  ['workspace','checklist','export'].forEach(tab=>$('nav-'+tab).addEventListener('click',()=>setTab(tab)));
  ['KZ','RU','EN'].forEach(lang=>$('lang-btn-'+lang.toLowerCase()).addEventListener('click',()=>{if(state.tab==='checklist')readEditor();state.lang=lang;document.documentElement.lang=lang==='KZ'?'kk':lang.toLowerCase();try{localStorage.setItem('qacore-lang',lang);}catch{}render();}));
  $('theme-toggle-btn').addEventListener('click',()=>{state.dark=!state.dark;document.documentElement.classList.toggle('dark',state.dark);$('theme-icon-sun').classList.toggle('hidden',!state.dark);$('theme-icon-moon').classList.toggle('hidden',state.dark);try{localStorage.setItem('qacore-dark',String(state.dark));}catch{}});
  $('filter-search').addEventListener('input',renderList);$('filter-lang').addEventListener('change',renderList);$('filter-status').addEventListener('change',renderList);
  $('filter-dataset').addEventListener('change',event=>work('Loading…',()=>loadGroup(event.target.value)));
  $('active-checklist').addEventListener('change',event=>{state.checklist=event.target.value;state.editor=null;});
  $('checklist-select').addEventListener('change',event=>{state.checklist=event.target.value;editorFromSelected();renderChecklist();renderLabels();});
  $('btn-evaluate-all').addEventListener('click',()=>work(t('loadingEval'),async()=>{const summary=await post('/api/evaluations',{group_id:state.group,checklist_id:state.checklist,retry_errors_only:false});await refreshRecord();toast(txt('Готово: ','Дайын: ','Completed: ')+summary.completed+' / '+summary.total+'; errors: '+summary.failed,summary.failed>0);}));
  $('btn-normalize-weights').addEventListener('click',()=>{readEditor();const total=state.editor.criteria.reduce((sum,c)=>sum+c.weight,0);if(total>0)state.editor.criteria.forEach(c=>c.weight=Number((c.weight/total*100).toFixed(2)));renderChecklist();});
  $('btn-reset-checklist').addEventListener('click',()=>{editorFromSelected();renderChecklist();});
  $('btn-save-checklist').addEventListener('click',()=>work('Saving…',async()=>{readEditor();if(!state.editor.criteria.length)throw new Error('At least one criterion is required');const saved=await api(state.editorNew?'/api/checklists':'/api/checklists/'+encodeURIComponent(state.editor.id),{method:state.editorNew?'POST':'PUT',body:JSON.stringify(state.editor)});state.checklist=saved.id;await refreshBase();state.editor=structuredClone(saved);state.editorNew=false;toast(txt('Конфигурация сохранена. Прошлые оценки не изменены; выберите чек-лист при новой оценке.','Баптау сақталды. Бұрынғы бағалар өзгермеді; жаңа бағалауда чек-листті таңдаңыз.','Configuration saved. Past assessments are unchanged; select it for new assessments.'));}));
  $('btn-run-validation').addEventListener('click',()=>work('Validation…',validateExports));
  $('btn-download-dev').addEventListener('click',()=>work('Export…',()=>download('dev')));
  $('btn-download-prod').addEventListener('click',()=>work('Export…',()=>download('prod')));
  $('btn-copy-json').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('json-preview-box').textContent);toast(t('toastCopied'));}catch{toast(txt('Не удалось скопировать. Выделите JSON вручную.','Көшіру мүмкін емес. JSON-ды қолмен таңдаңыз.','Could not copy. Select the JSON manually.'),true);}});
  async function init(){
    try{const lang=localStorage.getItem('qacore-lang');if(['KZ','RU','EN'].includes(lang))state.lang=lang;state.dark=localStorage.getItem('qacore-dark')==='true';}catch{}
    document.documentElement.classList.toggle('dark',state.dark);$('theme-icon-sun').classList.toggle('hidden',!state.dark);$('theme-icon-moon').classList.toggle('hidden',state.dark);document.documentElement.lang=state.lang==='KZ'?'kk':state.lang.toLowerCase();
    try{await refreshBase();let saved='';try{saved=localStorage.getItem('qacore-group')||'';}catch{}await loadGroup(state.groups.some(g=>g.group_id===saved)?saved:state.groups[0]?.group_id||'');editorFromSelected();setTab('workspace');render();}
    catch(error){$('dialog-detail-container').innerHTML='<div class="qa-error"><h2>'+escape(txt('Нет подключения к серверу','Серверге қосылым жоқ','Server unavailable'))+'</h2><p>'+escape(error.message)+'</p><p>'+escape(txt('Запустите RUN_WINDOWS.cmd и откройте адрес из терминала.','RUN_WINDOWS.cmd іске қосып, терминалдағы адресті ашыңыз.','Run RUN_WINDOWS.cmd and open the address printed in the terminal.'))+'</p></div>';toast(error.message,true);}
  }
  init();
})();
