/* Atlas local interaction prototype. All demo state stays in memory. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = (name, cls = '') => `<svg class="icon ${cls}" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  const action = (name, label, glyph, cls = 'secondary', extra = '') => `<button type="button" class="button ${cls}" data-action="${name}" ${extra}>${glyph ? icon(glyph) : ''}${label}</button>`;
  const statusNames = {ready:'可搜尋',processing:'處理中',queued:'等待處理',failed:'處理失敗',archived:'已封存'};
  const badge = status => `<span class="status ${status === 'queued' ? 'processing' : status}"><i></i>${statusNames[status] || esc(status)}</span>`;
  let toastTimer;
  const toast = text => { const el = $('#toast'); el.textContent = text; el.classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('visible'), 3500); };
  const dialog = $('#workspace-dialog');
  let previousFocus;
  const closeDialog = () => { dialog.close(); if (previousFocus?.isConnected) previousFocus.focus(); };
  const openDialog = (title, subtitle, body, footer = '', wide = false) => {
    previousFocus = document.activeElement;
    dialog.classList.toggle('wide', wide);
    $('#dialog-content').innerHTML = `<div class="dialog-heading"><div><h2>${title}</h2><p>${subtitle}</p></div><button class="icon-button" data-action="close" aria-label="關閉面板">${icon('close')}</button></div>${body}${footer ? `<div class="dialog-footer">${footer}</div>` : ''}`;
    if (!dialog.open) dialog.showModal();
  };
  dialog.addEventListener('click', e => { if (e.target === dialog) closeDialog(); });
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-action]');
    if (a?.dataset.action === 'close') closeDialog();
    if (a?.dataset.action === 'menu') $('#sidebar').classList.toggle('open');
  });
  $('#live-search')?.addEventListener('input', e => {
    let count = 0;
    $$('#live-documents tr').forEach(row => { row.hidden = !row.dataset.search.toLowerCase().includes(e.target.value.trim().toLowerCase()); if (!row.hidden) count++; });
    $('#live-empty').hidden = count > 0;
  });
  const app = $('#demo-app');
  if (!app) return;

  const travelContent = '一、適用範圍\n本規定適用於甲公司員工辦理國內公務出差。\n\n二、交通補助\n每日交通補助上限為 NT$1,500，依實際支出核實報銷。超出上限的部分應事先取得主管核准。\n\n三、申請方式\n員工須於出差返回後 7 日內，檢附交通單據與出差申請紀錄提出報銷。\n\n四、注意事項\n單據須載明日期與用途；同一筆支出不得重複申請。';
  const partsContent = '料件清冊｜2026 年第四季\n\n料號 A-1001\n品名：六角螺栓\n規格：M8×30\n材質：不鏽鋼 SUS304\n單位：支\n\n料號 A-1002\n品名：平華司\n規格：M8\n材質：不鏽鋼 SUS304\n單位：片\n\n採購前請依工單確認料號與規格，不得以相近品名替代。';
  const docs = [
    {id:1,title:'國內出差與交通補助規定',category:'作業規定',type:'PDF',size:'248 KB',v:3,visibility:'company',readers:[],status:'ready',date:'2026/10/20',content:travelContent,color:'sand',star:true},
    {id:2,title:'料件清冊｜2026 Q4',category:'料件清冊',type:'TXT',size:'36 KB',v:2,visibility:'private',readers:['reader'],status:'ready',date:'2026/10/19',content:partsContent,color:'teal',star:false},
    {id:3,title:'設備例行保養作業手冊',category:'技令手冊',type:'PDF',size:'1.8 MB',v:1,visibility:'company',readers:[],status:'ready',date:'2026/10/18',content:'設備例行保養作業手冊\n\n一、每日作業前\n確認電源與安全護罩完整，目視檢查是否有鬆脫、滲漏或異音。\n\n二、每週保養\n依點檢表清潔設備表面與散熱區域，並記錄保養日期。\n\n三、異常處置\n發現設備異常時立即停止使用並通知設備負責人，未完成確認前不得恢復運轉。',color:'blue',star:true},
    {id:4,title:'採購申請與驗收作業流程',category:'作業規定',type:'PDF',size:'520 KB',v:2,visibility:'private',readers:['reader','staff'],status:'ready',date:'2026/10/17',content:'採購申請與驗收作業流程\n\n一、提出需求\n申請人填寫用途、料號、數量及預估金額，送交直屬主管審核。\n\n二、採購與驗收\n採購完成後，驗收人依申請內容逐項核對料號、規格與數量。\n\n三、紀錄保存\n驗收紀錄與相關憑證依規定保存，差異項目須完成紀錄後再辦理後續作業。',color:'sand',star:false},
    {id:5,title:'新進人員教育訓練手冊',category:'技令手冊',type:'PDF',size:'2.4 MB',v:1,visibility:'private',readers:[],status:'processing',date:'2026/10/20',content:'新進人員教育訓練手冊\n\n到職後請完成安全訓練、設備操作說明與工作環境介紹。實際操作前須經指導人員確認。',color:'blue',star:false},
    {id:6,title:'出差補助規定｜2025 舊制',category:'作業規定',type:'PDF',size:'186 KB',v:1,visibility:'private',readers:[],status:'archived',date:'2026/09/30',content:'已封存的舊規定。',color:'sand',star:false},
  ];
  const members = [
    {id:'owner',name:'林以安',role:'一般員工',relation:'文件擁有者',active:true,color:''},
    {id:'reader',name:'陳品妤',role:'一般員工',relation:'直屬主管：王柏鈞',active:true,color:'blue'},
    {id:'staff',name:'許哲維',role:'一般員工',relation:'直屬主管：王柏鈞',active:true,color:'green'},
    {id:'manager',name:'王柏鈞',role:'一般員工',relation:'主管關係',active:true,color:'rose'},
    {id:'admin',name:'張雅筑',role:'公司管理員',relation:'帳號管理',active:true,color:'blue'},
    {id:'inactive',name:'黃庭安',role:'一般員工',relation:'帳號已停用',active:false,color:''},
  ];
  let transfers = [{id:'TR-2026-008',doc:2,to:'reader',reason:'職務調整，移交料件資料維護責任。',status:'待審核'}];
  const audit = [{title:'更新出差補助規定',detail:'第 3 版已完成處理並成為有效版本',time:'今天 09:42'}, {title:'授予版本閱讀權限',detail:'陳品妤可閱讀料件清冊第 2 版',time:'昨天 16:20'}, {title:'完成文件解析',detail:'料件清冊已可供授權檢索',time:'昨天 16:12'}];
  const historyEvents = [];
  const state = {view:'documents',tab:'all',category:'全部分類',query:'',grid:false,review:false,question:'',answer:null,selectedFile:null,memberQuery:''};
  const viewNames = {documents:'文件知識庫',ask:'知識問答',processing:'文件處理',transfers:'文件交接',history:'歷史調閱',members:'成員管理',usage:'用量與預算',guide:'使用指南'};
  const findDoc = id => docs.find(d => d.id === Number(id));
  const memberName = id => members.find(m => m.id === id)?.name || id;
  const title = (label,en,description,buttons = '') => `<div class="page-heading"><div><div class="eyebrow">${en}</div><h1>${label}<span class="heading-dot">.</span></h1><p>${description}</p></div>${buttons}</div>`;
  const note = (text,detail,link = '') => `<div class="bottom-note">${icon('shield')}<div><strong>${text}</strong><p>${detail}</p></div>${link}</div>`;
  const stat = (label,value,unit,glyph,detail,color='') => `<div class="stat-card"><span class="stat-icon ${color}">${icon(glyph)}</span><div><p>${label}</p><div class="stat-value">${value}<small>${unit}</small></div><div class="stat-note">${detail}</div></div></div>`;
  const access = d => `<span class="access-label">${icon(d.visibility === 'company' ? 'users' : 'lock')}${d.visibility === 'company' ? '本公司可讀' : d.readers.length ? '指定人員' : '僅自己'}</span>`;
  const avatar = (id) => { const m = members.find(m => m.id === id); return `<span class="avatar ${m?.color || ''}">${esc(m?.name.slice(-1) || '安')}</span>`; };
  const rows = () => docs.filter(d => {
    const tabOk = state.tab === 'archived' ? d.status === 'archived' : d.status !== 'archived' && (state.tab === 'starred' ? d.star : state.tab === 'shared' ? d.visibility === 'company' || d.readers.length > 0 : true);
    return tabOk && (state.category === '全部分類' || d.category === state.category) && (d.title + d.category).toLowerCase().includes(state.query.toLowerCase());
  });
  const rowHtml = d => `<tr><td><button class="file-link" data-action="document" data-id="${d.id}"><span class="file-icon ${d.color}">${icon('file')}</span><span><strong>${esc(d.title)}</strong><small>${d.type} <span class="footer-dot">·</span> ${d.size}</small></span></button></td><td><span class="category-label">${d.category}</span></td><td>${access(d)}</td><td><span class="version-label">v${d.v}</span></td><td>${badge(d.status)}</td><td>${d.date}</td><td><button class="icon-button" data-action="document" data-id="${d.id}" aria-label="開啟 ${esc(d.title)}">${icon('arrow')}</button></td></tr>`;
  const cardHtml = d => `<button class="doc-card" data-action="document" data-id="${d.id}"><div class="doc-card-top"><span class="file-icon ${d.color}">${icon('file')}</span>${badge(d.status)}</div><span class="category-label">${d.category}</span><h3>${esc(d.title)}</h3><p>${d.type} · ${d.size} · v${d.v}</p><div class="doc-card-bottom">${access(d)}<span class="subtle">${d.date}</span></div></button>`;
  function resultHtml() {
    const result = rows();
    if (!result.length) return `<div class="empty-state">${icon('search')}<h3>沒有找到符合條件的文件</h3><p>試試其他關鍵字，或調整分類與閱讀範圍。</p>${action('clear-filters','清除篩選','filter','secondary','style="margin-top:17px"')}</div>`;
    if (state.grid) return `<div class="cards-grid">${result.map(cardHtml).join('')}</div>`;
    return `<div class="table-scroll"><table class="document-table"><thead><tr><th>文件名稱</th><th>分類</th><th>閱讀範圍</th><th>版本</th><th>處理狀態</th><th>更新時間</th><th></th></tr></thead><tbody>${result.map(rowHtml).join('')}</tbody></table></div>`;
  }
  function documentsView() {
    const active = docs.filter(d => d.status !== 'archived');
    return title('文件知識庫','KNOWLEDGE LIBRARY','把散落的資訊，整理成團隊可以信任的知識。',action('upload','上傳文件','plus','primary')) +
      `<section class="ask-banner"><div class="banner-symbol">${icon('sparkles')}</div><div><span class="mini-label">讓知識，成為答案</span><h2>少一點翻找，多一點專注。</h2><p>用一句話提問，從你的知識庫中找到有憑有據的答案。</p></div><a class="button banner-button" href="#ask">開始知識問答${icon('arrow')}</a><div class="banner-art" aria-hidden="true"><div></div><div></div><div></div></div></section>` +
      `<section class="stats-row" aria-label="知識庫概況">${stat('知識庫文件',active.length,'份','folder','<b>3</b> 種業務分類')}${stat('已可搜尋',active.filter(d=>d.status === 'ready').length,'份','check','依目前有效版本檢索')}${stat('處理中',active.filter(d=>d.status !== 'ready').length,'份','activity','解析完成後可供搜尋','sand')}${stat('本月模型用量','84.20','/ NT$300','chart','已使用預算 <b>28.1%</b>','blue')}</section>` +
      `<div class="section-heading"><h2>我的文件 <span class="count-pill">${active.length}</span></h2><div class="view-switch"><button aria-label="清單檢視" class="${!state.grid?'active':''}" data-action="list-view">${icon('list')}</button><button aria-label="卡片檢視" class="${state.grid?'active':''}" data-action="grid-view">${icon('grid')}</button></div></div><section class="library-panel"><div class="library-toolbar"><label class="search-field">${icon('search')}<input id="demo-search" type="search" placeholder="搜尋文件名稱、分類…" value="${esc(state.query)}" aria-label="搜尋文件"></label><div class="toolbar-end"><select id="demo-category" class="select-field" aria-label="依分類篩選">${['全部分類','作業規定','技令手冊','料件清冊'].map(c=>`<option ${state.category===c?'selected':''}>${c}</option>`).join('')}</select>${action('sort','最新更新','filter','secondary')}</div></div><div class="tabs" role="tablist" aria-label="文件範圍">${[['all','全部文件',active.length],['starred','已收藏',active.filter(d=>d.star).length],['shared','已分享',active.filter(d=>d.visibility==='company'||d.readers.length).length],['archived','已封存',docs.filter(d=>d.status==='archived').length]].map(([v,n,c])=>`<button class="tab ${state.tab===v?'active':''}" role="tab" aria-selected="${state.tab===v}" data-action="tab" data-tab="${v}">${n}<span>${c}</span></button>`).join('')}</div><div id="document-results">${resultHtml()}</div><div class="table-footer"><span id="result-count">顯示 ${rows().length} 份文件</span><span>${icon('lock')}僅包含本公司與已授權的內容</span><div class="pagination"><button aria-label="上一頁" disabled>${icon('chevron','flip')}</button><button class="current" aria-label="第 1 頁">1</button><button aria-label="下一頁" disabled>${icon('chevron')}</button></div></div></section>` +
      note('每一份知識，都有恰當的閱讀範圍。','公司公開、指定人員或私人文件，只讓對的人看見對的內容。','<a href="#guide">了解權限機制'+icon('arrow')+'</a>');
  }
  function askView() {
    return `<div class="ask-workspace"><div class="ask-welcome"><span class="eyebrow">ANSWERS WITH EVIDENCE</span><div class="ask-emblem">${icon('sparkles')}</div><h1>你的問題，知識庫有線索。</h1><p>從已授權的文件中尋找答案，每個引用都能回到來源。</p></div><form id="question-form" class="ask-composer"><label class="sr-only" for="question">你的問題</label><textarea id="question" name="question" placeholder="例如：國內出差的交通補助上限是多少？" required maxlength="1000">${esc(state.question)}</textarea><div class="composer-footer"><span>${icon('shield')}只使用本公司、有效且已授權的版本</span><button class="button primary" type="submit">開始查詢${icon('arrow')}</button></div></form><div class="suggestions">${[['book','國內出差的交通補助上限是多少？'],['file','A-1001 的規格與單位是什麼？'],['activity','設備開始作業前要檢查什麼？']].map(([i,q])=>`<button class="suggestion" data-action="suggestion" data-question="${q}">${icon(i)}<p>${q}</p></button>`).join('')}</div><div class="ask-explainer"><span>${icon('search')}授權檢索</span>${icon('chevron')}<span>${icon('sparkles')}依證據回答</span>${icon('chevron')}<span>${icon('check')}核對來源</span></div><div id="answer-result">${answerHtml()}</div><p class="section-caption" style="text-align:center;font-size:9px;margin-top:28px">互動展示使用預設範例回答，不連線模型、不產生費用；每次查詢獨立，不帶入前文。</p></div>`;
  }
  function answerHtml() {
    if (!state.answer) return '';
    const a = state.answer;
    if (a.doc && (findDoc(a.doc).status !== 'ready' || findDoc(a.doc).v !== a.version)) {
      return `<section class="answer-panel animation-in"><div class="answer-header">${icon('shield')}來源狀態已變更</div><p class="section-caption">引用的版本已被替換或封存，因此先前回答已停止顯示。請重新查詢目前有效的證據。</p></section>`;
    }
    return `<section class="answer-panel animation-in"><div class="answer-question">${esc(a.question)}</div><div class="answer-header">${icon(a.doc?'sparkles':'shield')}<strong>${a.doc?'根據文件的範例回答':'目前沒有足夠的證據'}</strong>${a.doc?'<span class="status ready"><i></i>附來源</span>':''}</div><div class="answer-text">${esc(a.text)}</div>${a.doc?`<div class="answer-sources"><p>參考來源 · 1 份文件</p><button class="source-card" data-action="source" data-id="${a.doc}"><span class="source-index">1</span><span>${esc(findDoc(a.doc).title)}<small>第 ${findDoc(a.doc).v} 版 · ${a.location}</small></span>${icon('external')}</button></div>`:''}<p class="answer-foot">${a.doc?'這是預設示範資料的回答，引用內容可點開核對。':'不使用未授權文件，也不以常識補足缺少的公司規定。'}</p></section>`;
  }
  function answerQuestion(question) {
    state.question = question.trim();
    let doc, text, location;
    if (state.question === '國內出差的交通補助上限是多少？') {doc=1;location='第 2 頁 · 交通補助';text='甲公司員工國內出差，每日交通補助上限為 NT$1,500，依實際支出核實報銷。\n\n請於返回後 7 日內，檢附交通單據與出差申請紀錄提出報銷。超出上限的支出，須事先取得主管核准。[1]';}
    else if (state.question === 'A-1001 的規格與單位是什麼？') {doc=2;location='第 1 段 · 料號 A-1001';text='料號 A-1001 是六角螺栓：\n\n・規格：M8×30\n・材質：不鏽鋼 SUS304\n・計量單位：支\n\n採購時應以料號與規格核對，不要只依相近品名替代。[1]';}
    else if (state.question === '設備開始作業前要檢查什麼？') {doc=3;location='第 1 頁 · 每日作業前';text='開始作業前，應確認電源與安全護罩完整，並目視檢查是否有鬆脫、滲漏或異音。\n\n若發現異常，立即停止使用並通知設備負責人，未完成確認前不得恢復運轉。[1]';}
    const originalContents = {1:travelContent,2:partsContent,3:'設備例行保養作業手冊\n\n一、每日作業前\n確認電源與安全護罩完整，目視檢查是否有鬆脫、滲漏或異音。\n\n二、每週保養\n依點檢表清潔設備表面與散熱區域，並記錄保養日期。\n\n三、異常處置\n發現設備異常時立即停止使用並通知設備負責人，未完成確認前不得恢復運轉。'};
    if (doc && (findDoc(doc).status !== 'ready' || findDoc(doc).content !== originalContents[doc])) doc = null;
    state.answer = {question:state.question,doc,version:doc?findDoc(doc).v:null,text:doc?text:'目前的有效文件沒有這個問題可用的預設證據回答。請點選上方範例體驗引用流程；文件已封存或內容變更時，展示不會沿用舊證據。',location};
    $('#answer-result').innerHTML = answerHtml();
    $('#answer-result').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function processingView() {
    const jobs = docs.filter(d=>d.status === 'processing' || d.status === 'queued' || d.pending || d.status === 'failed');
    return title('文件處理','DOCUMENT PIPELINE','從原始文件到可搜尋的知識，每一步都有跡可循。',action('upload','上傳文件','plus','primary')) +
      `<div class="stats-row">${stat('進行中的工作',jobs.length,'項','activity','尚未完成不會進入搜尋','sand')}${stat('已可搜尋',docs.filter(d=>d.status==='ready').length,'份','check','僅使用已完成的有效版本')}${stat('失敗的工作',docs.filter(d=>d.status==='failed').length,'項','clock','失敗保留紀錄，可重新處理')}${stat('重複付費','0','次','shield','展示不會呼叫付費服務','blue')}</div><div class="split-grid"><section class="panel"><h2>目前工作 <span class="count-pill">${jobs.length}</span></h2>${jobs.length?jobs.map(d=>`<div class="job-item"><div class="section-heading"><div class="file-link"><span class="file-icon ${d.color}">${icon('file')}</span><span><strong>${esc(d.title)}</strong><small>${d.pending?'新版 v'+(d.v+1)+' 處理中 · 目前 v'+d.v+' 繼續可讀':'第 '+d.v+' 版 · 等待成為可搜尋文件'}</small></span></div>${badge(d.status==='ready'?'processing':d.status)}</div><div class="steps">${['檔案驗證','解析清洗','文字切塊','建立向量','完成索引'].map((s,i)=>`<div class="step ${i<2?'done':i===2?'active':''}">${s}<span>${i<2?'已完成':i===2?'處理中':'等待'}</span></div>`).join('')}</div><div class="section-heading"><span class="subtle">本次展示以按鈕模擬工作完成</span>${action('complete','模擬完成處理','check','secondary',`data-id="${d.id}"`)}</div></div>`).join('<hr style="border:0;border-top:1px solid var(--line);margin:23px 0">'):`<div class="empty-state">${icon('check')}<h3>目前沒有待處理工作</h3><p>所有展示文件已完成處理。</p></div>`}</section><aside class="panel"><h2>最近動態</h2>${audit.map(a=>`<div class="activity-item"><span class="activity-dot"></span><div><strong>${esc(a.title)}</strong><p>${esc(a.detail)}</p><small>${a.time}</small></div></div>`).join('')}</aside></div>` + note('處理完成，才進入知識庫。','新版處理中時，原有效版本繼續提供服務；完成後才切換到新版。');
  }
  function transfersView() {
    return title('文件交接','OWNERSHIP HANDOVER','明確的責任移轉，讓知識不因人員變動而中斷。',action('transfer-new','提出交接','plus','primary'))+
      `<div class="inline-note" style="margin-top:0">${icon('transfer')}交接須由直屬主管核准。既有閱讀權保留，歷史版本須明確列入交接範圍。</div><section class="library-panel"><div class="tabs"><button class="tab ${!state.review?'active':''}" data-action="transfer-mine">我的交接 <span>${transfers.length}</span></button><button class="tab ${state.review?'active':''}" data-action="transfer-review">主管審核情境 <span>${transfers.filter(t=>t.status==='待審核').length}</span></button></div>${state.review?'<div class="library-toolbar"><span class="subtle">主管只見申請人、接收人與原因；不顯示文件名稱、摘要或內容。</span></div>':''}<div class="table-scroll"><table class="document-table"><thead><tr><th>申請編號${!state.review?' / 文件':''}</th><th>原擁有者</th><th>接收人</th><th>狀態</th><th>申請日期</th><th></th></tr></thead><tbody>${transfers.map(t=>`<tr><td><strong style="color:#6d805c;font-weight:500">${t.id}</strong>${!state.review?`<div class="subtle" style="display:block;margin-top:3px">${esc(findDoc(t.doc).title)} · v${findDoc(t.doc).v}</div>`:''}</td><td>林以安</td><td>${memberName(t.to)}</td><td><span class="status ${t.status==='待審核'?'processing':t.status==='已核准'?'ready':'archived'}"><i></i>${t.status}</span></td><td>2026/10/20</td><td>${action('transfer-detail',state.review?'檢視申請':'查看詳情','chevron','secondary',`data-id="${t.id}"`)}</td></tr>`).join('')}</tbody></table></div></section>` + note('核准前，再確認一次。','主管不能核准自己接收；帳號、主管關係或擁有者狀態變動時，須重新確認。');
  }
  function historyView() {
    return title('歷史調閱','VERSION ARCHIVE','只查看獨立授權的歷史版本，每次調閱都留下紀錄。') +
      `<div class="inline-note" style="margin-top:0">${icon('lock')}新版閱讀權不包含歷史調閱權。以下僅列出已授予你的獨立歷史權限。</div><section class="library-panel"><div class="library-toolbar"><strong style="font-size:12px">已授權的歷史版本</strong><span class="subtle">調閱者：林以安</span></div><div class="table-scroll"><table class="document-table"><thead><tr><th>文件 / 版本</th><th>版本擁有者</th><th>授權範圍</th><th>生效日期</th><th></th></tr></thead><tbody><tr><td><div class="file-link"><span class="file-icon sand">${icon('history')}</span><span><strong>國內出差與交通補助規定</strong><small>第 2 版 · 歷史版本</small></span></div></td><td>林以安</td><td><span class="status ready">獨立歷史授權</span></td><td>2026/10/12</td><td>${action('history-read','調閱並記錄','external','secondary')}</td></tr></tbody></table></div></section><div class="panel" style="margin-top:22px"><h2>調閱紀錄</h2><div id="history-log">${historyLogHtml()}</div></div>`;
  }
  function historyLogHtml() {
    return historyEvents.length ? historyEvents.map(time => `<div class="activity-item"><span class="activity-dot"></span><div><strong>林以安 · 調閱出差補助規定 v2</strong><p>獨立歷史授權已確認</p><small>本次展示 ${esc(time)}</small></div></div>`).join('') : '<p>本次展示尚無調閱紀錄。開啟歷史版本後，紀錄會顯示在這裡。</p>';
  }
  function membersView() {
    return title('成員管理','TEAM MEMBERS','管理本公司的帳號狀態，讓存取權與人員狀態保持一致。','<span class="status processing">管理員情境 · 示範操作</span>') +
      `<div class="stats-row">${stat('本公司成員',members.length,'位','users','使用預建帳號')}${stat('已啟用',members.filter(m=>m.active).length,'位','check','可依授權使用知識庫')}${stat('已停用',members.filter(m=>!m.active).length,'位','lock','停用後下一次請求拒絕','sand')}${stat('公司管理員','1','位','shield','不自動獲得文件閱讀權','blue')}</div><section class="library-panel"><div class="library-toolbar"><strong style="font-size:12px">甲公司成員</strong><span class="subtle">公司、角色與主管關係為固定資料</span></div><div class="table-scroll"><table class="document-table"><thead><tr><th>成員</th><th>角色</th><th>主管 / 關係</th><th>帳號狀態</th><th>啟用</th></tr></thead><tbody>${members.map(m=>`<tr><td><div class="member-name">${avatar(m.id)}<span><strong>${m.name}</strong><small>a.${m.id}</small></span></div></td><td>${m.role}</td><td>${m.relation}</td><td><span class="status ${m.active?'ready':'archived'}"><i></i>${m.active?'已啟用':'已停用'}</span></td><td><button class="toggle ${m.active?'on':''}" data-action="toggle-member" data-id="${m.id}" role="switch" aria-checked="${m.active}" aria-label="${m.active?'停用':'啟用'} ${m.name}" ${m.id==='admin'?'disabled title="展示中保留管理員帳號"':''}></button></td></tr>`).join('')}</tbody></table></div></section>`+note('帳號管理，不等於文件管理。','管理員不能冒用身分、修改主管或角色，也不會接管停用成員的文件。');
  }
  function usageView() {
    const heights=[18,35,23,56,31,72,36,49,28,63,41,82,52,32,65,47,91,58,72,45];
    return title('用量與預算','USAGE & BUDGET','每一次模型呼叫都有紀錄，每一分預算都有上限。','<span class="category-label">2026 年 10 月 · 示範帳務</span>') +
      `<div class="split-grid"><section class="panel"><div class="section-heading"><h2>本月用量</h2><span class="status ready"><i></i>預算內</span></div><div class="usage-total">NT$84.20 <small>/ NT$300</small></div><p>每月固定上限；預算不足時停止付費功能。</p><div class="budget-meter"><span></span></div><div class="budget-labels"><span>已結算 28.1%</span><span>可用 NT$211.60 · 預留 NT$4.20</span></div><h3 style="margin-top:30px">每日用量</h3><div class="bar-chart" role="img" aria-label="10 月 1 日至 20 日的示範每日用量長條圖">${heights.map((h,i)=>`<div class="chart-column"><div class="chart-bar" style="height:${h}%" title="10/${i+1} 示範相對用量 ${h}"></div>${i%3===0?`<small>10/${i+1}</small>`:''}</div>`).join('')}</div><div class="usage-legend">示範用量分布 · 所有金額為虛構資料</div></section><aside class="panel"><h2>用量控制</h2><div class="activity-item"><span class="activity-dot"></span><div><strong>呼叫前預留費用</strong><p>所有工作共用預算，並行呼叫也須先預留。</p></div></div><div class="activity-item"><span class="activity-dot"></span><div><strong>不超出 NT$300 月上限</strong><p>餘額不足時停止，不自動加價或換用昂貴模型。</p></div></div><div class="activity-item"><span class="activity-dot"></span><div><strong>失敗與重試仍可追溯</strong><p>記錄預留、結算與釋放，不重複計帳。</p></div></div><div class="inline-note">${icon('chart')}這個介面展示不會呼叫模型，因此本次操作沒有實際費用。</div></aside></div><section class="library-panel" style="margin-top:23px"><div class="library-toolbar"><strong style="font-size:12px">近期呼叫紀錄</strong><span class="subtle">模型用量示範</span></div><div class="table-scroll"><table class="document-table"><thead><tr><th>時間</th><th>用途</th><th>模型</th><th>Token</th><th>結算</th><th>狀態</th></tr></thead><tbody><tr><td>10/20 09:45</td><td>證據式問答</td><td>回答模型（示範）</td><td>2,140</td><td>NT$0.18</td><td>${badge('ready')}</td></tr><tr><td>10/20 09:42</td><td>建立文件向量</td><td>Embedding（示範）</td><td>18,600</td><td>NT$0.12</td><td>${badge('ready')}</td></tr></tbody></table></div></section>`;
  }
  function guideView() {
    return title('把知識用得更好','YOUR WORKSPACE, EXPLAINED','從一份文件開始，建立可信任的團隊知識流程。') +
      `<div class="guide-grid">${[['01','整理文件','上傳文字型 PDF 或 UTF-8 TXT。新文件預設私人，處理完成後才能進入搜尋。','processing','查看處理流程'],['02','找到有來源的答案','每次查詢只檢索目前有效且已授權的版本。答案附上來源；證據不足時保留拒答。','ask','體驗知識問答'],['03','讓責任順利交接','指定接收人與版本範圍，由直屬主管審核。移轉管理責任時保留既有閱讀權。','transfers','查看交接流程']].map(([n,h,p,link,label])=>`<section class="guide-card"><div class="guide-number">${n}</div><h2>${h}</h2><p>${p}</p><a class="text-button" href="#${link}">${label}${icon('arrow')}</a></section>`).join('')}</div><section class="panel" style="margin-top:23px"><h2>你看到的，是哪個環境？</h2><p>目前是使用虛構資料的互動展示。搜尋、上傳排程、分享、版本、封存、審核及帳號狀態都只保留在這個頁面的記憶體，重新整理後還原。引用問答使用預設範例，沒有模型呼叫。登入後的文件閱讀頁則使用真正的 Session 與資料庫授權。</p><a class="button secondary" href="/documents" style="margin-top:20px">前往實際文件頁${icon('arrow')}</a></section>`;
  }
  function render() {
    const views={documents:documentsView,ask:askView,processing:processingView,transfers:transfersView,history:historyView,members:membersView,usage:usageView,guide:guideView};
    app.innerHTML = views[state.view]();
    app.classList.remove('animation-in'); void app.offsetWidth; app.classList.add('animation-in');
    $('#breadcrumb-current').textContent = viewNames[state.view];
    document.title = `${viewNames[state.view]}｜知序 Atlas 互動展示`;
    $$('[data-nav]').forEach(n=>{n.classList.toggle('active',n.dataset.nav===state.view);if(n.dataset.nav===state.view)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
    $('#sidebar').classList.remove('open');
  }
  function navigate() {
    const target = location.hash.slice(1) || 'documents';
    if (target === 'upload') {state.view='documents';render();uploadDialog();return;}
    state.view = viewNames[target] ? target : 'documents'; render();
  }
  window.addEventListener('hashchange', navigate);

  function documentDialog(id, tab = 'content') {
    const d = findDoc(id);
    if (d.status === 'archived') {
      openDialog('這份文件已封存','封存後退出一般閱讀、搜尋與下載。',`<div class="dialog-body"><div class="inline-note">${icon('lock')}封存保留資料與紀錄，但不能從一般文件頁開啟。歷史調閱需要獨立授權。</div></div>`,action('close','關閉',null));return;
    }
    if (d.status !== 'ready') {
      openDialog(esc(d.title),'內容尚未完成處理，暫時不能閱讀。',`<div class="dialog-body">${badge(d.status)}<div class="steps">${['檔案驗證','解析清洗','文字切塊','建立向量','完成索引'].map((s,i)=>`<div class="step ${i<2?'done':i===2?'active':''}">${s}</div>`).join('')}</div><p class="section-caption">完成所有步驟後才會開放閱讀與檢索。</p></div>`,action('close','關閉',null)+action('goto-processing','查看文件處理','activity','primary'));return;
    }
    let body;
    if (tab === 'permissions') body = `<div class="dialog-body"><h3 style="font-size:13px;margin-bottom:15px">目前版本的閱讀範圍</h3>${access(d)}<div class="inline-note">${icon('shield')}${d.visibility==='company'?'本公司所有有效帳號都可讀取目前版本。':d.readers.length?'已授權：'+d.readers.filter(id=>members.find(m=>m.id===id)?.active).map(memberName).join('、'):'目前只有版本擁有者可以閱讀。'}</div><p class="section-caption">此授權只適用 v${d.v}，不自動授予新版或歷史版本。</p>${action('share','設定閱讀權限','users','secondary',`data-id="${d.id}" style="margin-top:15px"`)}</div>`;
    else if (tab === 'info') body = `<div class="dialog-body"><div class="steps">${['檔案驗證','解析清洗','文字切塊','建立向量','完成索引'].map(s=>`<div class="step done">${s}<span>已完成</span></div>`).join('')}</div><p class="section-caption">檔案類型 ${d.type} · ${d.size} · 目前有效版本 v${d.v}</p><div class="inline-note">${icon('check')}文字與來源位置已完成索引，此處為流程展示資料。</div></div>`;
    else body = `<article class="dialog-document">${esc(d.content)}</article>`;
    openDialog(esc(d.title),'甲公司知識空間 · 版本擁有者：林以安',`<div class="detail-meta"><span class="version-label">v${d.v} 目前有效</span><span class="category-label">${d.category}</span>${access(d)}${badge(d.status)}</div><div class="detail-tabs">${[['content','文件內容'],['permissions','閱讀權限'],['info','處理資訊']].map(([v,n])=>`<button class="${tab===v?'active':''}" data-action="detail-tab" data-tab="${v}" data-id="${d.id}">${n}</button>`).join('')}</div>${body}`,`<div class="detail-controls">${action('star',d.star?'取消收藏':'收藏文件','star','secondary',`data-id="${d.id}"`)}${action('version','新增版本','plus','secondary',`data-id="${d.id}"`)}${action('archive-confirm','封存','history','secondary',`data-id="${d.id}"`)}</div>${action('share','設定權限','users','primary',`data-id="${d.id}"`)}`,true);
  }
  function shareDialog(id) {
    const d = findDoc(id);
    openDialog('閱讀權限設定',`${esc(d.title)} · 第 ${d.v} 版`, `<form id="sharing-form" data-id="${d.id}"><div class="dialog-body"><label class="radio-card"><input type="radio" name="visibility" value="private" ${d.visibility==='private'&&!d.readers.length?'checked':''}>${icon('lock')}<div>僅自己<p>只有此版本的擁有者可以閱讀。</p></div></label><label class="radio-card"><input type="radio" name="visibility" value="company" ${d.visibility==='company'?'checked':''}>${icon('users')}<div>本公司可讀<p>本公司所有有效帳號可閱讀此版本。</p></div></label><label class="radio-card"><input type="radio" name="visibility" value="selected" ${d.visibility==='private'&&d.readers.length?'checked':''}>${icon('shield')}<div>指定同公司人員<p>只授權所選的有效帳號。</p></div></label><div id="sharing-members" style="padding:8px 5px">${members.filter(m=>m.active&&m.id!=='owner').map(m=>`<label style="display:flex;align-items:center;gap:9px;font-size:11px;padding:7px"><input type="checkbox" name="readers" value="${m.id}" ${d.readers.includes(m.id)?'checked':''}>${m.name}<span class="subtle">a.${m.id}</span></label>`).join('')}</div><div class="inline-note">${icon('lock')}只適用目前版本。新版不繼承此授權，撤權後下次存取即失效。</div><div id="permission-preview" class="section-caption"></div></div><div class="dialog-footer">${action('close','取消',null)}<button class="button primary" type="submit">${icon('check')}儲存展示設定</button></div></form>`);
    updatePermissionPreview();
  }
  function updatePermissionPreview() {
    const f = $('#sharing-form'); if(!f)return;
    const v = new FormData(f).get('visibility');
    $$('#sharing-members input').forEach(i=>i.disabled=v!=='selected');
    $('#sharing-members').style.opacity=v==='selected'?'1':'.45';
    const canRead = v==='company'||v==='selected'&&new FormData(f).getAll('readers').includes('reader');
    $('#permission-preview').textContent=`閱讀者 a.reader 檢視預覽：${canRead?'可閱讀目前版本':'無權閱讀，回傳 404'}`;
  }
  function uploadDialog() {
    state.selectedFile=null;
    openDialog('讓新的知識，加入團隊','新文件預設私人，完成處理後才可供閱讀與搜尋。',`<form id="upload-form"><div class="dialog-body"><label class="dropzone" id="dropzone">${icon('upload')}<strong id="file-label">選擇 PDF 或 TXT 文件</strong><small>文字型 PDF / UTF-8 TXT · 展示上限 10 MB</small><input id="upload-file" type="file" accept=".pdf,.txt" aria-label="選擇上傳文件"></label><label class="form-label">文件名稱<input id="upload-title" name="title" required maxlength="100" placeholder="為這份知識取一個清楚的名稱"></label><label class="form-label">業務分類<select name="category"><option>作業規定</option><option>技令手冊</option><option>料件清冊</option></select></label><div class="inline-note">${icon('lock')}示範操作不會傳送或保存檔案。只建立暫存的文件名稱與處理工作。</div></div><div class="dialog-footer">${action('close','取消',null)}<button class="button primary" type="submit">${icon('upload')}加入展示佇列</button></div></form>`);
  }
  function versionDialog(id) {
    const d = findDoc(id);
    if(d.pending){toast('這份文件已有待處理版本，請先完成處理。');return;}
    openDialog('建立新版本',`${esc(d.title)} · v${d.v} → v${d.v+1}`,`<form id="version-form" data-id="${d.id}"><div class="dialog-body"><label class="form-label">新版內容<textarea name="content" required maxlength="10000" rows="8">${esc(d.content)}</textarea></label><div class="inline-note">${icon('lock')}新版預設私人，不複製既有閱讀權限。處理完成前，v${d.v} 繼續有效。</div><p class="field-hint">此處用文字模擬版本內容；正式上傳與向量處理尚未接入。</p></div><div class="dialog-footer">${action('close','取消',null)}<button class="button primary" type="submit">建立展示版本${icon('arrow')}</button></div></form>`);
  }
  function transferDialog() {
    if (!docs.some(d=>d.status==='ready') || !members.some(m=>m.active&&['reader','staff'].includes(m.id))) {
      openDialog('目前無法提出交接','需要可交接的有效文件及同公司有效接收人。','<div class="dialog-body"><p class="section-caption">請先確認文件未封存且已完成處理，並至少有一位有效的接收帳號。</p></div>',action('close','關閉',null));
      return;
    }
    openDialog('提出文件交接','移轉管理責任前，需要直屬主管核准。',`<form id="transfer-form"><div class="dialog-body"><label class="form-label">選擇文件<select name="document">${docs.filter(d=>d.status==='ready').map(d=>`<option value="${d.id}">${esc(d.title)} · v${d.v}</option>`).join('')}</select></label><label class="form-label">接收人<select name="recipient">${members.filter(m=>m.active&&['reader','staff'].includes(m.id)).map(m=>`<option value="${m.id}">${m.name}（a.${m.id}）</option>`).join('')}</select></label><label class="form-label">交接原因<textarea name="reason" placeholder="請說明職務或責任調整；勿貼入文件正文。" required maxlength="200"></textarea></label><label style="font-size:10px;color:#859775;display:flex;gap:8px"><input type="checkbox" required>交接範圍僅含目前版本，保留既有閱讀 ACL。</label><div class="inline-note">${icon('shield')}直屬主管：王柏鈞。主管審核介面不會顯示檔名或內容，也不能指定主管自己接收。</div></div><div class="dialog-footer">${action('close','取消',null)}<button class="button primary" type="submit">送出展示申請${icon('arrow')}</button></div></form>`);
  }
  function transferDetail(id) {
    const t=transfers.find(t=>t.id===id);
    const isReview=state.review;
    openDialog(isReview?'主管審核':'交接申請詳情',t.id,`<div class="dialog-body">${!isReview?`<h3 style="font-size:13px">${esc(findDoc(t.doc).title)} · v${findDoc(t.doc).v}</h3>`:''}<div class="transfer-person"><div>${avatar('owner')}<strong>林以安</strong><small>原擁有者</small></div>${icon('arrow')}<div>${avatar(t.to)}<strong>${memberName(t.to)}</strong><small>接收人</small></div></div><label class="form-label">交接原因</label><p style="font-size:12px;color:#80926d;line-height:2">${esc(t.reason)}</p><div class="inline-note">${icon('shield')}${isReview?'此審核頁不顯示檔名、摘要及內容。核准前會重新確認接收帳號狀態。':'狀態：'+t.status+'。直屬主管：王柏鈞。交接後保留既有閱讀權。'}</div></div>`,isReview&&t.status==='待審核'?action('transfer-reject','拒絕申請',null,'secondary',`data-id="${t.id}"`)+action('transfer-approve','模擬核准','check','primary',`data-id="${t.id}"`):action('close','關閉',null));
  }
  document.addEventListener('input', e => {
    if(e.target.id==='demo-search') {
      state.query=e.target.value;$('#document-results').innerHTML=resultHtml();$('#result-count').textContent=`顯示 ${rows().length} 份文件`;
    }
  });
  document.addEventListener('change', e => {
    if(e.target.id==='demo-category'){state.category=e.target.value;render();}
    if(e.target.closest('#sharing-form'))updatePermissionPreview();
    if(e.target.id==='upload-file') {
      const file=e.target.files[0];if(!file)return;
      if(!/\.(pdf|txt)$/i.test(file.name)||file.size>10*1024*1024){e.target.value='';state.selectedFile=null;toast('請選擇 10 MB 以下的 PDF 或 TXT 文件。');return;}
      state.selectedFile=file;
      $('#file-label').textContent=file.name;
      $('#upload-title').value=file.name.replace(/\.(pdf|txt)$/i,'');
    }
  });
  document.addEventListener('submit', e => {
    const f=e.target;if(!['question-form','sharing-form','upload-form','version-form','transfer-form'].includes(f.id))return;
    e.preventDefault();const data=new FormData(f);
    if(f.id==='question-form'){if(!data.get('question').trim()){toast('請先輸入問題。');return;} answerQuestion(data.get('question'));}
    if(f.id==='sharing-form') {
      const d=findDoc(f.dataset.id),v=data.get('visibility');d.visibility=v==='company'?'company':'private';d.readers=v==='selected'?data.getAll('readers'):[];
      audit.unshift({title:'更新版本閱讀權限',detail:`${d.title} · v${d.v}`,time:'剛剛'});closeDialog();render();toast('已更新展示權限；正式資料未變更。');
    }
    if(f.id==='upload-form') {
      if(!state.selectedFile){toast('請先選擇一份 PDF 或 TXT 文件。');return;}
      if(!data.get('title').trim()){toast('請輸入文件名稱。');return;}
      docs.unshift({id:Math.max(...docs.map(d=>d.id))+1,title:data.get('title').trim(),category:data.get('category'),type:state.selectedFile.name.toLowerCase().endsWith('.pdf')?'PDF':'TXT',size:Math.max(1,Math.round(state.selectedFile.size/1024))+' KB',v:1,visibility:'private',readers:[],status:'queued',date:'2026/10/20',content:'這是新上傳工作完成後的展示內容。\n\n本次操作未讀取、傳送或保存你選擇的檔案內容；正式解析與向量化功能待後續接入。',color:'teal',star:false});
      closeDialog();location.hash='processing';if(state.view==='processing')render();toast('已加入展示佇列，檔案未傳送。');
    }
    if(f.id==='version-form') {
      const d=findDoc(f.dataset.id);if(!data.get('content').trim()){toast('新版內容不能空白。');return;}
      d.pending={content:data.get('content')};closeDialog();location.hash='processing';if(state.view==='processing')render();toast('新版已排入展示佇列，原有效版保持可用。');
    }
    if(f.id==='transfer-form') {
      if(!data.get('reason').trim()){toast('請填寫交接原因。');return;}
      if(!docs.some(d=>d.id===Number(data.get('document'))&&d.status==='ready') || !members.some(m=>m.id===data.get('recipient')&&m.active&&['reader','staff'].includes(m.id))) {
        toast('文件或接收人已不可用，請重新確認交接範圍。');return;
      }
      transfers.unshift({id:'TR-2026-'+String(9+transfers.length).padStart(3,'0'),doc:Number(data.get('document')),to:data.get('recipient'),reason:data.get('reason').trim(),status:'待審核'});closeDialog();state.review=false;render();toast('展示申請已送出，等待直屬主管審核。');
    }
  });
  document.addEventListener('click', e => {
    const b=e.target.closest('[data-action]');if(!b)return;
    const a=b.dataset.action,id=b.dataset.id,d=id?findDoc(id):null;
    if(a==='upload')uploadDialog();
    if(a==='document'||a==='source')documentDialog(id);
    if(a==='detail-tab')documentDialog(id,b.dataset.tab);
    if(a==='share')shareDialog(id);
    if(a==='version')versionDialog(id);
    if(a==='grid-view'){state.grid=true;render();}
    if(a==='list-view'){state.grid=false;render();}
    if(a==='tab'){state.tab=b.dataset.tab;render();}
    if(a==='clear-filters'){state.query='';state.category='全部分類';render();}
    if(a==='sort'){docs.sort((a,b)=>a.date===b.date?b.id-a.id:b.date.localeCompare(a.date));render();toast('已依更新時間由新到舊排序。');}
    if(a==='star'){d.star=!d.star;documentDialog(id);toast(d.star?'已加入收藏。':'已取消收藏。');render();}
    if(a==='suggestion') {$('#question').value=b.dataset.question;answerQuestion(b.dataset.question);}
    if(a==='goto-processing'){closeDialog();location.hash='processing';}
    if(a==='complete') {
      if(d.status==='archived'){toast('已封存文件不能重新啟用。');return;}
      if(d.pending){d.content=d.pending.content;d.v++;d.pending=null;d.visibility='private';d.readers=[];}
      d.status='ready';audit.unshift({title:'文件已完成處理',detail:`${d.title} · v${d.v} 已可搜尋`,time:'剛剛'});render();toast('展示處理完成，新版預設私人；請另行設定閱讀權。');
    }
    if(a==='archive-confirm')openDialog('封存這份文件？',esc(d.title),`<div class="dialog-body"><p style="font-size:12px;line-height:2;color:#8b9b79">封存會保留版本與紀錄，並使文件退出一般閱讀、下載與問答檢索。處理中的工作也不能將它重新啟用。</p></div>`,action('document','返回文件',null,'secondary',`data-id="${d.id}"`)+action('archive','確認封存','history','primary',`data-id="${d.id}"`));
    if(a==='archive'){d.status='archived';d.pending=null;closeDialog();render();toast('已封存展示文件，不再提供一般檢索。');}
    if(a==='transfer-new')transferDialog();
    if(a==='transfer-mine'){state.review=false;render();}
    if(a==='transfer-review'){state.review=true;render();}
    if(a==='transfer-detail')transferDetail(id);
    if(a==='transfer-approve'||a==='transfer-reject') {
      const t=transfers.find(t=>t.id===id);if(t.status!=='待審核'){toast('此申請已審核，無法重複處理。');return;}
      if(a==='transfer-approve'&&!members.find(m=>m.id===t.to)?.active){toast('接收帳號已停用，無法核准。');return;}
      if(a==='transfer-approve'&&findDoc(t.doc).status==='archived'){toast('文件已封存，無法核准。');return;}
      // Demonstrates review decisions only; no DB ownership transfer is claimed.
      t.status=a==='transfer-approve'?'已核准':'已拒絕';closeDialog();render();toast('已記錄展示審核結果；正式擁有權未變更。');
    }
    if(a==='history-read') {
      const date=new Date().toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit'});
      historyEvents.unshift(date);
      $('#history-log').innerHTML=historyLogHtml();
      openDialog('歷史版本調閱','國內出差與交通補助規定 · v2 · 獨立歷史授權',`<div class="dialog-body"><div class="inline-note">${icon('history')}這是歷史版本，不能作為一般問答的有效證據。調閱已記錄於本次展示。</div><article class="document-content">${esc('2026 年第二版\n\n甲公司國內出差交通補助上限為每日 NT$1,200，請於返回後 7 日內提出申請。\n\n此版已被 v3 取代，僅供獨立授權之歷史調閱。')}</article></div>`,action('close','關閉歷史版本',null));
    }
    if(a==='toggle-member') {
      const m=members.find(m=>m.id===id);if(m.id==='admin')return;
      openDialog(m.active?'停用這位成員？':'重新啟用成員？',`${m.name} · a.${m.id}`,`<div class="dialog-body"><p style="font-size:12px;color:#8b9c78">${m.active?'停用後將無法繼續存取；文件不會自動轉移給管理員。':'重新啟用後仍依原有版本閱讀授權，不增加任何文件權限。'}</p><div class="inline-note">${icon('users')}本次僅示範帳號狀態，正式帳號不受影響。</div></div>`,action('close','取消',null)+action('member-confirm','確認展示操作','check','primary',`data-id="${m.id}"`));
    }
    if(a==='member-confirm'){const m=members.find(m=>m.id===id);m.active=!m.active;closeDialog();render();toast(`${m.name}已${m.active?'啟用':'停用'}（僅展示）。`);}
  });
  navigate();
})();
