const assert=require('node:assert/strict');global.window={};global.escH=s=>s;require('../ui/studio/song_sheet.js');const s=window.SongSheet;
assert.equal(s.transpose('B7/D#',1),'C7/E');assert.equal(s.transpose('Cm7b5',-1),'Bm7b5');assert.equal(s.transpose('Bb',-12),'Bb');assert.equal(s.transpose(s.transpose('F#m',1),-1),'Gbm');
const row={title:'Test',guitar_sheet:[{text:'One two',cells:[{text:'One ',char_index:0,chords:['Am']},{text:'two',char_index:4,chords:['E7']}]}]};assert.ok(s.plainText(row).includes('Am  E7\nOne two'));console.log('Transposition, slash bass and aligned copy: OK');
// The preview and clipboard must use the same current semitone offset.
const fixture={song_id:'copy-check',title:'Must not copy',artist:'Artist',phrases:[{phrase_id:'p'}],sections:[{label:'Verse',phrase_indexes:[0]}],guitar_sheet:[{phrase_id:'p',text:'One two',cells:[{char_index:0,chords:['B7/D#']},{char_index:9,chords:['F#m']}]},{text:'',unplaced_chords:[{name:'Bb',start_ms:1234},{name:'N',start_ms:1500}]}]};
const elements=new Map();global.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',setAttribute(name,value){this[name]=value}});return elements.get(id)}};
let copied;Object.defineProperty(global,'navigator',{value:{clipboard:{writeText:async text=>{copied=text}}},configurable:true});
s.bindToolbar(fixture,()=>fixture,()=>{});
document.getElementById('song-key-up').onclick();
assert.equal(s.plainText(fixture),'C7/E     Gm\nOne two\n\nB\nПроигрыш');
assert.equal(document.getElementById('song-copy-preview').value,s.plainText(fixture));
(async()=>{await document.getElementById('song-copy-sheet').onclick();assert.equal(copied,s.plainText(fixture));document.getElementById('song-key-down').onclick();assert.equal(s.plainText(fixture),'B7/D#    F#m\nOne two\n\nBb\nПроигрыш');assert.ok(!copied.includes('Must not copy'));assert.ok(!copied.includes('Verse'));assert.ok(!copied.includes('1234'));console.log('Clean preview, selected key, clipboard equality and reset: OK');})().catch(e=>{console.error(e);process.exitCode=1});

assert.equal(s.plainText({guitar_sheet:[{text:'One two',cells:[{char_index:4,chords:['Am']}]}]}),'    Am\nOne two');
const refs=s.sourceLinks({source_ref:'https://audio.example/song',source_candidates:[{url:'javascript:bad',title:'аккорды'},{url:'https://chords.example/a#one',title:'Аккорды'},{url:'https://chords.example/a#two',title:'Аккорды копия'},{url:'https://lyrics.example/a',kind:'lyrics',title:'Текст'},{url:'https://video.example/a',title:'Запись'},{url:'https://extra.example/a'}]});
assert.equal(refs.length,3);assert.equal(refs[0].url,'https://chords.example/a');assert.equal(new Set(refs.map(x=>x.url)).size,3);
const sheetExample={title:'Title',artist:'Artist',source_candidates:[{url:'https://chords.example/a',title:'Chords'}],guitar_sheet:Array.from({length:5},(_,i)=>({text:'Line '+i,cells:[{char_index:0,chords:['Am']}]}))};
assert.equal(s.sheetRows(sheetExample).filter(r=>r.kind==='space').length,1);
assert.equal(s.sheetRows(sheetExample).map(r=>r.text).join('\n'),s.plainText(sheetExample));
assert.ok(s.sheetHTML(sheetExample).includes('song-paper-sources'));assert.ok(!s.plainText(sheetExample).includes('https://'));console.log('Paper layout and three deduplicated source links: OK');

