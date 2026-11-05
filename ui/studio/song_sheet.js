/* Manual chord positions: clicking a character creates an explicit anchor. */
window.SongSheet = (() => {
  const esc = v => escH(String(v ?? ''));
  const shifts = new Map();
  const offset = row => shifts.get(row.song_id) || 0;
  function transpose(name, steps) {
    if (!steps) return name;
    const notes = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];
    const pitch = {C:0,D:2,E:4,F:5,G:7,A:9,B:11};
    return String(name).replace(/(^|\/)([A-G])([#b]?)/g, (_,prefix,n,acc) => prefix + notes[((pitch[n]+(acc==='#'?1:acc==='b'?-1:0)+steps)%12+12)%12]);
  }
  const widths = new Map(), playback = new Map();
  const columns = row => widths.get(row.song_id) || 42;
  function wrapLine(line, width) {
    if (!line.text || line.text.length <= width) return [line];
    const tokens = [...line.text.matchAll(/\S+/g)], pieces = [];
    let i = 0;
    while (i < tokens.length) {
      const start = i === 0 ? 0 : tokens[i].index;
      let j = i+1;
      while (j < tokens.length && tokens[j].index+tokens[j][0].length-start <= width) j++;
      if (j < tokens.length && j-i>2 && /^(не|ни|и|а|но|в|на|с|к|по|без|для|о|из|от)$/i.test(tokens[j-1][0])) j--;
      const end = j < tokens.length ? tokens[j].index : line.text.length;
      const text = line.text.slice(start,end).trimEnd();
      const marks = chordCandidates(line).filter(c=>c.char_index>=start && c.char_index<start+text.length).map(c=>({...c,char_index:c.char_index-start}));
      pieces.push({...line,text,chords:marks,cells:[],unplaced_chords:[],display_words:(line.display_words || []).filter(w=>w.char_start>=start && w.char_end<=start+text.length).map(w=>({...w,char_start:w.char_start-start,char_end:w.char_end-start}))});
      i=j;
    }
    return pieces;
  }
  function chordCandidates(line) {
    const positioned=line.chords?.length ? line.chords : (line.cells || []).flatMap(c=>(c.chords || []).map(name=>({name,char_index:c.char_index || 0})));
    if(!line.text || !line.unplaced_chords?.length) return positioned;
    const duration=Math.max(1,(line.end_ms || 0)-(line.start_ms || 0));
    return [...positioned,...line.unplaced_chords.map(c=>({...c,char_index:Math.max(0,Math.min(line.text.length-1,Math.floor((c.start_ms-(line.start_ms || 0))/duration*line.text.length)))}))];
  }
  function compactChords(line, steps, width=42) {
    let candidates=chordCandidates(line);
    if (!line.text && line.unplaced_chords?.length) {
      const events=line.unplaced_chords, a=events[0].start_ms || 0;
      const b=Math.max(a+1,...events.map(c=>c.end_ms || c.start_ms+1000));
      candidates=events.map(c=>({...c,char_index:Math.floor((c.start_ms-a)/(b-a)*(width-5))}));
    }
    const grouped=new Map();
    for (const c of candidates) {
      if (!c.name || c.name==='N') continue;
      const name=transpose(c.name,steps), at=Math.max(0,Math.min(c.char_index || 0,width-name.length));
      const weight=Math.max(1,(c.end_ms ?? ((c.start_ms || 0)+1000))-(c.start_ms || 0));
      const key=at+':'+name, old=grouped.get(key);
      grouped.set(key,{...c,name,at,end:at+name.length+1,weight:weight+(old?.weight || 0)});
    }
    // Weighted interval scheduling: one chord row, maximum sounding duration.
    const items=[...grouped.values()].sort((a,b)=>a.end-b.end || a.at-b.at);
    const best=[{weight:0,items:[]}];
    for(let i=0;i<items.length;i++) {
      const c=items[i];let previous=i-1;
      while(previous>=0 && items[previous].end>c.at) previous--;
      const take={weight:best[previous+1].weight+c.weight,items:[...best[previous+1].items,c]};
      best.push(take.weight>best[i].weight ? take : best[i]);
    }
    return best[items.length].items.sort((a,b)=>a.at-b.at);
  }
  function presentationLines(row, width=columns(row)) {
    // Collapse chord-only rows in the same vocal gap into one timed instrumental cue.
    // The gap is bounded by adjacent lyric phrases; chord events prove it is musical.
    const source=row.guitar_sheet || [], result=[];
    const duration=Math.max(Number(row.audio?.duration_ms)||0,...(row.phrases||[]).map(p=>Number(p.end_ms)||0));
    const sectionByPhrase=new Map();
    for(const section of row.sections||[]) {
      const phrase=row.phrases?.[section.phrase_indexes?.[0]];
      if(phrase) sectionByPhrase.set(phrase.phrase_id,section);
    }
    const targetName=section=>{
      const kind=section?.kind||'';
      const label=section?.label||'';
      if(kind==='chorus'||/припев/i.test(label)) return /возможн/i.test(label)?'Возможный припев':'Припев';
      if(kind==='verse'||/куплет/i.test(label)) return 'Куплет';
      if(kind==='prechorus'||/предприпев/i.test(label)) return 'Предприпев';
      if(kind==='bridge'||/бридж/i.test(label)) return 'Бридж';
      return 'Вокал';
    };
    for(let i=0;i<source.length;) {
      if(source[i].text) { result.push(...wrapLine(source[i],width)); i++; continue; }
      const first=i, group=[];
      while(i<source.length && !source[i].text) group.push(source[i++]);
      const events=group.flatMap(line=>line.unplaced_chords||[]);
      if(!events.length) continue;
      const previous=[...source.slice(0,first)].reverse().find(line=>line.text);
      const next=source.slice(i).find(line=>line.text);
      const eventStart=Math.min(...events.map(event=>Number(event.start_ms)||0));
      const eventEnd=Math.max(...events.map(event=>Number(event.end_ms)||Number(event.start_ms)||0));
      let start=previous ? Number(previous.end_ms) : 0;
      let end=next ? Number(next.start_ms) : duration;
      if(!Number.isFinite(start)) start=eventStart;
      if(!Number.isFinite(end)||end<=start) { start=eventStart; end=Math.max(eventEnd,eventStart+1000); }
      const phraseId=next?.source_phrase_ids?.[0]||next?.phrase_id;
      const section=sectionByPhrase.get(phraseId);
      result.push({...group[0],phrase_id:phraseId,instrumental:true,unplaced_chords:events,start_ms:start,end_ms:end,
        next_section_label:next?targetName(section):'Окончание'});
    }
    return result;
  }
  function sheetRows(row) {
    const parts = new Map((row.sections || []).map(s => [row.phrases?.[s.phrase_indexes?.[0]]?.phrase_id,s.label]));
    const lines = [], width=columns(row), seenParts=new Set();
    let stanzaLines = 0, hasLyrics = false;
    for (const line of presentationLines(row,width)) {
      const newPart=parts.has(line.phrase_id) && !seenParts.has(line.phrase_id);
      seenParts.add(line.phrase_id);
      if (lines.length && (newPart || stanzaLines >= 4)) { lines.push({kind:'space',text:''}); stanzaLines=0; }
      const chords=compactChords(line,offset(row),width);
      let text='';
      for (const c of chords) text=text.padEnd(c.at,' ')+c.name;
      if(text && !line.text && lines.length && lines.at(-1).kind!=='space') lines.push({kind:'space',text:''});
      const phraseId=line.source_phrase_ids?.[0]||line.phrase_id||'';
      if(text) lines.push({kind:'chords',text,phrase_id:phraseId});
      if(line.text) { lines.push({kind:'lyrics',text:line.text,words:line.display_words || [],phrase_id:phraseId});stanzaLines++;hasLyrics=true; }
      else if(text) { lines.push({kind:'instrumental',text:hasLyrics?(line.next_section_label==='Окончание'?'Окончание':'Проигрыш'):'Вступление',
        phrase_id:line.phrase_id,start_ms:line.start_ms,end_ms:line.end_ms,next_section_label:line.next_section_label});lines.push({kind:'space',text:''}); }
    }
    return lines;
  }
  function lyricHTML(line) {
    let cursor=0, html='';
    for(const w of line.words || []) {
      if(w.char_start<cursor || w.char_end>line.text.length) continue;
      html+=esc(line.text.slice(cursor,w.char_start));
      const hint=w.quality==='aligned' ? 'Подсветка по выравниванию слов' : 'Приблизительная подсветка по времени фразы';
      html+=`<span class="song-paper-word" data-word-id="${esc(w.word_id)}" data-phrase-id="${esc(w.phrase_id || line.phrase_id || '')}" data-char-start="${w.phrase_char_start ?? w.char_start}" data-char-end="${(w.phrase_char_start ?? w.char_start)+(w.char_end-w.char_start)}" data-start="${w.start_ms}" data-end="${w.end_ms}" title="${hint}">${esc(line.text.slice(w.char_start,w.char_end))}</span>`;
      cursor=w.char_end;
    }
    return html+esc(line.text.slice(cursor));
  }
  function updatePlayback(songId, ms, scroll=true) {
    playback.set(songId,ms);
    const paper=document.querySelector('.song-paper');
    if(!paper || paper.dataset.songId!==songId) return;
    const words=[...paper.querySelectorAll('.song-paper-word')];
    let selected=null;
    for(const word of words) {
      if(Number(word.dataset.start)<=ms && ms<Number(word.dataset.end) && (!selected || Number(word.dataset.start)>=Number(selected.dataset.start))) selected=word;
    }
    const instrumentals=[...paper.querySelectorAll('.song-paper-instrumental[data-start-ms][data-end-ms]')];
    let activeInstrumental=null;
    for(const marker of instrumentals) {
      const start=Number(marker.dataset.startMs), end=Number(marker.dataset.endMs);
      if(Number.isFinite(start)&&Number.isFinite(end)&&start<=ms&&ms<end&&(!activeInstrumental||start>=Number(activeInstrumental.dataset.startMs))) activeInstrumental=marker;
      marker.classList.toggle('playing-instrumental',marker===activeInstrumental);
      const parts=[...marker.querySelectorAll('.song-paper-cue-part')];
      parts.forEach(part=>part.classList.remove('countdown-lit'));
      const cue=countdownState(ms,start,end);
      if(marker===activeInstrumental&&cue) {
        parts.find(part=>Number(part.dataset.cueStep)===cue.count)?.classList.add('countdown-lit');
      }
    }
    const old=paper.querySelector('.song-paper-word.current-word');
    const previousLine=paper.querySelector('.song-paper-line.playing-line');
    if(selected===old && !activeInstrumental) return;
    old?.classList.remove('current-word');
    previousLine?.classList.remove('playing-line');
    selected?.classList.add('current-word');
    const line=(selected?.closest('.song-paper-line')||activeInstrumental?.closest('.song-paper-line'));
    line?.classList.add('playing-line');
    selected?.closest('.song-paper-lyrics')?.classList.add('playing-line');
    if(scroll && line && line!==previousLine) {
      line.scrollIntoView({block:'center',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
  }
  function countdownState(ms,start,end) {
    const duration=end-start;
    if(!Number.isFinite(ms)||!Number.isFinite(start)||!Number.isFinite(end)||duration<=0||ms<start||ms>=end) return null;
    const windowMs=Math.min(3000,duration), remaining=end-ms;
    if(remaining>windowMs)return null;
    const count=Math.max(1,Math.min(3,Math.ceil(remaining/(windowMs/3))));
    return {count,lit:4-count};
  }
  function currentWord(songId) {
    const paper=document.querySelector('.song-paper');
    if(!paper || paper.dataset.songId!==songId) return null;
    const word=paper.querySelector('.song-paper-word.current-word');
    const line=word?.closest('.song-paper-lyrics');
    if(!word || !line?.dataset.phraseId) return null;
    return {phraseId:word.dataset.phraseId || line.dataset.phraseId,wordId:word.dataset.wordId,text:word.textContent,
      charStart:Number(word.dataset.charStart),charEnd:Number(word.dataset.charEnd),
      startMs:Number(word.dataset.start),endMs:Number(word.dataset.end)};
  }
  function lineStarts(songId) {
    const paper=document.querySelector('.song-paper');
    if(!paper || paper.dataset.songId!==songId) return [];
    return [...paper.querySelectorAll('.song-paper-lyrics')].map(line=>{
      const times=[...line.querySelectorAll('.song-paper-word')].map(w=>Number(w.dataset.start)).filter(Number.isFinite);
      return times.length ? Math.min(...times) : null;
    }).filter(t=>t!==null);
  }
  function lineTarget(starts, ms, direction) {
    const times=[...new Set(starts)].filter(Number.isFinite).sort((a,b)=>a-b);
    let current=-1;
    // Tolerate sub-millisecond rounding of currentTime after a seek.
    while(current+1<times.length && times[current+1]<=ms+1) current++;
    const target=current+direction;
    return target>=0 && target<times.length ? times[target] : null;
  }
  let sheetObserver=null;
  function bindResponsive(row,render) {
    sheetObserver?.disconnect();
    if(typeof ResizeObserver==='undefined') return;
    const container=document.querySelector('.song-paper-scroll');
    if(!container) return;
    sheetObserver=new ResizeObserver(()=>{
      const body=document.querySelector('.song-paper-body');if(!body || !container.isConnected)return;
      const context=document.createElement('canvas').getContext('2d'), style=window.getComputedStyle(body);
      // The computed font shorthand can be empty with font-variant-ligatures.
      // Canvas would then silently measure its 10px default and overfill the row.
      context.font=`${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const lyricWidth=context.measureText('0').width;
      context.font=`700 ${style.fontSize} ${style.fontFamily}`;
      const charWidth=Math.max(lyricWidth,context.measureText('0').width)+(parseFloat(style.letterSpacing) || 0);
      const width=Math.max(1,Math.min(42,Math.floor(container.clientWidth/charWidth)-1));
      if(width===columns(row))return;
      widths.set(row.song_id,width);render();
      document.getElementById('song-copy-preview').value=plainText(row);
      if(playback.has(row.song_id))updatePlayback(row.song_id,playback.get(row.song_id),false);
      sheetObserver.disconnect();bindResponsive(row,render);
    });
    sheetObserver.observe(container);
  }
  function plainText(row) {
    const body=sheetRows(row).map(r=>r.text).join('\n').replace(/^\n+|\n+$/g, '');
    const names=[...new Set(sheetRows(row).filter(r=>r.kind==='chords')
      .flatMap(r=>r.text.trim().split(/\s+/)).filter(Boolean))];
    const fingerings=names.map(name=>{
      const shape=chordFingering(name);
      return `${name} — ${shape || 'аппликатура не найдена'}`;
    });
    const sources=sourceLinks(row).map(source=>`${source.title} — ${source.url}`);
    const extras=[];
    if(fingerings.length) extras.push(`Аппликатуры (струны 1→6; число — лад, 0 — открытая струна, x — не играть):\n${fingerings.join('\n')}`);
    if(sources.length) extras.push(`Источники:\n${sources.join('\n')}`);
    return [body,...extras].filter(Boolean).join('\n\n');
  }
  const NOTE_PC={C:0,D:2,E:4,F:5,G:7,A:9,B:11};
  const STRING_PC=[4,11,7,2,9,4]; // standard tuning, first (thinnest) string to sixth
  const CHORD_INTERVALS={
    '':[0,4,7],maj:[0,4,7],major:[0,4,7],m:[0,3,7],min:[0,3,7],minor:[0,3,7],
    '7':[0,4,7,10],maj7:[0,4,7,11],major7:[0,4,7,11],m7:[0,3,7,10],min7:[0,3,7,10],
    mMaj7:[0,3,7,11],m7b5:[0,3,6,10],dim:[0,3,6],dim7:[0,3,6,9],aug:[0,4,8],
    sus:[0,5,7],sus4:[0,5,7],sus2:[0,2,7], '6':[0,4,7,9],m6:[0,3,7,9],
    add9:[0,2,4,7], '9':[0,2,4,7,10],m9:[0,2,3,7,10],maj9:[0,2,4,7,11],
    '11':[0,2,4,5,7,10],m11:[0,2,3,5,7,10],maj11:[0,2,4,5,7,11],
    '13':[0,2,4,7,9,10],m13:[0,2,3,7,9,10],maj13:[0,2,4,7,9,11]
  };
  const fingeringCache=new Map();
  function chordFingering(symbol) {
    if(fingeringCache.has(symbol)) return fingeringCache.get(symbol);
    const match=String(symbol).match(/^([A-G])([#b]?)(.*?)(?:\/([A-G])([#b]?))?$/i);
    if(!match) { fingeringCache.set(symbol,'');return ''; }
    const [,letter,accidental,suffix,slashLetter,slashAccidental]=match;
    const root=NOTE_PC[letter.toUpperCase()];
    const pc=(base,acc)=>((base+(acc==='#'?1:acc==='b'?-1:0))%12+12)%12;
    const rootPc=pc(root,accidental), bassPc=slashLetter?pc(NOTE_PC[slashLetter.toUpperCase()],slashAccidental):rootPc;
    let intervals=CHORD_INTERVALS[suffix];
    if(!intervals) {
      const normalized=suffix.toLowerCase();
      intervals=CHORD_INTERVALS[normalized];
    }
    if(!intervals) { fingeringCache.set(symbol,'');return ''; }
    const tones=[...new Set(intervals.map(n=>(rootPc+n)%12))];
    if(slashLetter&&!tones.includes(bassPc)) { fingeringCache.set(symbol,'');return ''; }
    const requiredMask=tones.reduce((mask,n)=>mask|(1<<n),0);
    const options=STRING_PC.map(open=>[-1,...Array.from({length:13},(_,f)=>f).filter(f=>tones.includes((open+f)%12))]);
    let best=null;
    function visit(string,mask,positions,lowestPc) {
      if(string===6) {
        if(mask!==requiredMask || lowestPc!==bassPc) return;
        const played=positions.filter(f=>f>=0), fretted=played.filter(f=>f>0);
        const min=fretted.length?Math.min(...fretted):0,max=fretted.length?Math.max(...fretted):0;
        const spread=max-min;
        const distinct=new Set(fretted).size;
        const score=fretted.reduce((sum,f)=>sum+f,0)+max*1.25+distinct*0.7+
          positions.filter(f=>f<0).length*7+Math.max(0,spread-4)*2-
          positions.filter(f=>f===0).length*0.8;
        if(!best||score<best.score) best={score,positions:[...positions]};
        return;
      }
      for(const fret of options[string]) {
        const nextMask=fret<0?mask:mask|(1<<((STRING_PC[string]+fret)%12));
        // Strings are visited 1→6; every played lower string replaces the
        // current bass candidate, leaving the lowest sounding string at leaf.
        const nextLowest=fret<0?lowestPc:(STRING_PC[string]+fret)%12;
        visit(string+1,nextMask,[...positions,fret],nextLowest);
      }
    }
    // Search from the first string through the sixth so bass-note validation is exact.
    visit(0,0,[],null);
    const result=best ? best.positions.map(f=>f<0?'x':String(f)).join('-') : '';
    fingeringCache.set(symbol,result);return result;
  }
  function sourceLinks(row) {
    const all = [...(row.source_candidates || [])];
    if (row.source_ref) all.push({url:row.source_ref,title:'Исходная запись',kind:'youtube'});
    const rank = c => /аккорд|chord/i.test(c.title || '') ? 0 : c.kind === 'lyrics' ? 1 : 2;
    all.sort((a,b)=>rank(a)-rank(b));
    const seen = new Set(), links = [];
    for (const c of all) {
      const verified = c.source_check?.status === 'available' ||
        (c.kind === 'youtube' && c.url && c.url === row.source_ref && row.audio?.path);
      if (!verified) continue;
      let url;
      try { url = new URL(c.url); } catch (_) { continue; }
      if (!['http:','https:'].includes(url.protocol)) continue;
      url.hash=''; const key=url.href;
      if (seen.has(key)) continue;
      seen.add(key); links.push({url:key,title:c.title || url.hostname});
      if (links.length===3) break;
    }
    return links;
  }
  function instrumentalCueMarkup(text) {
    const chars=[...text];
    if(text.trim().toLocaleLowerCase('ru-RU')==='проигрыш') {
      return [['Про',3],['и',2],['грыш',1]].map(([part,step])=>`<span class="song-paper-cue-part" data-cue-step="${step}">${esc(part)}</span>`).join('');
    }
    const pieces=[];
    let cursor=0;
    for(let index=0;index<3;index++) {
      const remaining=chars.length-cursor, slots=3-index;
      const size=Math.ceil(remaining/slots);
      pieces.push(chars.slice(cursor,cursor+size).join(''));
      cursor+=size;
    }
    return pieces.map((part,index)=>`<span class="song-paper-cue-part" data-cue-step="${3-index}">${esc(part)}</span>`).join('');
  }
  function editButton(phraseId,kind) {
    if(!phraseId) return '';
    const title=kind==='chords'?'Редактировать аккорды этой фразы':'Редактировать текст этой фразы';
    return `<button type="button" class="btn song-paper-edit song-paper-edit-${kind}" data-song-edit-phrase="${esc(phraseId)}" data-song-edit-kind="${kind}" title="${title}" aria-label="${title}">${icon('edit')}</button>`;
  }
  function sheetHTML(row) {
    if (!row.guitar_sheet?.length) return '';
    const links = sourceLinks(row);
    const rows=sheetRows(row), blocks=[];
    for(let i=0;i<rows.length;i++) {
      const r=rows[i];
      if(r.kind==='space') { blocks.push('<div class="song-paper-space" aria-hidden="true"></div>');continue; }
      const chords=r.kind==='chords' ? `${editButton(r.phrase_id,'chords')}<div class="song-paper-chords">${esc(r.text)}</div>` : '';
      const lyric=r.kind==='lyrics' ? r : ['lyrics','instrumental'].includes(rows[i+1]?.kind) ? rows[++i] : null;
      let lyricHTMLContent='';
      if(lyric?.kind==='lyrics') lyricHTMLContent=lyricHTML(lyric);
      else if(lyric?.kind==='instrumental') {
        lyricHTMLContent=`<span class="song-paper-instrumental-label">${instrumentalCueMarkup(lyric.text)}</span>`;
      }
      const timing=lyric?.kind==='instrumental'&&Number.isFinite(lyric.start_ms)&&Number.isFinite(lyric.end_ms)
        ? ` data-start-ms="${lyric.start_ms}" data-end-ms="${lyric.end_ms}" data-next-section="${esc(lyric.next_section_label||'вокала')}"` : '';
      const lyricEdit=lyric?.kind==='lyrics' ? editButton(lyric.phrase_id,'lyrics') : '';
      blocks.push(`<div class="song-paper-line${chords?' has-chords':''}${lyricEdit?' has-lyrics-edit':''}">${chords}${lyric ? `${lyricEdit}<div class="song-paper-${lyric.kind}"${lyric.phrase_id ? ` data-phrase-id="${esc(lyric.phrase_id)}"` : ''}${timing}>${lyric.kind==='lyrics' ? lyricHTMLContent : lyric.kind==='instrumental' ? lyricHTMLContent : esc(lyric.text)}</div>` : ''}</div>`);
    }
    return `<article class="song-paper" data-song-id="${esc(row.song_id)}" aria-label="Итоговый текст с аккордами"><h3 class="song-paper-title">${esc(row.title)}${row.artist ? ' — '+esc(row.artist) : ''}</h3>${(row.guitar_sheet || []).some(l=>(l.display_words || []).some(w=>w.quality!=='aligned')) ? '<p class="song-timing-note">Подсветка слов без точных меток — приблизительно по времени фразы.</p>' : ''}<div class="song-paper-scroll" tabindex="0" aria-label="Лист песни; прокрутка длинных строк"><div class="song-paper-body">${blocks.join('')}</div></div>${links.length ? `<footer class="song-paper-sources"><span>Источники</span>${links.map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a>`).join('')}</footer>` : ''}</article>`;
  }
  function icon(name) {
    const paths = {
      edit:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z"/>',
      trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6m4-6v6"/>',
      down:'<path d="M12 4v16m-6-6 6 6 6-6"/>',
      up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
      previous:'<path d="M5 5v14m14-14L7 12l12 7Z"/>',
      next:'<path d="M19 5v14M5 5l12 7-12 7Z"/>',
      copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
      text:'<path d="M4 5h16M12 5v15M8 20h8M4 5v3m16-3v3"/>',
      play:'<path d="m8 4 12 8-12 8Z"/>',
      karaoke:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-4 0h8M18 4l3 2v5"/>',
      pause:'<path d="M8 4v16M16 4v16"/>',
      stop:'<rect x="5" y="5" width="14" height="14" rx="1"/>',
      mic:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-4 0h8"/>',
      system:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M7 10h2l2-3 3 7 2-4h1"/>',
      upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4"/>'
    };
    return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ''}</svg>`;
  }
  const shiftLabel = row => offset(row)>0 ? `+${offset(row)}` : String(offset(row));
  function toolbar(row) {
    const button = (id, glyph, title, extra='') => `<button type="button" class="btn song-icon-btn" id="${id}" title="${title}" aria-label="${title}" ${extra}>${icon(glyph)}</button>`;
    return `<div class="song-sheet-tools" role="group" aria-label="Тональность, копирование и прослушивание">
      <div class="song-tool-group">
        ${button('song-key-down','down','Понизить аккорды на один полутон; звук записи не меняется')}
        <span id="song-key-label" role="status" title="Сдвиг аккордов относительно исходной тональности, в полутонах" aria-label="Сдвиг аккордов: ${shiftLabel(row)} полутонов">${shiftLabel(row)}</span>
        ${button('song-key-up','up','Повысить аккорды на один полутон; звук записи не меняется')}
      </div>
      <div class="song-tool-group song-copy-group" role="group" aria-label="Текст и копирование">
        ${button('song-show-text','text','Показать текст с аккордами для копирования в выбранной тональности','aria-controls="song-copy-panel" aria-expanded="false"')}
        ${button('song-copy-sheet','copy','Скопировать текст с аккордами, аппликатурами и ссылками на источники')}
      </div>
      <div class="song-tool-group" role="group" aria-label="Прослушивание песни">
        ${button('song-sheet-previous','previous','Перейти к началу предыдущей строки песни; при паузе воспроизведение остаётся на паузе','disabled')}
        ${button('song-sheet-play','play',row.capabilities?.audio ? 'Проиграть всю песню в исходной тональности записи' : 'Для прослушивания добавьте аудиозапись',row.capabilities?.audio ? '' : 'disabled')}
        ${button('song-sheet-next','next','Перейти к началу следующей строки песни; при паузе воспроизведение остаётся на паузе','disabled')}
        ${button('song-sheet-stop','stop','Остановить воспроизведение и вернуться к началу песни','disabled')}
      </div>
      <span id="song-copy-status" role="status"></span><span id="song-sheet-play-status" role="status"></span>
      </div><div class="song-current-edit-wrap"><div class="song-copy-preview" id="song-copy-panel" hidden><p class="hint">Текст в выбранной тональности, аппликатуры использованных аккордов и проверенные ссылки на источники.</p><textarea id="song-copy-preview" aria-label="Текст с аккордами, аппликатурами и источниками" readonly rows="16" wrap="off">${esc(plainText(row))}</textarea></div>
      <section class="song-live-editor" id="song-live-editor" aria-label="Правка текущей фразы и аккордов" hidden>
        <div class="song-live-editor-head"><div><b>Правка во время прослушивания</b><p class="hint" id="song-live-edit-target"></p></div><div class="actions"><button type="button" class="btn primary small" id="song-live-edit-save" title="Сохранить исправленную фразу и её аккорды">Сохранить текст и аккорды</button><button type="button" class="btn small" id="song-live-edit-close" title="Закрыть редактор, не меняя сохранённый текст">Закрыть</button></div></div>
        <div class="song-live-preview" id="song-live-preview"><b>Фраза после правки</b><p id="song-live-edit-preview" aria-live="polite"></p></div>
        <p class="song-save-status" id="song-live-edit-status" role="status">После сохранения песня останется на паузе.</p>
        <input type="hidden" id="song-live-edit-mode" value="phrase">
        <label class="f" id="song-live-text-field">Текст фразы<textarea id="song-live-edit-text" rows="2" maxlength="4000"></textarea></label>
        <label class="song-reviewed" id="song-live-reviewed-field"><input id="song-live-reviewed" type="checkbox"> Фраза проверена по записи</label>
        <div class="song-live-chord-head"><b>Аккорды текущей фразы</b><button type="button" class="btn small" id="song-live-chord-add">Добавить аккорд</button></div>
        <div id="song-live-chords"></div>
      </section></div>`;
  }
  function bindToolbar(row, getCurrent, render) {
    if (!document.getElementById('song-key-up')) return;
    bindResponsive(row,render);
    function change(delta) {
      shifts.set(row.song_id,offset(row)+delta); render();
      const label = document.getElementById('song-key-label');
      label.textContent = shiftLabel(row);
      label.setAttribute('aria-label', `Сдвиг аккордов: ${shiftLabel(row)} полутонов`);
      document.getElementById('song-copy-preview').value = plainText(getCurrent());
      document.getElementById('song-copy-status').textContent = '';
      if(playback.has(row.song_id))updatePlayback(row.song_id,playback.get(row.song_id),false);
      bindResponsive(row,render);
    }
    document.getElementById('song-key-up').onclick = () => change(1);
    document.getElementById('song-key-down').onclick = () => change(-1);
    function showText(open) {
      document.getElementById('song-copy-panel').hidden = !open;
      document.getElementById('song-show-text').setAttribute('aria-expanded', String(open));
    }
    document.getElementById('song-show-text').onclick = () => showText(document.getElementById('song-copy-panel').hidden);
    document.getElementById('song-copy-sheet').onclick = async () => {
      const text = plainText(getCurrent());
      document.getElementById('song-copy-preview').value = text;
      try { await navigator.clipboard.writeText(text); document.getElementById('song-copy-status').textContent='Скопировано'; }
      catch (_) { const box=document.getElementById('song-copy-preview');box.value=text;showText(true);box.focus();box.select();document.getElementById('song-copy-status').textContent='Нажмите ⌘C / Ctrl+C, чтобы скопировать выделенный текст'; }
    };
  }
  function html(row) {
    if (!row.phrases?.length) return '';
    return `<details class="song-chord-editor"><summary>Править аккорды и положение над текстом</summary>
      <p class="hint">Выберите смену или добавьте новую. Время задаётся от начала песни. Чтобы поставить аккорд над гласной распева, нажмите на эту букву.</p>
      <label class="f">Смена<select id="song-chord-select"><option value="new">Новая смена</option>${(row.chords || []).map((c,i) => c.name === 'N' ? '' : `<option value="${i}">${(c.start_ms/1000).toFixed(2)} с — ${esc(c.name)}</option>`).join('')}</select></label>
      <label class="f">Аккорд<input id="song-chord-name" placeholder="Am, E7, Bb"></label>
      <label class="f">Время, секунды<input id="song-chord-time" type="number" min="0" step="0.01" value="0"></label>
      <p id="song-chord-letters" class="song-chord-letters"></p>
      <div class="actions"><button class="btn small" id="song-chord-save">Сохранить аккорд</button><button class="btn small" id="song-chord-remove">Удалить смену</button><button class="btn small" id="song-sheet-export">Скачать ChordPro</button></div>
      <p id="song-chord-status" role="status"></p></details>
      <details class="song-chord-editor"><summary>Завершить проверку песни</summary>
      <label class="song-reviewed"><input id="song-check-all-lyrics" type="checkbox"> Текст полностью проверен по записи</label>
      <label class="song-reviewed"><input id="song-check-all-chords" type="checkbox"> Аккорды и места смены проверены по записи</label>
      <button class="btn small" id="song-finish-review">Отметить песню готовой</button><p id="song-finish-status" role="status"></p></details>`;
  }
  function bind(row, getCurrent, accept) {
    const select = document.getElementById('song-chord-select');
    if (!select) return;
    const name = document.getElementById('song-chord-name'), time = document.getElementById('song-chord-time');
    const status = document.getElementById('song-chord-status'), letters = document.getElementById('song-chord-letters');
    let anchor = null;
    function showLetters() {
      const ms = Number(time.value)*1000;
      const phrase = getCurrent().phrases.find(p => p.start_ms <= ms && ms < p.end_ms);
      if (!phrase) { anchor = null; letters.textContent = 'В этом месте нет фразы — аккорд останется в проигрыше.'; return; }
      letters.innerHTML = Array.from(phrase.text).map((c,i) => `<button type="button" data-letter="${i}" class="song-letter ${anchor?.phrase_id === phrase.phrase_id && anchor?.grapheme_index === i ? 'on' : ''}" aria-label="${esc(c)} — позиция ${i+1}">${c === ' ' ? '&nbsp;' : esc(c)}</button>`).join('');
      letters.querySelectorAll('[data-letter]').forEach(b => b.onclick = () => {
        anchor = {kind:'manual', phrase_id:phrase.phrase_id, grapheme_index:Number(b.dataset.letter)};
        showLetters();
      });
    }
    select.onchange = () => {
      const event = getCurrent().chords?.[Number(select.value)];
      name.value = event?.name || ''; time.value = (event?.start_ms || 0)/1000;
      anchor = event?.anchor || null; showLetters();
    };
    time.oninput = () => { anchor = null; showLetters(); };
    async function save(remove) {
      try {
        const current = getCurrent(), events = structuredClone(current.chords || []).filter(c => c.name !== 'N');
        const original = current.chords?.[Number(select.value)];
        const index = original ? events.findIndex(c => c.start_ms === original.start_ms && c.name === original.name) : -1;
        if (remove) { if (index < 0) throw Error('Сначала выберите смену'); events.splice(index,1); }
        else {
          const event = {name:name.value.trim(),start_ms:Math.round(Number(time.value)*1000),anchor,verified:true,method:'manual'};
          if (index < 0) events.push(event); else events[index] = event;
        }
        events.sort((a,b) => a.start_ms-b.start_ms);
        const saved = await core.call('song_edit_chords',{song_id:row.song_id,base_revision:current.revision,chords:events});
        accept(saved); window.loadSongs(); status.textContent = 'Аккорд сохранён';
      } catch (e) { status.textContent = e.message; }
    }
    document.getElementById('song-chord-save').onclick = () => save(false);
    document.getElementById('song-chord-remove').onclick = () => save(true);
    document.getElementById('song-finish-review').onclick = async () => {
      const status = document.getElementById('song-finish-status');
      try {
        const current = getCurrent();
        const saved = await core.call('song_finish_review',{song_id:row.song_id,base_revision:current.revision,
          lyrics_checked:document.getElementById('song-check-all-lyrics').checked,
          chords_checked:document.getElementById('song-check-all-chords').checked});
        accept(saved); window.loadSongs(); status.textContent='Песня отмечена как проверенная';
      } catch(e) { status.textContent=e.message; }
    };
    document.getElementById('song-sheet-export').onclick = () => {
      const current = getCurrent();
      const lines = [`{title: ${current.title.replace(/[{}\n]/g,' ')}}`, `{artist: ${(current.artist || '').replace(/[{}\n]/g,' ')}}`,
        '{comment: Предварительный подбор Dewiar. B=си, Bb=си-бемоль.}'];
      for (const line of presentationLines(current)) {
        const chords=compactChords(line,offset(current),columns(current));
        let text='', cursor=0;
        for(const c of chords) { const at=Math.min(c.at,(line.text || '').length); text+=(line.text || '').slice(cursor,at)+`[${c.name}]`;cursor=at; }
        lines.push(text+(line.text || '').slice(cursor));
        if(line.instrumental && chords.length) lines.push('{comment: Проигрыш}');
      }
      const url = URL.createObjectURL(new Blob([lines.join('\n')+'\n'],{type:'text/plain;charset=utf-8'}));
      const link = document.createElement('a'); link.href=url; link.download=(current.title.replace(/[\\/:*?"<>|]/g,'_') || 'song')+'.cho'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    };
    showLetters();
  }
  return {html,bind,toolbar,bindToolbar,transpose,offset,plainText,sheetRows,sheetHTML,sourceLinks,chordFingering,icon,compactChords,wrapLine,presentationLines,updatePlayback,countdownState,currentWord,lineStarts,lineTarget};
})();
