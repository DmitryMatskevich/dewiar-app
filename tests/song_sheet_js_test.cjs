const assert=require('node:assert/strict');global.window={};global.escH=s=>s;require('../ui/studio/song_sheet.js');const s=window.SongSheet;
assert.equal(s.transpose('B7/D#',1),'C7/E');assert.equal(s.transpose('Cm7b5',-1),'Bm7b5');assert.equal(s.transpose('Bb',-12),'Bb');assert.equal(s.transpose(s.transpose('F#m',1),-1),'Gbm');
const row={title:'Test',guitar_sheet:[{text:'One two',cells:[{text:'One ',char_index:0,chords:['Am']},{text:'two',char_index:4,chords:['E7']}]}]};assert.ok(s.plainText(row).includes('Am  E7\nOne two'));console.log('Transposition, slash bass and aligned copy: OK');
// The preview and clipboard must use the same current semitone offset.
const fixture={song_id:'copy-check',title:'Must not copy',artist:'Artist',phrases:[{phrase_id:'p'}],sections:[{label:'Verse',phrase_indexes:[0]}],guitar_sheet:[{phrase_id:'p',text:'One two',cells:[{char_index:0,chords:['B7/D#']},{char_index:9,chords:['F#m']}],unplaced_chords:[{name:'Bb',start_ms:1234},{name:'N',start_ms:1500}]}]};
const elements=new Map();global.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',setAttribute(name,value){this[name]=value}});return elements.get(id)}};
let copied;Object.defineProperty(global,'navigator',{value:{clipboard:{writeText:async text=>{copied=text}}},configurable:true});
s.bindToolbar(fixture,()=>fixture,()=>{});
document.getElementById('song-key-up').onclick();
assert.equal(s.plainText(fixture),'C7/E     Gm\nOne two\nB');
assert.equal(document.getElementById('song-copy-preview').value,s.plainText(fixture));
(async()=>{await document.getElementById('song-copy-sheet').onclick();assert.equal(copied,s.plainText(fixture));document.getElementById('song-key-down').onclick();assert.equal(s.plainText(fixture),'B7/D#    F#m\nOne two\nBb');assert.ok(!copied.includes('Must not copy'));assert.ok(!copied.includes('Verse'));assert.ok(!copied.includes('1234'));console.log('Clean preview, selected key, clipboard equality and reset: OK');})().catch(e=>{console.error(e);process.exitCode=1});

assert.equal(s.plainText({guitar_sheet:[{text:'One two',cells:[{char_index:4,chords:['Am']}]}]}),'    Am\nOne two');
const refs=s.sourceLinks({source_ref:'https://audio.example/song',source_candidates:[{url:'javascript:bad',title:'аккорды'},{url:'https://chords.example/a#one',title:'Аккорды'},{url:'https://chords.example/a#two',title:'Аккорды копия'},{url:'https://lyrics.example/a',kind:'lyrics',title:'Текст'},{url:'https://video.example/a',title:'Запись'},{url:'https://extra.example/a'}]});
assert.equal(refs.length,3);assert.equal(refs[0].url,'https://chords.example/a');assert.equal(new Set(refs.map(x=>x.url)).size,3);
const sheetExample={title:'Title',artist:'Artist',source_candidates:[{url:'https://chords.example/a',title:'Chords'}],guitar_sheet:Array.from({length:5},(_,i)=>({text:'Line '+i,cells:[{char_index:0,chords:['Am']}]}))};
assert.equal(s.sheetRows(sheetExample).filter(r=>r.kind==='space').length,1);
assert.equal(s.sheetRows(sheetExample).map(r=>r.text).join('\n'),s.plainText(sheetExample));
assert.ok(s.sheetHTML(sheetExample).includes('song-paper-sources'));assert.ok(!s.plainText(sheetExample).includes('https://'));console.log('Paper layout and three deduplicated source links: OK');

const toolbar=s.toolbar({...fixture,capabilities:{audio:true}});
for(const id of ['song-key-down','song-key-up','song-key-reset','song-show-text','song-copy-sheet','song-sheet-play','song-sheet-stop']) {
 const button=toolbar.match(new RegExp('<button[^>]*id="'+id+'"[^>]*>[\\s\\S]*?</button>'))?.[0];
 assert.ok(button && button.includes('title="') && button.includes('aria-label="') && button.includes('<svg'),id);
}
assert.ok(!toolbar.includes('½ тона'));assert.ok(!toolbar.includes('Сдвиг:'));
console.log('Compact named icon controls: OK');