const toolbar=s.toolbar({...fixture,capabilities:{audio:true}});
for(const id of ['song-key-down','song-key-up','song-show-text','song-copy-sheet','song-sheet-play','song-sheet-stop','song-sheet-previous','song-sheet-next']) {
 const button=toolbar.match(new RegExp('<button[^>]*id="'+id+'"[^>]*>[\\s\\S]*?</button>'))?.[0];
 assert.ok(button && button.includes('title="') && button.includes('aria-label="') && button.includes('<svg'),id);
}
assert.ok(!toolbar.includes('song-key-reset'));assert.ok(!toolbar.includes('½ тона'));assert.ok(!toolbar.includes('Сдвиг:'));
console.log('Compact named icon controls: OK');

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
 {text:'Previous phrase.',chords:[{name:'C#',char_index:0,start_ms:1000,end_ms:2000}]},
 {text:'',unplaced_chords:[{name:'Abmaj7',start_ms:2000,end_ms:2427}]},
 {text:'',unplaced_chords:[{name:'F6',start_ms:2427,end_ms:2600}]},
 {text:'Next phrase with words.',chords:[{name:'F#m',char_index:0,start_ms:2600,end_ms:6000},{name:'Bm',char_index:17,start_ms:6000,end_ms:7000}]},
 {text:'',unplaced_chords:[{name:'E',start_ms:7000,end_ms:8000}]},
 {text:'',unplaced_chords:[{name:'Am',start_ms:8000,end_ms:9000}]}
]};
const boundaryBefore=JSON.stringify(boundary);
const visible=s.sheetRows(boundary).filter(r=>r.kind!=='space');
assert.ok(visible.every((r,i)=>r.kind!=='chords' || visible[i-1]?.kind!=='chords'));
assert.equal(visible.filter(r=>r.kind==='lyrics').map(r=>r.text).join(' '),'Previous phrase. Next phrase with words.');
assert.equal(visible.filter(r=>r.kind==='chords')[3].text,'F#m              Bm');
assert.equal(visible.at(-1).kind,'instrumental');
assert.equal(s.presentationLines(boundary).length,6);
assert.equal(JSON.stringify(boundary),boundaryBefore);
// Each instrumental passage has one chord row followed immediately by its label.
const gaps={guitar_sheet:boundary.guitar_sheet.filter(l=>!l.text)};
assert.equal(s.sheetRows(gaps).filter(r=>r.kind==='chords').length,4);
assert.equal(s.sheetRows({guitar_sheet:[...gaps.guitar_sheet,boundary.guitar_sheet[3]]}).filter(r=>r.kind==='chords').length,5);
console.log('Gap/lyric boundaries, consecutive instrumental blocks and source preservation: OK');
const pairedHTML=s.sheetHTML(boundary);
assert.equal(visible.filter(r=>r.kind==='instrumental').length,4);
assert.ok(visible.every((r,i)=>r.kind!=='instrumental' || visible[i-1]?.kind==='chords'));
assert.match(pairedHTML,/song-paper-chords">Abmaj7<\/div><div class="song-paper-instrumental">Проигрыш/);
assert.equal(s.sheetRows({guitar_sheet:[{text:'',unplaced_chords:[{name:'N',start_ms:0}]}]}).length,0);
assert.match(pairedHTML, /class="song-paper-line"><div class="song-paper-chords">F#m\s+Bm<\/div><div class="song-paper-lyrics">Next phrase with words\.<\/div><\/div>/);
console.log('Chord and lyric share one inseparable presentation block: OK');

assert.equal(s.lineTarget([1000,5000,11000],0,1),1000);
assert.equal(s.lineTarget([1000,5000,11000],3000,-1),null);
assert.equal(s.lineTarget([1000,5000,11000],3000,1),5000);
assert.equal(s.lineTarget([1000,5000,11000],8500,-1),1000);
assert.equal(s.lineTarget([1000,5000,11000],8500,1),11000);
assert.equal(s.lineTarget([1000,5000,11000],4999.999,1),11000);
assert.equal(s.lineTarget([1000,5000,11000],11000,1),null);
assert.equal(s.lineTarget([],0,1),null);
console.log('Previous/next displayed lyric lines and first/last boundaries: OK');
