/* Editorial and video libraries are task types, nested in «Задачи». */
(() => {
  const tasks=document.getElementById('p-tasks');
  const articles=document.getElementById('tasks-articles-tab'),video=document.getElementById('tasks-video-tab');
  const articlePanel=document.getElementById('p-articles'),videoPanel=document.getElementById('p-video');
  if(!tasks||!articles||!video||!articlePanel||!videoPanel)return;
  const work=document.getElementById('tasks-work-content'),songs=document.getElementById('p-songs'),sites=document.getElementById('p-sites');
  const buttons=[document.getElementById('tasks-work-tab'),document.getElementById('tasks-songs-tab'),document.getElementById('tasks-sites-tab'),articles,video];
  function select(which){
    document.querySelector('nav button[data-p="tasks"]')?.click();
    const target=which==='articles'?articles:video;
    buttons.forEach(button=>button?.setAttribute('aria-pressed',String(button===target)));
    if(work)work.hidden=true;if(songs)songs.hidden=true;if(sites)sites.hidden=true;
    articlePanel.hidden=which!=='articles';videoPanel.hidden=which!=='video';
    articlePanel.classList.toggle('cur',which==='articles');videoPanel.classList.toggle('cur',which==='video');
    target.scrollIntoView({block:'start'});
  }
  articles.addEventListener('click',()=>select('articles'));
  video.addEventListener('click',()=>select('video'));
  for(const id of ['tasks-work-tab','tasks-songs-tab','tasks-sites-tab'])
    document.getElementById(id)?.addEventListener('click',()=>{
      articlePanel.hidden=true;videoPanel.hidden=true;articlePanel.classList.remove('cur');videoPanel.classList.remove('cur');articles.setAttribute('aria-pressed','false');video.setAttribute('aria-pressed','false');
    });
  window.openContentTaskTab=select;
  document.addEventListener('DOMContentLoaded',()=>{
    if(location.hash==='#articles'||location.hash==='#video')select(location.hash.slice(1));
  });
})();
