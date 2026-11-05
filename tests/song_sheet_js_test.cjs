const assert=require('node:assert/strict');global.window={};global.escH=s=>s;require('../ui/studio/song_sheet.js');const s=window.SongSheet;
assert.equal(s.transpose('B7/D#',1),'C7/E');assert.equal(s.transpose('Cm7b5',-1),'Bm7b5');assert.equal(s.transpose('Bb',-12),'Bb');assert.equal(s.transpose(s.transpose('F#m',1),-1),'Gbm');
const row={title:'Test',guitar_sheet:[{text:'One two',cells:[{text:'One ',char_index:0,chords:['Am']},{text:'two',char_index:4,chords:['E7']}]}]};assert.ok(s.plainText(row).includes('Am  E7\nOne two'));console.log('Transposition, slash bass and aligned copy: OK');
assert.equal(s.chordFingering('Em'),'0-0-0-2-2-0');
assert.equal(s.chordFingering('Am'),'0-1-2-2-0-x');
assert.equal(s.chordFingering('C/G'),'0-1-0-2-3-3');
// The preview and clipboard must use the same current semitone offset.
const fixture={song_id:'copy-check',title:'Must not copy',artist:'Artist',phrases:[{phrase_id:'p'}],sections:[{label:'Verse',phrase_indexes:[0]}],guitar_sheet:[{phrase_id:'p',text:'One two',cells:[{char_index:0,chords:['B7/D#']},{char_index:9,chords:['F#m']}]},{text:'',unplaced_chords:[{name:'Bb',start_ms:1234},{name:'N',start_ms:1500}]}]};
const elements=new Map();global.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',setAttribute(name,value){this[name]=value}});return elements.get(id)}};
let copied;Object.defineProperty(global,'navigator',{value:{clipboard:{writeText:async text=>{copied=text}}},configurable:true});
s.bindToolbar(fixture,()=>fixture,()=>{});
document.getElementById('song-key-up').onclick();
assert.ok(s.plainText(fixture).startsWith('C7/E     Gm\nOne two\n\nB\nОкончание'));
assert.match(s.plainText(fixture),/Аппликатуры \(струны 1→6/);
assert.equal(document.getElementById('song-copy-preview').value,s.plainText(fixture));
(async()=>{await document.getElementById('song-copy-sheet').onclick();assert.equal(copied,s.plainText(fixture));document.getElementById('song-key-down').onclick();assert.ok(s.plainText(fixture).startsWith('B7/D#    F#m\nOne two\n\nBb\nОкончание'));assert.ok(!copied.includes('Must not copy'));assert.ok(!copied.includes('Verse'));assert.ok(!copied.includes('1234'));console.log('Clean preview, selected key, clipboard equality and reset: OK');})().catch(e=>{console.error(e);process.exitCode=1});

assert.ok(s.plainText({guitar_sheet:[{text:'One two',cells:[{char_index:4,chords:['Am']}]}]}).startsWith('    Am\nOne two'));
const refs=s.sourceLinks({source_ref:'https://audio.example/song',source_candidates:[{url:'javascript:bad',title:'аккорды'},{url:'https://chords.example/a#one',title:'Аккорды',source_check:{status:'available'}},{url:'https://chords.example/a#two',title:'Аккорды копия',source_check:{status:'available'}},{url:'https://lyrics.example/a',kind:'lyrics',title:'Текст',source_check:{status:'available'}},{url:'https://video.example/a',title:'Запись',source_check:{status:'available'}},{url:'https://extra.example/a',source_check:{status:'available'}}]});
assert.equal(refs.length,3);assert.equal(refs[0].url,'https://chords.example/a');assert.equal(new Set(refs.map(x=>x.url)).size,3);
const copiedWithSources=s.plainText({source_candidates:[{url:'https://chords.example/a#fragment',title:'Аккорды',source_check:{status:'available'}},{url:'https://lyrics.example/a',kind:'lyrics',title:'Текст',source_check:{status:'available'}},{url:'https://video.example/a',title:'Запись',source_check:{status:'available'}}],guitar_sheet:[{text:'Строка',cells:[{char_index:0,chords:['Em']},{char_index:5,chords:['Am']}]}]});
assert.ok(copiedWithSources.endsWith('Источники:\nАккорды — https://chords.example/a\nТекст — https://lyrics.example/a\nЗапись — https://video.example/a'));
assert.ok(copiedWithSources.includes('Em — 0-0-0-2-2-0'));assert.ok(copiedWithSources.includes('Am — 0-1-2-2-0-x'));
const sheetExample={title:'Title',artist:'Artist',source_candidates:[{url:'https://chords.example/a',title:'Chords',source_check:{status:'available'}}],guitar_sheet:Array.from({length:5},(_,i)=>({text:'Line '+i,cells:[{char_index:0,chords:['Am']}]}))};
assert.equal(s.sheetRows(sheetExample).filter(r=>r.kind==='space').length,1);
assert.ok(s.plainText(sheetExample).startsWith(s.sheetRows(sheetExample).map(r=>r.text).join('\n')));
assert.ok(s.sheetHTML(sheetExample).includes('song-paper-sources'));assert.ok(s.plainText(sheetExample).includes('Источники:\nChords — https://chords.example/a'));assert.ok(!s.plainText({guitar_sheet:[{text:'No source',cells:[]}]}).includes('https://'));console.log('Paper layout, chord fingerings and copied source links: OK');

const toolbar=s.toolbar({...fixture,capabilities:{audio:true}});
for(const id of ['song-key-down','song-key-up','song-show-text','song-copy-sheet','song-sheet-play','song-sheet-stop','song-sheet-previous','song-sheet-next']) {
 const button=toolbar.match(new RegExp('<button[^>]*id="'+id+'"[^>]*>[\\s\\S]*?</button>'))?.[0];
 assert.ok(button && button.includes('title="') && button.includes('aria-label="') && button.includes('<svg'),id);
}
assert.ok(!toolbar.includes('song-key-reset'));assert.ok(!toolbar.includes('½ тона'));assert.ok(!toolbar.includes('Сдвиг:'));
assert.ok(!toolbar.includes('song-edit-current'));
console.log('Compact named icon controls: OK');

const editable=s.sheetHTML({song_id:'edit-check',title:'Edit',phrases:[],guitar_sheet:[{phrase_id:'phrase-1:line:12',source_phrase_ids:['phrase-1'],text:'A lyric phrase.',chords:[{name:'Am',char_index:0,start_ms:0,end_ms:1000}]}]});
assert.match(editable,/data-song-edit-phrase="phrase-1" data-song-edit-kind="chords" title="Редактировать аккорды этой фразы"/);
assert.match(editable,/data-song-edit-phrase="phrase-1" data-song-edit-line="phrase-1:line:12" data-song-edit-kind="lyrics" title="Редактировать текст этой фразы"/);
assert.ok(editable.indexOf('song-paper-edit-chords')<editable.indexOf('song-paper-chords'));
assert.ok(editable.indexOf('song-paper-edit-lyrics')<editable.indexOf('song-paper-lyrics'));
console.log('Inline phrase and chord edit controls: OK');

// Dense candidates become one row by sounding duration; source/editor is intact.
const dense={text:'First words, then more words.',chords:[
 {name:'F6',char_index:0,start_ms:0,end_ms:100},
 {name:'Fdim',char_index:0,start_ms:100,end_ms:220},
 {name:'F',char_index:0,start_ms:220,end_ms:2200},
 {name:'Dm',char_index:18,start_ms:2200,end_ms:3600}]};
const denseBefore=JSON.stringify(dense);
assert.deepEqual(s.compactChords(dense,0).map(c=>c.name),['F','Dm']);
assert.equal(JSON.stringify(dense),denseBefore);
assert.equal(s.sheetRows({guitar_sheet:[dense]}).filter(r=>r.kind==='chords').length,1);
for(let shift=-12;shift<=12;shift++) {
 const cs=s.compactChords(dense,shift,24);
 assert.ok(cs.every((c,i)=>i===0 || cs[i-1].at+cs[i-1].name.length<c.at));
 assert.ok(cs.every(c=>c.at+c.name.length<=24));
}
const narrow={text:'One little word and another final word',chords:[{name:'Am',char_index:0},{name:'E7',char_index:28}],display_words:[{word_id:'last',char_start:34,char_end:38,start_ms:5000,end_ms:6000}]};
const wrapped=s.wrapLine(narrow,20);
assert.equal(wrapped.map(l=>l.text).join(' '),narrow.text);
assert.ok(wrapped.every(l=>l.text.length<=20));
assert.equal(wrapped.flatMap(l=>l.chords).length,2);
const last=wrapped.find(l=>l.display_words.length);
assert.equal(last.text.slice(last.display_words[0].char_start,last.display_words[0].char_end),'word');
console.log('Single chord row, duration dominance, transpose fit and responsive cues: OK');

// Real regression shape: a change in a short vocal gap was a separate API row,
// then the next lyric had its own changes. Per-row compaction left a staircase.
const boundary={guitar_sheet:[
 {text:'Previous phrase.',start_ms:1000,end_ms:2000,chords:[{name:'C#',char_index:0,start_ms:1000,end_ms:2000}]},
 {text:'',unplaced_chords:[{name:'Abmaj7',start_ms:2000,end_ms:2427}]},
 {text:'',unplaced_chords:[{name:'F6',start_ms:2427,end_ms:3000}]},
 {text:'Next phrase with words.',start_ms:2600,end_ms:7000,chords:[{name:'F#m',char_index:0,start_ms:2600,end_ms:6000},{name:'Bm',char_index:17,start_ms:6000,end_ms:7000}]},
 {text:'',unplaced_chords:[{name:'E',start_ms:7000,end_ms:8000}]},
 {text:'',unplaced_chords:[{name:'Am',start_ms:8000,end_ms:9000}]}
]};
const boundaryBefore=JSON.stringify(boundary);
const visible=s.sheetRows(boundary).filter(r=>r.kind!=='space');
assert.ok(visible.every((r,i)=>r.kind!=='chords' || visible[i-1]?.kind!=='chords'));
assert.equal(visible.filter(r=>r.kind==='lyrics').map(r=>r.text).join(' '),'Previous phrase. Next phrase with words.');
assert.equal(visible.filter(r=>r.kind==='chords').length,4);
assert.equal(visible.filter(r=>r.kind==='instrumental').length,2);
assert.match(visible.filter(r=>r.kind==='chords')[1].text,/^Abmaj7\s+F6$/);
assert.equal(visible.filter(r=>r.kind==='instrumental')[0].start_ms,2000);
assert.equal(visible.filter(r=>r.kind==='instrumental')[0].end_ms,2600);
assert.equal(visible.at(-1).kind,'instrumental');assert.equal(visible.at(-1).text,'Окончание');
assert.equal(s.presentationLines(boundary).length,4);
assert.equal(JSON.stringify(boundary),boundaryBefore);
// Each instrumental passage has one chord row followed immediately by its label.
const gaps={guitar_sheet:boundary.guitar_sheet.filter(l=>!l.text)};
assert.equal(s.sheetRows(gaps).filter(r=>r.kind==='chords').length,1);
assert.equal(s.sheetRows({guitar_sheet:[...gaps.guitar_sheet,boundary.guitar_sheet[3]]}).filter(r=>r.kind==='chords').length,2);
console.log('Gap/lyric boundaries, consecutive instrumental blocks and source preservation: OK');
const pairedHTML=s.sheetHTML(boundary);
assert.equal(visible.filter(r=>r.kind==='instrumental').length,2);
assert.ok(visible.every((r,i)=>r.kind!=='instrumental' || visible[i-1]?.kind==='chords'));
assert.match(pairedHTML,/song-paper-chords">Abmaj7\s+F6<\/div><div class="song-paper-instrumental"[^>]*data-start-ms="2000"[^>]*data-end-ms="2600"/);
assert.match(pairedHTML,/song-paper-cue-part" data-cue-step="3">Про<\/span><span class="song-paper-cue-part" data-cue-step="2">и<\/span><span class="song-paper-cue-part" data-cue-step="1">грыш<\/span>/);
assert.equal(s.sheetRows({guitar_sheet:[{text:'',unplaced_chords:[{name:'N',start_ms:0}]}]}).length,0);
assert.match(pairedHTML, /<div class="song-paper-chords">F#m\s+Bm<\/div>[\s\S]*?<div class="song-paper-lyrics">Next phrase with words\.<\/div>/);
console.log('Chord and lyric share one inseparable presentation block: OK');

const intro=s.sheetHTML({song_id:'intro',title:'Intro',phrases:[],guitar_sheet:[{text:'',unplaced_chords:[{name:'Em',start_ms:0,end_ms:3000}]},{phrase_id:'next',text:'First lyric.',start_ms:3000,end_ms:5000,chords:[{name:'Am',char_index:0,start_ms:3000,end_ms:5000}]}]});
assert.match(intro,/data-cue-step="3">Всту<\/span><span class="song-paper-cue-part" data-cue-step="2">пле<\/span><span class="song-paper-cue-part" data-cue-step="1">ние<\/span>/);
assert.ok(!intro.includes('До вокала'));
console.log('Intro countdown highlights title parts in 3-2-1 order without a vocal-wait message: OK');

assert.equal(s.lineTarget([1000,5000,11000],0,1),1000);
assert.equal(s.lineTarget([1000,5000,11000],3000,-1),null);
assert.equal(s.lineTarget([1000,5000,11000],3000,1),5000);
assert.equal(s.lineTarget([1000,5000,11000],8500,-1),1000);
assert.equal(s.lineTarget([1000,5000,11000],8500,1),11000);
assert.equal(s.lineTarget([1000,5000,11000],4999.999,1),11000);
assert.equal(s.lineTarget([1000,5000,11000],11000,1),null);
assert.equal(s.lineTarget([],0,1),null);
console.log('Previous/next displayed lyric lines and first/last boundaries: OK');
assert.equal(s.countdownState(0,0,10000),null);
assert.deepEqual(s.countdownState(7000,0,10000),{count:3,lit:1});
assert.deepEqual(s.countdownState(8000,0,10000),{count:2,lit:2});
assert.deepEqual(s.countdownState(9000,0,10000),{count:1,lit:3});
assert.equal(s.countdownState(10000,0,10000),null);
assert.deepEqual(s.countdownState(0,0,600),{count:3,lit:1});
assert.deepEqual(s.countdownState(200,0,600),{count:2,lit:2});
assert.deepEqual(s.countdownState(400,0,600),{count:1,lit:3});
console.log('Instrumental cue, active three-letter countdown and transition timing: OK');
