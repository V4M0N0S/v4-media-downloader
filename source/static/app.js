const $ = s => document.querySelector(s);
const jobsEl = $('#jobs'), filesEl = $('#files'), statsEl = $('#stats');
let cachedFiles = [], cachedJobs = [], cachedSearchResults = [], activeFilter = 'all', analyzedMedia = null;
let hadRunningJobs = false;
let translations = {}, fallbackTranslations = {}, localeMeta = { code: 'de', locale: 'de-DE' }, availableLocales = [];

function esc(s=''){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function getPath(obj,path){return path.split('.').reduce((v,k)=>v&&Object.prototype.hasOwnProperty.call(v,k)?v[k]:undefined,obj)}
function t(key, vars={}){
  let value=getPath(translations,key); if(value===undefined)value=getPath(fallbackTranslations,key); if(value===undefined)value=key;
  return String(value).replace(/\{(\w+)\}/g,(_,name)=>vars[name]===undefined?`{${name}}`:String(vars[name]));
}
function bytes(n){if(!n)return'0 B';const u=['B','KB','MB','GB','TB'];const i=Math.min(u.length-1,Math.floor(Math.log(n)/Math.log(1024)));return`${(n/1024**i).toFixed(1)} ${u[i]}`}
function date(ts){try{return new Date(ts*1000).toLocaleString(localeMeta.locale||localeMeta.code||'de-DE')}catch{return new Date(ts*1000).toLocaleString()}}
function duration(sec){if(sec===null||sec===undefined||sec==='')return'';sec=Math.round(Number(sec));const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`}
function kindLabel(k){return({vod:'VOD',clip:'CLIP',live:'LIVE',video:'VIDEO',twitch:'TWITCH',media:'MEDIA'})[k]||String(k||'MEDIA').toUpperCase()}
function translatedStage(stage){return t(`stage.${stage}`)===`stage.${stage}`?stage:t(`stage.${stage}`)}
function translatedStatus(status){const key=`status.${status}`;return t(key)===key?status:t(key)}

async function loadLocale(code){
  const r=await fetch(`/locales/${encodeURIComponent(code)}.json`,{cache:'no-store'}); if(!r.ok)throw new Error(`Locale ${code} unavailable`);
  const data=await r.json(); translations=data; localeMeta=data.meta||{code};
  document.documentElement.lang=localeMeta.code||code; localStorage.setItem('v4md-language',localeMeta.code||code);
  applyTranslations(); renderAllCached(); if(document.querySelector('#cookieStatus')) refreshCookieStatus();
}

function applyTranslations(){
  document.querySelectorAll('[data-i18n]').forEach(el=>{el.textContent=t(el.dataset.i18n)});
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el=>{el.placeholder=t(el.dataset.i18nPlaceholder)});
  document.querySelectorAll('[data-i18n-aria-label]').forEach(el=>{el.setAttribute('aria-label',t(el.dataset.i18nAriaLabel))});
  if(!analyzedMedia) resetQualityOptions(); else renderAnalysis(analyzedMedia, false);
  updateDownloadButtonSummary();
}

async function refreshCookieStatus(){
  const badge=$('#cookieStatus');
  if(!badge)return;
  try{
    const r=await fetch('/api/cookies',{cache:'no-store'}),d=await r.json();
    badge.textContent=d.configured?t('cookies.active'):t('cookies.inactive');
    badge.classList.toggle('active',!!d.configured);
    $('#cookieDeleteBtn').hidden=!d.configured;
  }catch(e){badge.textContent=t('cookies.unknown')}
}
function openCookieModal(){
  $('#cookieModal').classList.add('open');$('#cookieModal').setAttribute('aria-hidden','false');
  $('#cookieText').value='';refreshCookieStatus();setTimeout(()=>$('#cookieText').focus(),50);
}
function closeCookieModal(){
  $('#cookieModal').classList.remove('open');$('#cookieModal').setAttribute('aria-hidden','true');$('#cookieText').value='';
}
async function saveCookies(){
  const cookies=$('#cookieText').value.trim();
  if(!cookies)return showToast(t('cookies.empty'),'warning');
  const btn=$('#cookieSaveBtn');btn.disabled=true;
  try{
    const r=await fetch('/api/cookies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies})}),d=await r.json();
    if(!r.ok||!d.success)throw new Error(d.error||t('cookies.save_failed'));
    closeCookieModal();await refreshCookieStatus();showToast(t('cookies.saved'),'success',t('cookies.saved_title'));
  }catch(e){showToast(e.message,'error')}finally{btn.disabled=false}
}
async function deleteCookies(){
  if(!confirm(t('cookies.delete_confirm')))return;
  try{
    const r=await fetch('/api/cookies',{method:'DELETE'}),d=await r.json();
    if(!r.ok||!d.success)throw new Error(d.error||t('cookies.delete_failed'));
    $('#cookieText').value='';await refreshCookieStatus();showToast(t('cookies.deleted'),'success');
  }catch(e){showToast(e.message,'error')}
}

async function initI18n(){
  try{
    const listR=await fetch('/api/locales',{cache:'no-store'}), list=await listR.json(); availableLocales=list.locales||[];
    const deR=await fetch('/locales/de.json',{cache:'no-store'}); if(deR.ok)fallbackTranslations=await deR.json();
    const saved=localStorage.getItem('v4md-language'); const browser=(navigator.language||'de').toLowerCase();
    let code=saved;
    if(!code||!availableLocales.some(x=>x.code===code)){
      code=(availableLocales.find(x=>browser===x.code.toLowerCase()||browser.startsWith(`${x.code.toLowerCase()}-`))||{}).code||list.default||availableLocales[0]?.code||'de';
    }
    const select=$('#languageSelect'); select.innerHTML=availableLocales.map(x=>`<option value="${esc(x.code)}">${esc(x.native_name||x.name||x.code)}</option>`).join(''); select.value=code;
    await loadLocale(code); await refreshCookieStatus();
  }catch(e){console.error('i18n:',e); translations=fallbackTranslations; applyTranslations();}
}

function showToast(message,type='info',title=''){
  const stack=$('#toastStack'), item=document.createElement('div'); item.className=`toast-item ${type}`;
  const defaultTitle={success:t('common.success'),error:t('common.error'),warning:t('common.warning'),info:t('common.info')}[type]||t('common.info');
  item.innerHTML=`<div class="toast-mark"></div><div class="toast-copy"><strong>${esc(title||defaultTitle)}</strong><span>${esc(message)}</span></div><button class="toast-close" aria-label="${esc(t('common.close'))}">×</button>`;
  stack.appendChild(item); requestAnimationFrame(()=>item.classList.add('visible'));
  const remove=()=>{item.classList.remove('visible');setTimeout(()=>item.remove(),180)}; item.querySelector('.toast-close').onclick=remove; setTimeout(remove,4000);
}

function openYoutubeSearch(){const panel=$('#youtubeSearchPanel');panel.hidden=false;$('#searchToggleBtn').classList.add('active');setTimeout(()=>$('#youtubeSearchInput').focus(),40)}
function closeYoutubeSearch(clear=false){const panel=$('#youtubeSearchPanel');panel.hidden=true;$('#searchToggleBtn').classList.remove('active');if(clear){$('#youtubeSearchInput').value='';$('#searchStatus').textContent='';$('#searchResults').innerHTML='';cachedSearchResults=[]}}
function toggleYoutubeSearch(){$('#youtubeSearchPanel').hidden?openYoutubeSearch():closeYoutubeSearch()}
function resetQualityOptions(){const q=$('#qualityInput');if(q)q.innerHTML=`<option value="best">${esc(t('analysis.best_quality'))}</option>`}
function updateDownloadButtonSummary(){
  const btn=$('#addBtn');if(!btn)return;
  const format=($('#formatInput')?.value||'mp4').toUpperCase();
  let summary=format;
  if(format==='MP4'){const q=$('#qualityInput')?.value||'best';summary+=` · ${q==='best'?t('analysis.best_short'):`${q}p`}`}
  btn.textContent=`${t('download.start')} · ${summary}`;
}
function showAnalysisSkeleton(){
  const card=$('#analysisCard');
  card.hidden=false;card.setAttribute('aria-busy','true');card.classList.remove('is-visible');card.classList.add('is-loading');
  requestAnimationFrame(()=>requestAnimationFrame(()=>card.classList.add('is-visible')));
  card.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function finishAnalysisLoading(){
  const card=$('#analysisCard');card.setAttribute('aria-busy','false');
  requestAnimationFrame(()=>card.classList.remove('is-loading'));
}
function invalidateAnalysis(){analyzedMedia=null;const card=$('#analysisCard');card.hidden=true;card.classList.remove('is-loading','is-visible');card.setAttribute('aria-busy','false');resetQualityOptions();$('#trimStart').value='';$('#trimEnd').value='';updateDownloadButtonSummary()}
function updateFormatControls(){$('#qualityWrap').style.display=$('#formatInput').value==='mp4'?'block':'none';updateDownloadButtonSummary()}

function renderAnalysis(media, fillName=true){
  analyzedMedia=media;const card=$('#analysisCard');card.hidden=false;const thumb=$('#analysisThumb');
  if(media.thumbnail){thumb.src=media.thumbnail;thumb.style.display='block'}else{thumb.removeAttribute('src');thumb.style.display='none'}
  $('#analysisTitle').textContent=media.title||t('common.unknown_title');$('#analysisSource').textContent=media.source||t('common.web');$('#analysisKind').textContent=kindLabel(media.kind);
  $('#analysisMeta').textContent=[media.channel,media.duration?duration(media.duration):null,media.is_live?t('common.live'):null].filter(Boolean).join(' · ');
  const q=$('#qualityInput'),heights=Array.isArray(media.qualities)?media.qualities:[];q.innerHTML=`<option value="best">${esc(t('analysis.best_quality'))}</option>`+heights.map(h=>`<option value="${Number(h)}">${Number(h)}p</option>`).join('');
  $('#trimWrap').hidden=!media.trim_supported;
  let hint=t('analysis.ready'); if(media.trim_supported)hint=t('analysis.twitch_vod'); else if(media.source==='Twitch'&&media.kind==='clip')hint=t('analysis.twitch_clip'); else if(heights.length)hint=t('analysis.qualities_found',{count:heights.length});
  $('#analysisHint').textContent=hint; updateFormatControls(); if(fillName&&!$('#nameInput').value.trim())$('#nameInput').value=media.title||''; finishAnalysisLoading();
}

async function analyzeUrl({fromSearch=false}={}){
  const url=$('#urlInput').value.trim();if(!url)return showToast(t('download.analyze_first'),'warning');
  const btn=$('#analyzeBtn');btn.disabled=true;btn.textContent=t('download.analyzing');showAnalysisSkeleton();
  try{const r=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})}),d=await r.json();if(!r.ok||!d.success)throw new Error(d.error||t('download.analyze_failed'));renderAnalysis(d.media);if(fromSearch)closeYoutubeSearch(true);$('#downloadPanel').scrollIntoView({behavior:'smooth',block:'start'});showToast(t('download.analyzed'),'success',t('download.analyzed_title'))}
  catch(e){invalidateAnalysis();showToast(e.message,'error')}finally{btn.disabled=false;btn.textContent=t('download.analyze')}
}

async function addUrl(){
  const url=$('#urlInput').value.trim(),custom_name=$('#nameInput').value.trim(),format=$('#formatInput').value;if(!url)return showToast(t('download.missing_url'),'warning');
  const payload={url,custom_name,format,quality:format==='mp4'?$('#qualityInput').value:'best',trim_start:analyzedMedia?.trim_supported?$('#trimStart').value.trim():'',trim_end:analyzedMedia?.trim_supported?$('#trimEnd').value.trim():'',title:analyzedMedia?.title||'',thumbnail:analyzedMedia?.thumbnail||null,media_duration:analyzedMedia?.duration??null,channel:analyzedMedia?.channel||'',kind:analyzedMedia?.kind||''};
  try{const r=await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),d=await r.json();if(!r.ok)throw new Error(d.error||t('download.start_failed'));$('#urlInput').value='';$('#nameInput').value='';invalidateAnalysis();closeYoutubeSearch();await refresh();$('#activePanel').hidden=false;$('#activePanel').scrollIntoView({behavior:'smooth',block:'start'});showToast(t('download.queued'),'success')}catch(e){showToast(e.message,'error')}
}

async function uploadTxt(file){const fd=new FormData();fd.append('file',file);fd.append('format',$('#bulkFormatInput').value);fd.append('quality','best');try{const r=await fetch('/api/upload',{method:'POST',body:fd}),d=await r.json();if(!r.ok)throw new Error(d.error||t('download.upload_failed'));showToast(t('download.txt_imported',{count:d.count}),'success',t('download.txt_imported_title'));await refresh();$('#activePanel').scrollIntoView({behavior:'smooth',block:'start'})}catch(e){showToast(e.message,'error')}finally{$('#fileInput').value=''}}
async function pasteUrl(){try{const text=(await navigator.clipboard.readText()).trim();if(!text)return showToast(t('download.clipboard_empty'),'warning');$('#urlInput').value=text.split(/\s+/)[0];invalidateAnalysis();$('#urlInput').focus();showToast(t('download.clipboard_added'),'info')}catch(e){showToast(t('download.clipboard_blocked'),'error')}}

function openBulkModal(){$('#bulkModal').classList.add('open');$('#bulkModal').setAttribute('aria-hidden','false')}
function closeBulkModal(){$('#bulkModal').classList.remove('open');$('#bulkModal').setAttribute('aria-hidden','true');$('#fileInput').value=''}

async function searchYoutube(){const q=$('#youtubeSearchInput').value.trim();if(q.length<2)return showToast(t('search.too_short'),'warning');const btn=$('#youtubeSearchBtn'),status=$('#searchStatus'),out=$('#searchResults');btn.disabled=true;status.textContent=t('search.searching');out.innerHTML='';try{const r=await fetch(`/api/search?q=${encodeURIComponent(q)}`),d=await r.json();if(!r.ok||!d.success)throw new Error(d.error||t('search.failed'));cachedSearchResults=d.results||[];renderSearchResults(cachedSearchResults);status.textContent=t('search.results',{count:cachedSearchResults.length})}catch(e){status.textContent='';showToast(e.message,'error')}finally{btn.disabled=false}}
async function useSearchResult(index){const result=cachedSearchResults[index];if(!result)return;$('#urlInput').value=result.url||'';$('#nameInput').value=result.title||'';invalidateAnalysis();await analyzeUrl({fromSearch:true})}
function renderSearchResults(results){const out=$('#searchResults');if(!results.length){out.innerHTML=`<div class="empty" style="grid-column:1/-1">${esc(t('search.no_results'))}</div>`;return}out.innerHTML=results.map((r,i)=>`<article class="search-card">${r.thumbnail?`<img class="search-thumb" src="${esc(r.thumbnail)}" alt="">`:`<div class="search-thumb"></div>`}<div class="search-card-body"><div class="search-title">${esc(r.title)}</div><div class="search-meta">${esc(r.channel||'YouTube')}${r.duration?` · ${duration(r.duration)}`:''}</div><button class="search-use" data-search-index="${i}">${esc(t('search.use'))}</button></div></article>`).join('')}

async function jobAction(id,action){const r=action==='delete'?await fetch(`/api/jobs/${id}`,{method:'DELETE'}):await fetch(`/api/jobs/${id}/${action}`,{method:'POST'}),d=await r.json();if(!d.success)showToast(t('jobs.action_failed'),'error');refresh()}
async function removeFile(name){if(!confirm(t('library.confirm_delete',{name})))return;const r=await fetch(`/api/files/${encodeURIComponent(name)}`,{method:'DELETE'}),d=await r.json();if(!r.ok||!d.success)return showToast(d.error||t('library.delete_failed'),'error');showToast(t('library.deleted'),'success');refresh()}
async function deleteAll(){if(!cachedFiles.length)return;if(!confirm(t('library.confirm_delete_all',{count:cachedFiles.length})))return;const btn=$('#deleteAllBtn');btn.disabled=true;try{const r=await fetch('/api/delete-all',{method:'POST'}),d=await r.json();if(!r.ok||!d.success)throw new Error((d.errors||[]).join('\n')||t('library.delete_all_failed'));showToast(t('library.deleted_all',{count:d.deleted}),'success');await refresh()}catch(e){showToast(e.message,'error')}finally{btn.disabled=false}}
async function copyName(name){try{await navigator.clipboard.writeText(name);showToast(t('library.filename_copied'),'success')}catch(e){showToast(t('library.copy_failed'),'error')}}
function openPreview(name,format,url){const content=$('#previewContent'),safe=esc(url);content.innerHTML=format==='mp4'?`<video class="preview-media" controls autoplay src="${safe}"></video>`:`<div style="padding:24px 4px 8px;font-weight:800">${esc(name)}</div><audio class="preview-audio" controls autoplay src="${safe}"></audio>`;$('#previewModal').classList.add('open');$('#previewModal').setAttribute('aria-hidden','false')}
function closePreview(){$('#previewContent').innerHTML='';$('#previewModal').classList.remove('open');$('#previewModal').setAttribute('aria-hidden','true')}window.closePreview=closePreview;

function trimDescription(j){if(j.trim_start===null&&j.trim_end===null)return'';const a=j.trim_start!==null?duration(j.trim_start):t('jobs.start'),b=j.trim_end!==null?duration(j.trim_end):t('jobs.end');return` · ${t('jobs.clip')} ${a}-${b}`}
function renderJobs(jobs){
  cachedJobs=jobs;const panel=$('#activePanel'),activeJobs=jobs.filter(j=>j.status!=='finished');panel.hidden=!activeJobs.length;$('#activeSummary').textContent=activeJobs.length?`${activeJobs.length} ${activeJobs.length===1?t('active.job'):t('active.jobs')}`:'';
  if(!activeJobs.length){jobsEl.innerHTML='';if(hadRunningJobs)showToast(t('active.all_done'),'success');hadRunningJobs=false;return}hadRunningJobs=true;
  jobsEl.innerHTML=activeJobs.slice().reverse().map(j=>{const extra=[j.speed,j.eta&&`${t('jobs.eta')} ${j.eta}`].filter(Boolean).join(' · '),quality=j.format==='mp4'&&j.quality&&j.quality!=='best'?` · ${esc(j.quality)}p`:'';return `<div class="job"><img class="thumb" src="${esc(j.thumbnail||'')}" onerror="this.style.visibility='hidden'"><div><div class="job-title">${esc(j.custom_name||j.title)} <span class="format-pill">${esc((j.format||'mp3').toUpperCase())}${quality}</span><span class="source-pill">${esc(j.source||t('common.web'))}</span></div><div class="meta">${esc(translatedStage(j.stage))}${extra?` · ${esc(extra)}`:''}${trimDescription(j)}</div><div class="bar"><div style="width:${Number(j.progress||0)}%"></div></div><div class="progress-line"><span>${Number(j.progress||0).toFixed(1)}%</span><span>${esc(extra)}</span></div>${(j.status==='error'||j.status==='waiting')?`<div class="job-actions">${j.status==='error'?`<button class="ghost mini" data-job-id="${esc(j.id)}" data-job-action="retry">${esc(t('jobs.retry'))}</button>`:''}<button class="ghost mini" data-job-id="${esc(j.id)}" data-job-action="delete">${esc(t('jobs.remove'))}</button></div>`:''}${j.error?`<div class="meta error-copy">${esc(j.error)}</div>`:''}</div><div class="status ${esc(j.status)}">${esc(translatedStatus(j.status))}</div></div>`}).join('')
}
function renderFiles(files){cachedFiles=files;const q=$('#searchInput').value.trim().toLowerCase(),list=files.filter(f=>(activeFilter==='all'||f.format===activeFilter)&&f.name.toLowerCase().includes(q));$('#downloadAllBtn').classList.toggle('disabled',!files.length);$('#deleteAllBtn').disabled=!files.length;if(!list.length){filesEl.innerHTML=`<div class="empty">${esc(t('library.empty'))}</div>`;return}filesEl.innerHTML=list.map(f=>{const idx=cachedFiles.indexOf(f),thumb=f.thumbnail?`<img class="library-thumb" src="${esc(f.thumbnail)}" alt="">`:`<div class="library-thumb placeholder">${esc(t('library.no_thumb'))}</div>`;return `<div class="file" data-file-index="${idx}">${thumb}<div class="file-main"><div class="file-name">${esc(f.name)} <span class="format-pill">${esc((f.format||'').toUpperCase())}</span></div><div class="file-meta">${esc(t('library.done'))} · ${bytes(f.size)} · ${date(f.mtime)}</div></div><div class="file-actions"><button class="icon-btn" data-file-action="preview">${esc(t('library.play'))}</button><button class="icon-btn" data-file-action="copy">${esc(t('library.copy_name'))}</button><a href="/downloads/${encodeURIComponent(f.name)}">${esc(t('library.download'))}</a><button class="icon-btn danger" data-file-action="delete">${esc(t('library.remove'))}</button></div></div>`}).join('')}
function renderStats(jobs,files){const active=jobs.filter(j=>['starting','downloading','converting'].includes(j.status)).length,waiting=jobs.filter(j=>j.status==='waiting').length,done=files.length,size=files.reduce((a,b)=>a+b.size,0);statsEl.innerHTML=[[t('stats.active'),active],[t('stats.waiting'),waiting],[t('stats.done'),done],[t('stats.storage'),bytes(size)]].map(([l,n])=>`<div class="stat"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`).join('')}
function renderAllCached(){renderJobs(cachedJobs);renderFiles(cachedFiles);renderStats(cachedJobs,cachedFiles);if(cachedSearchResults.length)renderSearchResults(cachedSearchResults)}
async function refresh(){try{const[jr,fr]=await Promise.all([fetch('/api/jobs'),fetch('/api/files')]),jd=await jr.json(),fd=await fr.json();renderJobs(jd.jobs);renderFiles(fd);renderStats(jd.jobs,fd)}catch(e){console.error(e)}}

$('#analyzeBtn').onclick=()=>analyzeUrl();$('#addBtn').onclick=addUrl;$('#pasteBtn').onclick=pasteUrl;
$('#searchToggleBtn').onclick=toggleYoutubeSearch;$('#searchCloseBtn').onclick=()=>closeYoutubeSearch();
$('#urlInput').addEventListener('input',invalidateAnalysis);$('#urlInput').addEventListener('keydown',e=>{if(e.key==='Enter')analyzeUrl()});$('#formatInput').addEventListener('change',updateFormatControls);$('#qualityInput').addEventListener('change',updateDownloadButtonSummary);
$('#bulkToggleBtn').onclick=openBulkModal;$('#bulkCloseBtn').onclick=closeBulkModal;document.querySelectorAll('[data-bulk-close]').forEach(el=>el.addEventListener('click',closeBulkModal));$('#fileInput').addEventListener('change',async e=>{if(e.target.files[0]){const file=e.target.files[0];await uploadTxt(file);closeBulkModal()}});
$('#youtubeSearchBtn').onclick=searchYoutube;$('#youtubeSearchInput').addEventListener('keydown',e=>{if(e.key==='Enter')searchYoutube()});$('#searchResults').addEventListener('click',e=>{const b=e.target.closest('[data-search-index]');if(b)useSearchResult(Number(b.dataset.searchIndex))});
$('#deleteAllBtn').onclick=deleteAll;$('#searchInput').addEventListener('input',()=>renderFiles(cachedFiles));$('#filters').addEventListener('click',e=>{const b=e.target.closest('[data-format]');if(!b)return;activeFilter=b.dataset.format;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x===b));renderFiles(cachedFiles)});
$('#languageSelect').addEventListener('change',async e=>{try{await loadLocale(e.target.value);showToast(`${availableLocales.find(x=>x.code===e.target.value)?.native_name||e.target.value}`,'success')}catch(err){console.error(err)}});
jobsEl.addEventListener('click',e=>{const b=e.target.closest('[data-job-action]');if(b)jobAction(b.dataset.jobId,b.dataset.jobAction)});filesEl.addEventListener('click',e=>{const b=e.target.closest('[data-file-action]');if(!b)return;const row=b.closest('[data-file-index]'),f=cachedFiles[Number(row.dataset.fileIndex)];if(!f)return;if(b.dataset.fileAction==='preview')openPreview(f.name,f.format,f.stream_url);if(b.dataset.fileAction==='copy')copyName(f.name);if(b.dataset.fileAction==='delete')removeFile(f.name)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closePreview();closeYoutubeSearch();closeBulkModal()}});

(async()=>{await initI18n();updateFormatControls();await refresh();setInterval(refresh,1000)})();

$('#cookieOpenBtn')?.addEventListener('click',openCookieModal);
$('#cookieCloseBtn')?.addEventListener('click',closeCookieModal);
$('#cookieSaveBtn')?.addEventListener('click',saveCookies);
$('#cookieDeleteBtn')?.addEventListener('click',deleteCookies);
document.querySelectorAll('[data-cookie-close]').forEach(el=>el.addEventListener('click',closeCookieModal));