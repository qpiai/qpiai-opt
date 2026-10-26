
(() => {
  const $ = (s, r=document) => r.querySelector(s);
  const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
  const root = document.documentElement;
  const theme = $('#theme-toggle');
  const setTheme = value => { root.dataset.theme=value; try{localStorage.setItem('qpiai-docs-theme', value)}catch(e){} };
  theme?.addEventListener('click', () => setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'));

  const navToggle=$('#nav-toggle'), sidebar=$('#sidebar');
  navToggle?.addEventListener('click', () => { const open=sidebar.classList.toggle('open'); navToggle.setAttribute('aria-expanded', String(open)); });
  document.addEventListener('click', e => { if(innerWidth<=780 && sidebar?.classList.contains('open') && !sidebar.contains(e.target) && !navToggle.contains(e.target)){ sidebar.classList.remove('open'); navToggle.setAttribute('aria-expanded','false'); }});

  $$('.copy-code').forEach(btn => btn.addEventListener('click', async () => {
    const code=btn.closest('.code-shell')?.querySelector('code')?.innerText || '';
    try { await navigator.clipboard.writeText(code); btn.textContent='Copied'; setTimeout(()=>btn.textContent='Copy',1200); }
    catch { const ta=document.createElement('textarea'); ta.value=code; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); btn.textContent='Copied'; setTimeout(()=>btn.textContent='Copy',1200); }
  }));

  const backdrop=$('#search-backdrop'), trigger=$('#search-trigger'), input=$('#search-input'), close=$('#search-close'), results=$('#search-results');
  const index=window.__QP_DOCS_INDEX__ || [];
  let hits=[]; let selected=-1;
  const openSearch=()=>{backdrop.hidden=false; document.body.style.overflow='hidden'; input.value=''; render(''); requestAnimationFrame(()=>input.focus());};
  const closeSearch=()=>{backdrop.hidden=true; document.body.style.overflow=''; trigger?.focus();};
  trigger?.addEventListener('click',openSearch); close?.addEventListener('click',closeSearch); backdrop?.addEventListener('click',e=>{if(e.target===backdrop)closeSearch()});
  results?.addEventListener('click',e=>{if(e.target.closest('.search-hit')){backdrop.hidden=true;document.body.style.overflow='';}});
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault(); backdrop.hidden?openSearch():closeSearch(); return}
    if(e.key==='/' && backdrop.hidden && !/input|textarea/i.test(document.activeElement?.tagName||'')){e.preventDefault();openSearch();return}
    if(!backdrop.hidden && e.key==='Escape'){e.preventDefault();closeSearch();return}
    if(!backdrop.hidden && (e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault(); if(!hits.length)return; selected=(selected+(e.key==='ArrowDown'?1:-1)+hits.length)%hits.length; drawHits();}
    if(!backdrop.hidden && e.key==='Enter' && selected>=0 && hits[selected]){ location.href=hits[selected].url; }
  });
  input?.addEventListener('input',()=>render(input.value));
  const score=(item,terms)=>{ const title=item.title.toLowerCase(), headings=item.headings.toLowerCase(), text=item.text.toLowerCase(); let s=0; for(const term of terms){ if(title===term)s+=50; else if(title.includes(term))s+=25; if(headings.includes(term))s+=12; const idx=text.indexOf(term); if(idx>=0)s+=Math.max(1,8-Math.floor(idx/500)); } return s; };
  function render(q){ const terms=q.trim().toLowerCase().split(/\s+/).filter(Boolean); selected=-1; hits=terms.length?index.map(x=>({...x,_score:score(x,terms)})).filter(x=>x._score>0).sort((a,b)=>b._score-a._score||a.title.localeCompare(b.title)).slice(0,18):[]; drawHits(); }
  function drawHits(){ if(!results)return; if(!hits.length){results.innerHTML='<p class="search-hint">'+(input?.value?'No matching documentation.':'Type a page, API symbol, method, concept, error, or solver class.')+'</p>';return} results.innerHTML=hits.map((h,i)=>`<a class="search-hit ${i===selected?'selected':''}" href="${h.url}"><strong>${escapeHTML(h.title)}</strong><small>${escapeHTML(h.category)} · ${escapeHTML(h.snippet)}</small></a>`).join(''); }
  const escapeHTML=s=>String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

  const tocLinks=$$('.toc a');
  if(tocLinks.length && 'IntersectionObserver' in window){ const byId=new Map(tocLinks.map(a=>[a.hash.slice(1),a])); const obs=new IntersectionObserver(entries=>{ for(const e of entries){ if(e.isIntersecting){tocLinks.forEach(a=>a.classList.remove('active')); byId.get(e.target.id)?.classList.add('active');}} },{rootMargin:'-80px 0px -70% 0px'}); $$('h2[id],h3[id],section.api-symbol[id]').forEach(h=>obs.observe(h)); }
})();
