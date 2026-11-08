/* Manual chord positions: clicking a character creates an explicit anchor. */
window.SongSheet = (() => {
  const esc = v => escH(String(v ?? ''));
  const shifts = new Map();
  const offset = row => shifts.get(row.song_id) || 0;
  function transpose(name, steps) {
    if (!steps) return name;
    const notes = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];
    const pitch = {C:0,D:2,E:4,F:5,G:7,A:9,B:11,H:11};
    return String(name).replace(/(^|\/)([A-H])([#b]?)/g, (_,prefix,n,acc) => prefix + notes[((pitch[n]+(acc==='#'?1:acc==='b'?-1:0)+steps)%12+12)%12]);
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
  function vocalBounds(line) {
    const words=(line.display_words||line.words||[]).filter(word=>Number.isFinite(Number(word.start_ms))&&Number.isFinite(Number(word.end_ms))&&Number(word.end_ms)>Number(word.start_ms));
    if(!words.length) return {start:Number(line.start_ms)||0,end:Number(line.end_ms)||Number(line.start_ms)||0};
    return {start:Math.min(...words.map(word=>Number(word.start_ms))),end:Math.max(...words.map(word=>Number(word.end_ms)))};
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
      grouped.set(key,{...c,...voicingAtShift(c,steps),source_name:c.name,name,at,end:at+name.length+1,weight:weight+(old?.weight || 0)});
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
      const events=group.flatMap(line=>line.unplaced_chords||[]).filter(event=>event.name && event.name!=='N');
      if(!events.length && !group.some(line=>line.instrumental)) continue;
      const previous=[...source.slice(0,first)].reverse().find(line=>line.text);
      const next=source.slice(i).find(line=>line.text);
      const eventStart=Math.min(...events.map(event=>Number(event.start_ms)||0));
      const eventEnd=Math.max(...events.map(event=>Number(event.end_ms)||Number(event.start_ms)||0));
      const explicit=group.every(line=>line.instrumental && Number.isFinite(line.start_ms) && line.end_ms>line.start_ms);
      let start=explicit ? Math.min(...group.map(line=>line.start_ms)) : previous ? vocalBounds(previous).end : 0;
      let end=explicit ? Math.max(...group.map(line=>line.end_ms)) : next ? vocalBounds(next).start : duration;
      if(!Number.isFinite(start)) start=eventStart;
      if(!Number.isFinite(end)||end<=start) { start=eventStart; end=Math.max(eventEnd,eventStart+1000); }
      const phraseId=next?.source_phrase_ids?.[0]||next?.phrase_id;
      const section=sectionByPhrase.get(phraseId);
      result.push({...group[0],phrase_id:phraseId,instrumental:true,unplaced_chords:events,start_ms:start,end_ms:end,
        next_section_label:next?targetName(section):'Окончание'});
    }
    return result;
  }
  function sourceStanzaHints(row) {
    // A verified lyrics page often preserves stanza boundaries as blank lines.
    // Keep those boundaries as a formatting hint for the copied sheet; the
    // audio/phrase timeline remains authoritative for words and timestamps.
    const refs=[...(row.text_reference?.references||[]), ...(row.text_reference?.suggested_sources||[])];
    const text=refs.find(ref=>ref?.plain_lyrics)?.plain_lyrics || '';
    if(!text) return [];
    const normalize=value=>String(value||'').toLowerCase().replace(/ё/g,'е').replace(/[^a-zа-я0-9]+/gi,' ').trim();
    return text.split(/\n\s*\n+/).map(paragraph=>paragraph.split(/\n+/).map(normalize).filter(Boolean))
      .filter(lines=>lines.length).map(lines=>({lines,words:normalize(lines.join(' ')).split(/\s+/).filter(Boolean)}));
  }
  function sourceStanzaBreak(row, lineText, state) {
    const hints=state.hints;
    if(!hints.length || !lineText) return false;
    const normalize=value=>String(value||'').toLowerCase().replace(/ё/g,'е').replace(/[^a-zа-я0-9]+/gi,' ').trim();
    const words=normalize(lineText).split(/\s+/).filter(Boolean);
    if(!words.length) return false;
    let paragraph=hints[state.index];
    if(!paragraph) return false;
    if(state.line>=paragraph.lines.length && hints[state.index+1]) {
      state.index++; state.line=0; state.used=0; paragraph=hints[state.index];
      return true;
    }
    const overlap=(a,b)=>a.filter(word=>b.includes(word)).length/Math.max(1,a.length);
    const currentWords=paragraph.lines[state.line] ? paragraph.lines[state.line].split(/\s+/).filter(Boolean) : [];
    const currentScore=overlap(words,currentWords);
    const nextParagraph=hints[state.index+1];
    const nextScore=nextParagraph ? overlap(words,nextParagraph.words) : 0;
    // A source line may differ slightly from the audio transcription, so use
    // token overlap rather than exact equality. We only advance on a clear
    // match; otherwise keep the normal four-line fallback.
    if(nextParagraph && currentScore<0.25 && nextScore>currentScore+0.15) {
      state.index++; state.line=0; state.used=0; paragraph=hints[state.index];
    }
    if(currentScore>=0.25 || state.line===0 && !state.used) {
      state.line++;
      state.used++;
    }
    return false;
  }
  function sheetRows(row) {
    const parts = new Map((row.sections || []).map(s => [row.phrases?.[s.phrase_indexes?.[0]]?.phrase_id,s.label]));
    const lines = [], width=columns(row), seenParts=new Set();
    let stanzaLines = 0, hasLyrics = false;
    const sourceState={hints:sourceStanzaHints(row),index:0,line:0,used:0};
    for (const line of presentationLines(row,width)) {
      const newPart=parts.has(line.phrase_id) && !seenParts.has(line.phrase_id);
      seenParts.add(line.phrase_id);
      if (lines.length && (newPart || stanzaLines >= 4)) { lines.push({kind:'space',text:''}); stanzaLines=0; }
      const chords=compactChords(line,offset(row),width);
      let text='';
      for (const c of chords) text=text.padEnd(c.at,' ')+c.name;
      if(text && !line.text && lines.length && lines.at(-1).kind!=='space') lines.push({kind:'space',text:''});
      const phraseId=line.source_phrase_ids?.[0]||line.phrase_id||'';
      if(text) lines.push({kind:'chords',text,phrase_id:phraseId,song_id:row.song_id,chord_marks:chords.map(c=>({name:c.name,at:c.at,
        song_id:row.song_id,source_name:c.source_name,key_shift:offset(row),
        start_ms:c.start_ms,end_ms:c.end_ms,
        voicing_fingering:c.voicing_fingering||c.fingering||c.voicing?.fingering,
        voicing_probability:c.voicing_probability??c.fingering_probability??c.voicing?.probability}))});
      if(line.text) {
        const sourceBreak=sourceStanzaBreak(row,line.text,sourceState);
        if(sourceBreak && lines.length && lines.at(-1).kind!=='space') { lines.push({kind:'space',text:''}); stanzaLines=0; }
        lines.push({kind:'lyrics',text:line.text,words:line.display_words || [],phrase_id:phraseId,
          display_line_id:line.phrase_id,source_phrase_ids:line.source_phrase_ids || [phraseId],source_spans:line.source_spans || []});stanzaLines++;hasLyrics=true;
      }
      else if(text || line.instrumental) { lines.push({kind:'instrumental',text:hasLyrics?(line.next_section_label==='Окончание'?'Окончание':'Проигрыш'):'Вступление',
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
    const old=paper.querySelector('.song-paper-word.current-word');
    const oldChord=paper.querySelector('.song-chord-tip.current-chord');
    const instrumentals=[...paper.querySelectorAll('.song-paper-instrumental[data-start-ms][data-end-ms]')];
    let activeInstrumental=null;
    for(const marker of instrumentals) {
      const start=Number(marker.dataset.startMs), end=Number(marker.dataset.endMs);
      if(Number.isFinite(start)&&Number.isFinite(end)&&start<=ms&&ms<end&&(!activeInstrumental||start>=Number(activeInstrumental.dataset.startMs))) activeInstrumental=marker;
    }
    for(const marker of instrumentals) {
      const start=Number(marker.dataset.startMs), end=Number(marker.dataset.endMs);
      marker.classList.toggle('playing-instrumental',marker===activeInstrumental);
      const parts=[...marker.querySelectorAll('.song-paper-cue-part')];
      parts.forEach(part=>part.classList.remove('countdown-lit'));
      const cue=countdownState(ms,start,end);
      if(marker===activeInstrumental&&cue) {
        if(cue.single) parts.forEach(part=>part.classList.add('countdown-lit'));
        else parts.find(part=>Number(part.dataset.cueStep)===cue.count)?.classList.add('countdown-lit');
      }
    }
    const chordSpans=[...paper.querySelectorAll('.song-chord-tip[data-start-ms]')];
    let activeChord=null;
    for(const chord of chordSpans) {
      const start=Number(chord.dataset.startMs);
      const end=Number(chord.dataset.endMs);
      const nextStart=Number(chord.dataset.nextStartMs);
      const effectiveEnd=Number.isFinite(end)&&end>start ? end : (Number.isFinite(nextStart) ? nextStart : Infinity);
      const isActive=Number.isFinite(start)&&start<=ms&&ms<effectiveEnd;
      if(isActive&&(!activeChord||start>=Number(activeChord.dataset.startMs))) activeChord=chord;
    }
    for(const chord of chordSpans) chord.classList.toggle('current-chord',chord===activeChord);
    const previousLine=paper.querySelector('.song-paper-line.playing-line');
    // A confirmed musical break wins over estimated lyric cues. A sustained
    // chord can belong to a previous row, so it must not control gap scrolling.
    if(activeInstrumental) selected=null;
    const line=(selected?.closest('.song-paper-line')||activeInstrumental?.closest('.song-paper-line')||activeChord?.closest('.song-paper-line'));
    if(selected===old && activeChord===oldChord && line===previousLine && !activeInstrumental) return;
    old?.classList.remove('current-word');
    for(const active of paper.querySelectorAll('.playing-line')) active.classList.remove('playing-line');
    selected?.classList.add('current-word');
    activeChord?.classList.add('current-chord');
    line?.classList.add('playing-line');
    selected?.closest('.song-paper-lyrics')?.classList.add('playing-line');
    if(scroll && line && line!==previousLine) {
      line.scrollIntoView({block:'center',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
  }
  function countdownState(ms,start,end) {
    const duration=end-start;
    if(!Number.isFinite(ms)||!Number.isFinite(start)||!Number.isFinite(end)||duration<=0||ms<start||ms>=end) return null;
    if(duration<3000) return {count:1,lit:3,single:true};
    const windowMs=3000, remaining=end-ms;
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
      // The editing pencils reserve a gutter inside each line. Measuring the
      // outer scroll area alone lets the last chord spill past narrow screens.
      const line=body.querySelector('.song-paper-line > .song-paper-chords, .song-paper-line > .song-paper-lyrics');
      const available=Math.min(container.clientWidth,line?.clientWidth || container.clientWidth);
      const width=Math.max(1,Math.min(42,Math.floor(available/charWidth)-1));
      if(width===columns(row))return;
      widths.set(row.song_id,width);render();
      document.getElementById('song-copy-preview').value=plainText(row);
      if(playback.has(row.song_id))updatePlayback(row.song_id,playback.get(row.song_id),false);
      sheetObserver.disconnect();bindResponsive(row,render);
    });
    sheetObserver.observe(container);
  }
  function copyBody(row) {
    const rows=sheetRows(row), out=[];
    for(let i=0;i<rows.length;i++) {
      const current=rows[i];
      if(current.kind==='space') {
        if(out.length && out.at(-1)!=='') out.push('');
        continue;
      }
      if(current.kind==='instrumental') {
        let next=i+1, chordLines=[];
        while(next<rows.length && rows[next].kind!=='lyrics') {
          if(rows[next].kind==='chords' && rows[next].text) chordLines.push(rows[next].text.trim());
          next++;
        }
        if(rows[next]?.kind==='lyrics') {
          // Keep the preceding chord row and the break's chord row separate
          // from the lyric.  Labels such as "Проигрыш" are presentation-only:
          // the copied notation should contain only chords and words.
          if(chordLines.length) out.push(chordLines.join(' '));
          out.push(rows[next].text);
          i=next;
        } else if(chordLines.length) out.push(chordLines.join(' '));
        continue;
      }
      out.push(current.text);
    }
    return out.join('\n').replace(/^\n+|\n+$/g, '');
  }
  function plainText(row) {
    const body=copyBody(row);
    const names=[...new Set(sheetRows(row).filter(r=>r.kind==='chords')
      .flatMap(r=>r.text.trim().split(/\s+/)).filter(Boolean))];
    const evidenceFor=name=>[...(row.chords||[]),...(row.guitar_sheet||[]).flatMap(line=>line.chords||[])]
      .find(chord=>transpose(chord.name,offset(row))===name);
    const fingerings=names.map(name=>{
      const source=evidenceFor(name)||{};
      const evidence={...source,...voicingAtShift(source,offset(row)),song_id:row.song_id}, pinned=pinnedVoicing(name,row.song_id), shape=chordFingering(name,evidence);
      return `${name} — ${shape || 'позиция не определена'}${pinned?' (закреплено)':''}`;
    });
    const sources=sourceLinks(row).map(source=>`${source.title} — ${source.url}`);
    const header=[];
    if(String(row.title||'').trim()) header.push(String(row.title).trim());
    if(String(row.search_query||'').trim()) header.push(`Поисковый запрос: ${String(row.search_query).trim()}`);
    const extras=[];
    if(fingerings.length) extras.push(`Аппликатуры (струны 1→6):\n${fingerings.join('\n')}`);
    if(sources.length) extras.push(`Источники:\n${sources.join('\n')}`);
    const tags=[...(row.tags||[])].filter(Boolean);
    if(tags.length) extras.push(`Теги: ${tags.join(', ')}`);
    return [...header,body,...extras].filter(Boolean).join('\n\n');
  }
  const NOTE_PC={C:0,D:2,E:4,F:5,G:7,A:9,B:11,H:11};
  const STRING_PC=[4,11,7,2,9,4]; // standard tuning, first (thinnest) string to sixth
  const STRING_MIDI=[64,59,55,50,45,40], MAX_CHORD_FRET=15;
  // Standard movable forms, strings 1→6. Muted strings stay muted; open
  // strings become the barre when a form moves. B/H uses the A-family barre.
  const CHORD_FORMS=[
    [0,'0-1-0-2-3-x'],[7,'3-0-0-0-2-3'],[2,'2-3-2-0-x-x'],[9,'0-2-2-2-0-x'],[4,'0-0-1-2-2-0'],[11,'2-4-4-4-2-x'],
    [0,'3-4-5-5-3-x'],[7,'3-3-3-5-5-3'],[2,'1-3-2-0-x-x'],[9,'0-1-2-2-0-x'],[4,'0-0-0-2-2-0'],[11,'2-3-4-4-2-x'],
    [0,'0-1-3-2-3-x'],[7,'1-0-0-0-2-3'],[2,'2-1-2-0-x-x'],[9,'0-2-0-2-0-x'],[4,'0-0-1-0-2-0'],[11,'2-0-2-1-2-x'],
    [0,'0-0-0-2-3-x'],[7,'2-0-0-0-2-3'],[2,'2-2-2-0-x-x'],[9,'0-2-1-2-0-x'],[4,'0-0-1-1-2-0'],
    [2,'1-1-2-0-x-x'],[9,'0-1-0-2-0-x'],[4,'0-0-0-0-2-0'],[11,'2-3-2-4-2-x'],
    [2,'0-3-2-0-x-x'],[9,'0-0-2-2-0-x'],[2,'3-3-2-0-x-x'],[9,'0-3-2-2-0-x'],[4,'0-0-2-2-2-0'],
    [0,'0-3-0-2-3-x'],[4,'2-0-1-2-2-0'],[9,'0-2-2-2-0-2'],[4,'0-2-1-2-2-0'],
    [9,'2-1-2-2-0-x'],[4,'0-2-0-2-2-0']
  ].map(([root,shape])=>({root,frets:shape.split('-').map(f=>f==='x'?-1:Number(f))}));
  function fingeringFingers(positions) {
    // A barre can bridge higher frets/mutes, but cannot cross an open or
    // lower-fretted sounding string. Count each separate group as one finger.
    let fingers=0;
    for(const fret of new Set(positions.filter(f=>f>0))) {
      let grouped=false;
      for(const value of positions) {
        if(value>=0 && value<fret) grouped=false;
        if(value===fret && !grouped) { fingers++;grouped=true; }
      }
    }
    return fingers;
  }
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
  const PINNED_VOICING_PREFIX='dewiar:song-chord-voicing:';
  function pinnedVoicing(symbol,songId='') {
    try {
      const value=localStorage.getItem(PINNED_VOICING_PREFIX+`${songId||'global'}:${symbol}`);
      return exactVoicing({voicing_fingering:value}) || '';
    } catch (_) { return ''; }
  }
  function setPinnedVoicing(symbol,songId,shape) {
    try {
      const key=PINNED_VOICING_PREFIX+`${songId||'global'}:${symbol}`;
      if(shape) localStorage.setItem(key,shape); else localStorage.removeItem(key);
    } catch (_) {}
  }
  function chordFingerings(symbol,limit=Infinity) {
    const cacheKey=String(symbol);
    if(fingeringCache.has(cacheKey)) return fingeringCache.get(cacheKey).slice(0,limit);
    const match=String(symbol).match(/^([A-H])([#b]?)(.*?)(?:\/([A-H])([#b]?))?$/i);
    if(!match) { fingeringCache.set(cacheKey,[]);return []; }
    const [,letter,accidental,suffix,slashLetter,slashAccidental]=match;
    const root=NOTE_PC[letter.toUpperCase()];
    const pc=(base,acc)=>((base+(acc==='#'?1:acc==='b'?-1:0))%12+12)%12;
    const rootPc=pc(root,accidental), bassPc=slashLetter?pc(NOTE_PC[slashLetter.toUpperCase()],slashAccidental):rootPc;
    let intervals=CHORD_INTERVALS[suffix];
    if(!intervals) {
      const normalized=suffix.toLowerCase();
      intervals=CHORD_INTERVALS[normalized];
    }
    if(!intervals) { fingeringCache.set(cacheKey,[]);return []; }
    const tones=[...new Set(intervals.map(n=>(rootPc+n)%12))];
    if(slashLetter&&!tones.includes(bassPc)) { fingeringCache.set(cacheKey,[]);return []; }
    const requiredMask=tones.reduce((mask,n)=>mask|(1<<n),0);
    const options=STRING_PC.map(open=>[-1,...Array.from({length:MAX_CHORD_FRET+1},(_,f)=>f).filter(f=>tones.includes((open+f)%12))]);
    const found=new Map();
    function add(positions,template=false) {
        const notes=positions.flatMap((f,i)=>f<0?[]:[STRING_MIDI[i]+f]);
        const mask=notes.reduce((value,n)=>value|(1<<(n%12)),0);
        if(mask!==requiredMask || Math.min(...notes)%12!==bassPc) return;
        const played=positions.filter(f=>f>=0), fretted=played.filter(f=>f>0);
        const min=fretted.length?Math.min(...fretted):0,max=fretted.length?Math.max(...fretted):0;
        const spread=max-min;
        if(max>MAX_CHORD_FRET || spread>3 || fingeringFingers(positions)>4) return;
        const distinct=new Set(fretted).size;
        const score=fretted.reduce((sum,f)=>sum+f,0)+max*1.25+distinct*0.7+
          positions.filter(f=>f<0).length*7+Math.max(0,spread-4)*2-
          positions.filter(f=>f===0).length*0.8;
        const shape=positions.map(f=>f<0?'x':String(f)).join('-');
        const previous=found.get(shape);
        found.set(shape,{score,shape,position:min,template:template||!!previous?.template});
    }
    for(const form of CHORD_FORMS) for(let shift=-MAX_CHORD_FRET;shift<=MAX_CHORD_FRET;shift++) {
      if(((form.root+shift)%12+12)%12!==rootPc) continue;
      const positions=form.frets.map(f=>f<0?-1:f+shift);
      if(positions.some((f,i)=>form.frets[i]>=0&&(f<0||f>MAX_CHORD_FRET))) continue;
      add(positions,true);
    }
    function visit(string,mask,positions,min,max) {
      if(string===6) { if(mask===requiredMask) add(positions);return; }
      for(const fret of options[string]) {
        const nextMin=fret>0?Math.min(min,fret):min, nextMax=Math.max(max,fret);
        if(nextMin!==Infinity && nextMax-nextMin>3) continue;
        const nextMask=fret<0?mask:mask|(1<<((STRING_PC[string]+fret)%12));
        visit(string+1,nextMask,[...positions,fret],nextMin,nextMax);
      }
    }
    visit(0,0,[],Infinity,0);
    const ranked=[...found.values()].sort((a,b)=>a.score-b.score||a.shape.localeCompare(b.shape));
    // Retain every movable form and two useful alternatives at each position,
    // instead of filling the list with near-identical low-fret fingerings.
    const perPosition=new Map();
    const selected=ranked.filter(item=>{
      const count=perPosition.get(item.position)||0;perPosition.set(item.position,count+1);
      return item.template||count<2;
    });
    selected.sort((a,b)=>Number(b.template)-Number(a.template)||a.score-b.score||a.shape.localeCompare(b.shape));
    const result=selected.map(item=>item.shape);
    // Cache the complete selection, even when a tooltip asks for just one.
    fingeringCache.set(cacheKey,result);return result.slice(0,limit);
  }
  function exactVoicing(evidence) {
    const value=evidence?.voicing_fingering || evidence?.fingering || evidence?.voicing?.fingering;
    if(Array.isArray(value)) return value.length===6 && value.every(item=>/^(?:x|[0-9]|1[0-9]|2[0-4])$/.test(String(item))) ? value.join('-') : '';
    const shape=String(value||'').trim();
    return /^(?:x|[0-9]|1[0-9]|2[0-4])(?:-(?:x|[0-9]|1[0-9]|2[0-4])){5}$/.test(shape) ? shape : '';
  }
  function voicingAtShift(evidence,shift=0) {
    const shape=exactVoicing(evidence), delta=shift-(Number(evidence?.voicing_key_shift)||0);
    if(!shape || !delta)return {};
    const values=shape.split('-').map(value=>value==='x'?'x':Number(value)+delta);
    const valid=values.every(value=>value==='x'||Number.isInteger(value)&&value>=0&&value<=24);
    return {voicing_fingering:valid?values.join('-'):'',fingering:'',voicing:undefined,
      voicing_key_shift:shift,...(!valid?{voicing_probability:undefined}:{})};
  }
  function voicingProbability(evidence) {
    const raw=evidence?.voicing_probability ?? evidence?.fingering_probability ?? evidence?.voicing?.probability;
    const value=Number(raw);
    return Number.isFinite(value) && value>=0 && value<=1 ? Math.round(value*100) : null;
  }
  function chordFingering(symbol,evidence) {
    const pinned=pinnedVoicing(symbol,evidence?.song_id);
    return pinned || exactVoicing(evidence) || chordFingerings(symbol,1)[0]||'';
  }
  function chordTooltip(symbol,evidence) {
    const pinned=pinnedVoicing(symbol,evidence?.song_id), exact=exactVoicing(evidence), shape=pinned || exact || chordFingering(symbol), probability=exact?voicingProbability(evidence):null;
    if(!shape) return `${symbol}: позиция на грифе не определена`;
    const description=String(evidence?.description||'').trim();
    return `${symbol}${description?` · ${description}`:''} · 1→6: ${shape}${pinned?' · закреплено':exact?'':' · оценочная позиция'}${probability===null?'':` · вероятность ${probability}%`}`;
  }
  function chordVariants(symbol,evidence) {
    const result=[], add=shape=>{ const normalized=exactVoicing({voicing_fingering:shape}); if(normalized&&!result.includes(normalized)) result.push(normalized); };
    add(pinnedVoicing(symbol,evidence?.song_id)); add(exactVoicing(evidence));
    chordFingerings(symbol).forEach(add);
    return result;
  }
  function inferChordName(shape) {
    const values=String(shape||'').split('-'), sounded=[];
    values.forEach((value,index)=>{ if(value==='x'||value==='') return; const fret=Number(value); if(!Number.isInteger(fret)||fret<0) return; sounded.push((STRING_PC[index]+fret)%12); });
    if(!sounded.length) return '?';
    const set=new Set(sounded), bass=sounded.at(-1), noteNames=['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B'];
    const suffixes=Object.entries(CHORD_INTERVALS).filter(([suffix])=>suffix==='m7b5'||suffix==='maj7'||suffix==='m7'||suffix==='7'||suffix==='m'||suffix==='');
    let best=null;
    for(const root of new Set(sounded)) for(const [suffix,intervals] of suffixes) {
      const expected=new Set(intervals.map(interval=>(root+interval)%12));
      const exact=expected.size===set.size&&[...expected].every(note=>set.has(note));
      const coverage=[...set].filter(note=>expected.has(note)).length/Math.max(set.size,expected.size);
      const score=(exact?100:0)+coverage*20+(root===bass?5:0)-(expected.size-set.size)*2;
      if(!best||score>best.score) best={score,name:noteNames[root]+suffix};
    }
    return best?.name || noteNames[bass]+'?';
  }
  function fretNote(string, fret) {
    const names=['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B'];
    return Number.isInteger(string)&&string>=0&&string<6&&Number.isInteger(fret)&&fret>=0
      ? names[(STRING_PC[string]+fret)%12] : '';
  }
  function chordDiagramHTML(symbol,shape,index,total) {
    const values=String(shape||'').split('-'), labels=['e','B','G','D','A','E'];
    const fretted=values.map(Number).filter(Number.isFinite).filter(value=>value>0);
    const minFret=fretted.length ? Math.min(...fretted) : 1;
    const maxFret=fretted.length ? Math.max(...fretted) : 0;
    const base=maxFret<=4 ? 1 : Math.min(minFret,MAX_CHORD_FRET-3);
    const fretLabels=Array.from({length:4},(_,column)=>base+column);
    const rows=labels.map((label,row)=>{
      const value=values[row]??'x', fret=Number(value);
      const muted=String(value).toLowerCase()==='x';
      const cells=fretLabels.map(number=>{
        const title=`${fretNote(row,number)} · струна ${row+1}, лад ${number}`;
        return `<button type="button" class="song-chord-fret ${fret===number?'filled':''}" data-chord-fret="${number}" data-chord-string="${row}" title="${title}" aria-label="${title}" aria-pressed="${fret===number}"></button>`;
      }).join('');
      const title=muted?`Струна ${row+1} выключена. Открыть: ${fretNote(row,0)}`
        :`${fretNote(row,fret)} · струна ${row+1}, ${fret===0?'открытая':'лад '+fret}. ${fret===0?'Выключить':'Отпустить'} струну`;
      return `<div class="song-chord-row"><span class="song-chord-string">${label}</span><span class="song-chord-grid${muted?' is-muted':''}"${muted?' aria-label="Струна выключена; ни один лад не играется"':''}>${cells}</span><button type="button" class="song-chord-value" data-chord-string-toggle="${row}" title="${title}" aria-label="${title}">${esc(value)}</button></div>`;
    }).join('');
    return `<div class="song-chord-fret-head"><span></span>${fretLabels.map(number=>`<b>${number}</b>`).join('')}<span></span></div><div class="song-chord-diagram">${rows}</div><div class="song-chord-variants"><button type="button" class="song-chord-nav" data-chord-prev title="Предыдущая аппликатура" aria-label="Предыдущая аппликатура">‹</button><span>${index+1}/${total}</span><button type="button" class="song-chord-nav" data-chord-next title="Следующая аппликатура" aria-label="Следующая аппликатура">›</button></div>`;
  }
  let chordPopoverBound=false, chordPopoverState=null;
  const chordApplyHandlers=new Map();
  function chordPopoverElement() {
    let popover=document.querySelector('.song-chord-popover');
    if(popover) return popover;
    popover=document.createElement('div'); popover.className='song-chord-popover'; popover.hidden=true;
    popover.setAttribute('role','dialog');popover.setAttribute('aria-label','Правка аккорда');
    document.body.appendChild(popover);
    // A button can be replaced by a render during this very click. Its detached
    // target is no longer inside the popover; never let page handlers treat it
    // as an outside click or a click on the lyric underneath.
    popover.addEventListener('click',event=>event.stopPropagation());
    return popover;
  }
  function positionChordPopover(popover,trigger) {
    if(!trigger?.getBoundingClientRect) return;
    const rect=trigger.getBoundingClientRect(); popover.style.visibility='hidden'; popover.hidden=false;
    const box=popover.getBoundingClientRect(), gap=8;
    const left=Math.max(8,Math.min(window.innerWidth-box.width-8,rect.left));
    const top=rect.bottom+gap+box.height<=window.innerHeight ? rect.bottom+gap : Math.max(8,rect.top-gap-box.height);
    popover.style.left=`${left}px`; popover.style.top=`${top}px`; popover.style.visibility='visible';
  }
  function hideChordPopover() {
    const popover=document.querySelector('.song-chord-popover');
    if(popover) popover.hidden=true;
    if(chordPopoverState?.trigger) {
      chordPopoverState.trigger.setAttribute('aria-expanded','false');
      if(chordPopoverState.trigger.isConnected)chordPopoverState.trigger.focus({preventScroll:true});
    }
    chordPopoverState=null;
  }
  function renderChordPopover(focusSelector='') {
    const state=chordPopoverState, popover=chordPopoverElement();
    if(!state||!state.variants.length) return;
    const shape=state.variants[state.index];
    popover.innerHTML=`<div class="song-chord-popover-head"><button type="button" class="song-chord-popover-btn" data-chord-transpose="-1" title="Понизить аккорд на полтона" aria-label="Понизить аккорд на полтона">${icon('down')}</button><input class="song-chord-name-input" data-chord-name-edit value="${esc(state.customName??state.symbol)}" maxlength="24" size="6" spellcheck="false" aria-label="Название аккорда" title="Название аккорда, например Bm или G7"><button type="button" class="song-chord-popover-btn" data-chord-transpose="1" title="Повысить аккорд на полтона" aria-label="Повысить аккорд на полтона">${icon('up')}</button><div><button type="button" class="song-chord-popover-btn ${state.pinned?'is-pinned':''}" data-chord-pin aria-pressed="${state.pinned}" title="${state.pinned?'Открепить аппликатуру при подтверждении':'Закрепить аппликатуру при подтверждении'}" aria-label="Закрепить выбранную аппликатуру">${icon('pin')}</button><button type="button" class="song-chord-popover-btn" data-chord-apply title="Сохранить название и аппликатуру аккорда" aria-label="Сохранить аккорд">${icon('check')}</button><button type="button" class="song-chord-popover-btn" data-chord-close title="Отменить изменения и закрыть окно аккорда" aria-label="Отменить изменения и закрыть окно аккорда">×</button></div></div>${chordDiagramHTML(state.symbol,shape,state.index,state.variants.length)}<p class="song-chord-popover-status" role="status"></p>`;
    const redraw=selector=>renderChordPopover(selector);
    const nameEditor=popover.querySelector('[data-chord-name-edit]');
    nameEditor.addEventListener('input',()=>{
      const symbol=nameEditor.value.replace(/\s+/g,'').replace(/^[a-h]/,s=>s.toUpperCase());
      state.customName=symbol;
      const variants=chordVariants(symbol,{song_id:state.songId});
      if(!variants.length)return;
      state.symbol=symbol;state.variants=variants;state.variantNames=variants.map(()=>symbol);state.index=0;state.pinned=false;
      redraw('[data-chord-name-edit]');
      const updated=popover.querySelector('[data-chord-name-edit]');
      updated.setSelectionRange(updated.value.length,updated.value.length);
    });
    nameEditor.addEventListener('keydown',event=>{ if(event.key==='Enter'){ event.preventDefault(); nameEditor.blur(); } });
    popover.querySelector('[data-chord-pin]').onclick=()=>{ state.pinned=!state.pinned; redraw('[data-chord-pin]'); };
    popover.querySelector('[data-chord-close]').onclick=()=>hideChordPopover();
    popover.querySelectorAll('[data-chord-transpose]').forEach(button=>button.onclick=()=>{
      const symbol=transpose(state.symbol,Number(button.dataset.chordTranspose)||0);
      const variants=chordVariants(symbol,{song_id:state.songId});
      if(!variants.length)return;
      state.symbol=symbol;state.customName=symbol;state.variants=variants;state.variantNames=variants.map(()=>symbol);state.index=0;state.pinned=false;
      redraw(`[data-chord-transpose="${button.dataset.chordTranspose}"]`);
    });
    popover.querySelectorAll('[data-chord-fret]').forEach(button=>button.onclick=()=>{
      const values=shape.split('-'), string=Number(button.dataset.chordString), fret=Number(button.dataset.chordFret);
      values[string]=values[string]===String(fret)?'0':String(fret); state.variants[state.index]=values.join('-'); state.symbol=inferChordName(state.variants[state.index]); state.customName=state.symbol; state.variantNames[state.index]=state.symbol;state.pinned=false;
      redraw(`[data-chord-string="${string}"][data-chord-fret="${fret}"]`);
    });
    popover.querySelectorAll('[data-chord-string-toggle]').forEach(button=>button.onclick=()=>{
      const values=shape.split('-'), string=Number(button.dataset.chordStringToggle), current=values[string];
      values[string]=current==='x'?'0':current==='0'?'x':'0'; state.variants[state.index]=values.join('-'); state.symbol=inferChordName(state.variants[state.index]); state.customName=state.symbol; state.variantNames[state.index]=state.symbol;state.pinned=false;
      redraw(`[data-chord-string-toggle="${string}"]`);
    });
    const moveVariant=(delta,selector)=>{ state.index=(state.index+delta+state.variants.length)%state.variants.length;state.symbol=state.variantNames[state.index];state.customName=state.symbol; redraw(selector); };
    popover.querySelector('[data-chord-prev]').onclick=()=>moveVariant(-1,'[data-chord-prev]');
    popover.querySelector('[data-chord-next]').onclick=()=>moveVariant(1,'[data-chord-next]');
    popover.querySelector('[data-chord-apply]').onclick=async()=>{
      const status=popover.querySelector('[role="status"]');
      popover.querySelectorAll('button').forEach(button=>button.disabled=true);
      try {
        if(!state.customName || !/^[A-G](?:#|b)?(?:m7b5|(?:m|maj|min|dim|aug|sus|add)?\d*)(?:\/[A-G](?:#|b)?)?$/.test(state.customName))throw Error('Введите название аккорда сверху, например Bm или G7.');
        const apply=state.trigger.applySongChord || chordApplyHandlers.get(state.songId);
        if(!apply)throw Error('Редактор песни недоступен. Изменения оставлены в окне.');
        const refresh=await apply({trigger:state.trigger,original:state.original,symbol:state.customName,name:state.customName,description:state.description||'',shape:state.variants[state.index]});
        // No localStorage writes while experimenting: cancellation is a real rollback.
        setPinnedVoicing(state.customName,state.songId,state.pinned?state.variants[state.index]:'');
        if(typeof refresh==='function')refresh();
        hideChordPopover();
      } catch(error) { status.textContent=error.message||'Не удалось применить аккорд.'; }
      finally { popover.querySelectorAll('button').forEach(button=>button.disabled=false); }
    };
    positionChordPopover(popover,state.trigger);
    if(focusSelector)(popover.querySelector(focusSelector)||popover.querySelector('[data-chord-apply]')).focus({preventScroll:true});
  }
  function showChordPopover(trigger) {
    // Once opened, this is an editing draft. Hovering another chord, pressing a
    // grid button, scrolling or clicking outside cannot discard or replace it.
    if(!trigger || chordPopoverState) return;
    const symbol=(trigger.dataset.chordName||trigger.textContent||'').trim(); if(!symbol) return;
    const evidence={song_id:trigger.dataset.songId||'',voicing_fingering:trigger.dataset.chordFingering||'',voicing_probability:trigger.dataset.chordProbability};
    const variants=chordVariants(symbol,evidence); if(!variants.length) return;
    const pinnedShape=pinnedVoicing(symbol,evidence.song_id); let index=Math.max(0,variants.indexOf(pinnedShape));
    chordPopoverState={trigger,symbol,customName:symbol,description:trigger.dataset.chordDescription||'',original:symbol,songId:evidence.song_id,variants,variantNames:variants.map(()=>symbol),index,pinned:!!pinnedShape};
    trigger.setAttribute('aria-expanded','true'); renderChordPopover();
  }
  function bindChordPopovers() {
    if(chordPopoverBound || typeof document==='undefined' || !document.addEventListener || !document.body) return;
    chordPopoverBound=true;
    document.addEventListener('pointerover',event=>{ const trigger=event.target?.closest?.('.song-chord-tip'); if(trigger) showChordPopover(trigger); });
    document.addEventListener('click',event=>{ const trigger=event.target?.closest?.('.song-chord-tip'); if(trigger) showChordPopover(trigger); });
    document.addEventListener('keydown',event=>{
      const trigger=event.target?.closest?.('.song-chord-tip');
      if(trigger&&(event.key==='Enter'||event.key===' ')) {
        event.preventDefault();showChordPopover(trigger);
        chordPopoverElement().querySelector('[data-chord-fret]')?.focus({preventScroll:true});
      }
    });
  }
  function chordLineHTML(line) {
    if(!line.chord_marks?.length) return esc(line.text);
    let cursor=0,html='';
    for(let index=0; index<line.chord_marks.length; index++) {
      const chord=line.chord_marks[index];
      const at=Math.max(cursor,Math.min(line.text.length,Number(chord.at)||0));
      html+=esc(line.text.slice(cursor,at));
      const start=Number(chord.start_ms), end=Number(chord.end_ms);
      const nextStart=Number(line.chord_marks[index+1]?.start_ms);
      const timing=Number.isFinite(start) ? ` data-start-ms="${start}"${Number.isFinite(end)?` data-end-ms="${end}"`:''}${Number.isFinite(nextStart)?` data-next-start-ms="${nextStart}"`:''}` : '';
      const chordEvidence={...chord,song_id:chord.song_id||line.song_id};
      const shape=exactVoicing(chordEvidence);
      const attrs=` data-chord-name="${esc(chord.name)}" data-source-chord-name="${esc(chord.source_name||chord.name)}" data-chord-description="${esc(chord.description||'')}" data-chord-key-shift="${Number(chord.key_shift)||0}" data-song-id="${esc(chordEvidence.song_id||'')}"${shape?` data-chord-fingering="${esc(shape)}"`:''}${Number.isFinite(Number(chord.voicing_probability))?` data-chord-probability="${chord.voicing_probability}"`:''}`;
      const tooltip=chordTooltip(chord.name,chordEvidence);
      html+=`<span class="song-chord-tip" title="${esc(tooltip)}" role="button" tabindex="0" aria-label="${esc(tooltip)}" aria-expanded="false"${attrs}${timing}>${esc(chord.name)}</span>`;
      cursor=at+chord.name.length;
    }
    return html+esc(line.text.slice(cursor));
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
  function editButton(phraseId,kind,lineId='') {
    if(!phraseId) return '';
    const title=kind==='chords'?'Редактировать аккорды этой фразы':'Редактировать текст этой фразы';
    const lineAttr=kind==='lyrics'&&lineId?` data-song-edit-line="${esc(lineId)}"`:'';
    return `<button type="button" class="btn song-paper-edit song-paper-edit-${kind}" data-song-edit-phrase="${esc(phraseId)}"${lineAttr} data-song-edit-kind="${kind}" title="${title}" aria-label="${title}">${icon('edit')}</button>`;
  }
  function sheetHTML(row) {
    if (!row.guitar_sheet?.length) return '';
    const links = sourceLinks(row);
    const rows=sheetRows(row), blocks=[];
    for(let i=0;i<rows.length;i++) {
      const r=rows[i];
      if(r.kind==='space') { blocks.push('<div class="song-paper-space" aria-hidden="true"></div>');continue; }
      const chords=r.kind==='chords' ? `${editButton(r.phrase_id,'chords')}<div class="song-paper-chords">${chordLineHTML(r)}</div>` : '';
      const lyric=['lyrics','instrumental'].includes(r.kind) ? r : ['lyrics','instrumental'].includes(rows[i+1]?.kind) ? rows[++i] : null;
      let lyricHTMLContent='';
      if(lyric?.kind==='lyrics') lyricHTMLContent=lyricHTML(lyric);
      else if(lyric?.kind==='instrumental') {
        lyricHTMLContent=`<span class="song-paper-instrumental-label">${instrumentalCueMarkup(lyric.text)}</span>`;
      }
      const timing=lyric?.kind==='instrumental'&&Number.isFinite(lyric.start_ms)&&Number.isFinite(lyric.end_ms)
        ? ` data-start-ms="${lyric.start_ms}" data-end-ms="${lyric.end_ms}" data-next-section="${esc(lyric.next_section_label||'вокала')}"` : '';
      const lyricEdit=lyric?.kind==='lyrics' ? editButton(lyric.phrase_id,'lyrics',lyric.display_line_id) : '';
      blocks.push(`<div class="song-paper-line${chords?' has-chords':''}${lyricEdit?' has-lyrics-edit':''}">${chords}${lyric ? `${lyricEdit}<div class="song-paper-${lyric.kind}"${lyric.phrase_id ? ` data-phrase-id="${esc(lyric.phrase_id)}"` : ''}${timing}>${lyric.kind==='lyrics' ? lyricHTMLContent : lyric.kind==='instrumental' ? lyricHTMLContent : esc(lyric.text)}</div>` : ''}</div>`);
    }
    return `<article class="song-paper" data-song-id="${esc(row.song_id)}" aria-label="Итоговый текст с аккордами"><h3 class="song-paper-title">${esc(row.title)}${row.artist ? ' — '+esc(row.artist) : ''}</h3>${(row.guitar_sheet || []).some(l=>(l.display_words || []).some(w=>w.quality!=='aligned')) ? '<p class="song-timing-note">Подсветка слов без точных меток — приблизительно по времени фразы.</p>' : ''}<div class="song-paper-scroll" tabindex="0" aria-label="Лист песни; прокрутка длинных строк"><div class="song-paper-body">${blocks.join('')}</div></div>${links.length ? `<footer class="song-paper-sources"><span>Источники</span>${links.map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a>`).join('')}</footer>` : ''}</article>`;
  }
  function icon(name) {
    const paths = {
      refresh:'<path d="M20 7a8 8 0 0 0-14-2L3 8m0-5v5h5M4 17a8 8 0 0 0 14 2l3-3m0 5v-5h-5"/>',
      edit:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z"/>',
      trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6m4-6v6"/>',
      down:'<path d="M12 4v16m-6-6 6 6 6-6"/>',
      up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
      left:'<path d="m15 18-6-6 6-6"/>',
      right:'<path d="m9 18 6-6-6-6"/>',
      previous:'<path d="M5 5v14m14-14L7 12l12 7Z"/>',
      next:'<path d="M19 5v14M5 5l12 7-12 7Z"/>',
      copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
      text:'<path d="M4 5h16M12 5v15M8 20h8M4 5v3m16-3v3"/>',
      play:'<path d="m8 4 12 8-12 8Z"/>',
      karaoke:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-4 0h8M18 4l3 2v5"/>',
      pause:'<path d="M8 4v16M16 4v16"/>',
      stop:'<rect x="5" y="5" width="14" height="14" rx="1"/>',
      speedDown:'<path d="M4 8h12m-4-4 4 4-4 4M20 16H8m4-4-4 4 4 4"/>',
      speedUp:'<path d="M4 16h12m-4-4 4 4-4 4M20 8H8m4-4-4 4 4 4"/>',
      pin:'<path d="m14 4 6 6-3 3-2-2-4 4v4l-2 2-1-5-5-1 2-2h4l4-4-2-2Z"/>',
      check:'<path d="m5 12 4 4L19 6"/>',
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
        ${button('song-key-down','down','Понизить аккорды и тональность воспроизведения на один полутон')}
        <span id="song-key-label" role="status" title="Сдвиг аккордов относительно исходной тональности, в полутонах" aria-label="Сдвиг аккордов: ${shiftLabel(row)} полутонов">${shiftLabel(row)}</span>
        ${button('song-key-up','up','Повысить аккорды и тональность воспроизведения на один полутон')}
      </div>
      <div class="song-tool-group song-copy-group" role="group" aria-label="Текст и копирование">
        ${button('song-show-text','text','Показать текст с аккордами для копирования в выбранной тональности','aria-controls="song-copy-panel" aria-expanded="false"')}
        ${button('song-copy-sheet','copy','Скопировать текст с аккордами, аппликатурами и ссылками на источники')}
      </div>
      <div class="song-tool-group" role="group" aria-label="Прослушивание песни">
        ${button('song-speed-down','speedDown','Замедлить воспроизведение на 10 процентов; тональность не меняется')}
        <span id="song-speed-label" role="status" title="Скорость воспроизведения записи и минусовки" aria-label="Скорость воспроизведения">×1.00</span>
        ${button('song-speed-up','speedUp','Ускорить воспроизведение на 10 процентов; тональность не меняется')}
        ${button('song-sheet-previous','previous','Перейти к началу предыдущей строки песни; при паузе воспроизведение остаётся на паузе','disabled')}
        ${button('song-sheet-play','play',row.capabilities?.audio ? 'Проиграть всю песню в исходной тональности записи' : 'Для прослушивания добавьте аудиозапись',row.capabilities?.audio ? '' : 'disabled')}
        ${button('song-sheet-play-instrumental','karaoke',row.karaoke?.state === 'ready' ? 'Проиграть всю песню с минусовкой' : 'Минусовка ещё не готова',row.karaoke?.state === 'ready' ? '' : 'disabled')}
        ${button('song-sheet-next','next','Перейти к началу следующей строки песни; при паузе воспроизведение остаётся на паузе','disabled')}
        ${button('song-sheet-stop','stop','Остановить воспроизведение и вернуться к началу песни','disabled')}
      </div>
      <span id="song-copy-status" role="status"></span><span id="song-sheet-play-status" role="status"></span>
      </div><div class="song-current-edit-wrap"><div class="song-copy-preview" id="song-copy-panel" hidden><p class="hint">Текст в выбранной тональности, аппликатуры использованных аккордов и проверенные ссылки на источники.</p><textarea id="song-copy-preview" aria-label="Текст с аккордами, аппликатурами и источниками" readonly rows="16" wrap="off">${esc(plainText(row))}</textarea></div>
      <section class="song-live-editor" id="song-live-editor" aria-label="Правка текущей фразы и аккордов" hidden>
        <audio id="song-live-edit-audio" preload="auto" aria-label="Прослушивание текущей фразы"></audio>
        <div class="song-live-editor-head"><div><b>Правка во время прослушивания</b><p class="hint" id="song-live-edit-target"></p></div><div class="actions">
          <button type="button" class="btn song-icon-btn" id="song-live-edit-previous" title="Сохранить изменения и перейти к предыдущей фразе в редакторе" aria-label="Предыдущая фраза">${icon('previous')}</button>
          <button type="button" class="btn song-icon-btn" id="song-live-edit-play" title="Проиграть текущую фразу, не выходя из редактора" aria-label="Проиграть текущую фразу, не выходя из редактора">${icon('play')}</button>
          <button type="button" class="btn song-icon-btn" id="song-live-edit-next" title="Сохранить изменения и перейти к следующей фразе в редакторе" aria-label="Следующая фраза">${icon('next')}</button>
          <button type="button" class="btn primary small" id="song-live-edit-save" title="Сохранить текст и аккорды, оставаясь в редакторе фразы">Сохранить</button>
          <button type="button" class="btn song-icon-btn" id="song-live-edit-play-original" title="Проиграть оригинальную запись, не закрывая редактор" aria-label="Проиграть оригинальную запись" ${row.capabilities?.audio ? '' : 'disabled'}>${icon('play')}</button>
          <button type="button" class="btn small" id="song-live-edit-close" title="Закрыть редактор и продолжить песню с начала текущей фразы">Закрыть</button>
        </div></div>
        <div class="song-live-preview" id="song-live-preview"><b>Фраза после правки</b><div class="song-live-preview-chords" id="song-live-edit-preview-chords" aria-hidden="true"></div><p id="song-live-edit-preview" aria-live="polite"></p></div>
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
    bindChordPopovers();
    bindResponsive(row,render);
    function change(delta) {
      shifts.set(row.song_id,offset(row)+delta); render();
      // Keep the audible track in the same key as the displayed chords.
      // SongReview stores the value until playback starts, so changing the
      // key before pressing play is also supported.
      window.SongReview?.setPitch?.(row.song_id, offset(row));
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
      <label class="f">Смена<select id="song-chord-select"><option value="new">Новая смена</option>${(row.chords || []).map((c,i) => c.name === 'N' ? '' : `<option value="${i}" title="${esc(chordTooltip(c.name))}">${(c.start_ms/1000).toFixed(2)} с — ${esc(c.name)}</option>`).join('')}</select></label>
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
    chordApplyHandlers.set(row.song_id,async({trigger,original,symbol,name,description,shape})=>{
      const current=getCurrent();
      if(current?.song_id!==row.song_id)throw Error('Откройте исходную песню, чтобы применить аккорд.');
      const sourceName=trigger.dataset.sourceChordName||original, start=Number(trigger.dataset.startMs);
      const events=structuredClone(current.chords||[]);
      const matches=events.map((event,index)=>event.name===sourceName&&Number(event.start_ms)===start?index:-1).filter(index=>index>=0);
      if(matches.length!==1)throw Error('Смена аккорда уже изменилась. Правка оставлена в окне.');
      const shift=Number(trigger.dataset.chordKeyShift)||0,index=matches[0];
      const displayName=(name||symbol).trim();
      events[index]={...events[index],name:shift?transpose(displayName,-shift):displayName,
        description:description||'',voicing_fingering:shape,voicing_key_shift:shift,voicing_source:'manual',verified:true,method:'manual'};
      delete events[index].voicing_probability;
      const saved=await core.call('song_edit_chords',{song_id:row.song_id,base_revision:current.revision,chords:events});
      return ()=>accept(saved);
    });
    const select = document.getElementById('song-chord-select');
    if (!select) return;
    const name = document.getElementById('song-chord-name'), time = document.getElementById('song-chord-time');
    const status = document.getElementById('song-chord-status'), letters = document.getElementById('song-chord-letters');
    let anchor = null;
    const updateChordTitle=()=>{name.title=chordTooltip(name.value)||'Введите название аккорда';};
    name.addEventListener('input',updateChordTitle);
    updateChordTitle();
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
      updateChordTitle();
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
  return {html,bind,toolbar,bindToolbar,transpose,offset,plainText,sheetRows,sheetHTML,sourceLinks,chordFingering,chordFingerings,chordTooltip,inferChordName,fretNote,voicingAtShift,icon,compactChords,wrapLine,presentationLines,updatePlayback,countdownState,currentWord,lineStarts,lineTarget};
})();
