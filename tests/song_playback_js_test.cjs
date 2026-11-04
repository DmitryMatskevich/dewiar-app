// Transport behavior, cancellation, pause/resume and sharing a single audio element.
const assert=require('node:assert/strict');
global.window={addEventListener(){}};global.escH=s=>s;
require('../ui/studio/song_sheet.js');global.SongSheet=window.SongSheet;
require('../ui/studio/song_review.js');const review=window.SongReview;
let elements, player, requests, pending, follows=[];
SongSheet.updatePlayback=(id,ms,scroll)=>follows.push({id,ms,scroll});
function setup() {
 elements=new Map();requests=0;pending=null;follows=[];
 global.document={activeElement:null,getElementById:id=>elements.get(id),querySelectorAll:()=>[],querySelector:()=>player};
 for(const id of ['song-sheet-play','song-sheet-stop','song-play-full','song-play-status','song-sheet-play-status','song-review-loop']) elements.set(id,{setAttribute(n,v){this[n]=v}});
 player={dataset:{},src:'',paused:true,ended:false,currentTime:0,isConnected:true,listeners:{},
 addEventListener(n,fn){this.listeners[n]=fn},
 pause(){this.paused=true;this.onpause?.()},
 async play(){this.paused=false;this.ended=false;this.onplay?.()},
 matches(){return !!(this.dataset.session || this.dataset.loading || !this.dataset.idle)}};
 elements.set('song-review-audio',player);
 global.core={call:async()=>{requests++;return pending ? await pending : {data:'data:audio/mpeg;base64,test'}}};
 review.bind({song_id:'song',capabilities:{audio:true}},()=>({phrases:[]}),()=>{});
}
const button=id=>elements.get(id);
(async()=>{
 setup();const play=button('song-sheet-play'),stop=button('song-sheet-stop');
 assert.equal(stop.disabled,true);
 await play.onclick();assert.equal(player.paused,false);assert.match(play.title,/Приостановить/);assert.equal(requests,1);
 player.currentTime=12;player.listeners.timeupdate();assert.equal(follows.at(-1).ms,12000);await play.onclick();assert.equal(follows.at(-1).scroll,false);assert.equal(player.paused,true);assert.equal(player.currentTime,12);assert.equal(review.protectedView('song'),true);
 await play.onclick();assert.equal(player.paused,false);assert.equal(player.currentTime,12);assert.equal(requests,1);
 stop.onclick();const held=follows.at(-1).ms;player.listeners.timeupdate();assert.equal(follows.at(-1).ms,held);assert.equal(player.paused,true);assert.equal(player.currentTime,0);assert.equal(review.protectedView('song'),false);
 await play.onclick();assert.equal(requests,1);player.ended=true;player.paused=true;player.listeners.ended();assert.equal(review.protectedView('song'),false);assert.match(play.title,/Проиграть/);
 // A phrase and full song share one element; the sheet play action restores full audio.
 player.src='phrase';player.dataset.phraseId='p';player.currentTime=4;
 await play.onclick();assert.match(player.src,/data:audio/);assert.equal(player.dataset.phraseId,undefined);
 review.stop();assert.equal(player.currentTime,0);
 // Stop while preparing: a late bridge response must never start audio.
 setup();let resolve;pending=new Promise(r=>{resolve=r});
 const loading=button('song-sheet-play').onclick();assert.equal(button('song-sheet-play').disabled,true);assert.equal(button('song-sheet-stop').disabled,false);assert.equal(review.protectedView('song'),true);
 button('song-sheet-stop').onclick();resolve({data:'late'});await loading;assert.equal(player.src,'');assert.equal(player.paused,true);assert.equal(button('song-sheet-play').disabled,false);
 // Failure stays visible and the play button becomes available for retry.
 setup();global.core.call=async()=>{throw Error('Audio unavailable')};await button('song-sheet-play').onclick();assert.equal(button('song-sheet-play').disabled,false);assert.match(button('song-sheet-play-status').textContent,/Audio unavailable/);
 console.log('Full song, pause/resume, stop/reset, shared player, cancellation and failure: OK');
})().catch(e=>{console.error(e);process.exitCode=1});
