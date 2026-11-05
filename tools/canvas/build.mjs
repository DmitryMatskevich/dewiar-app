import {build} from 'esbuild';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const out=path.resolve('../../ui/assets/vendor/canvas');
await fs.rm(out,{recursive:true,force:true});await fs.mkdir(out,{recursive:true});
const offlineFonts={name:'offline-fonts',setup(builder){builder.onLoad({filter:/chunk-K2UTITRG\.js$/},async args=>{
 const source=await fs.readFile(args.path,'utf8'),needle='return r.push(new URL(n,jn.ASSETS_FALLBACK_URL)),r';
 if(source.split(needle).length!==2)throw Error('Pinned Excalidraw font fallback changed; inspect before rebuilding');
 return {contents:source.replace(needle,'return r'),loader:'js'};
});}};
const result=await build({entryPoints:['editor.jsx'],outdir:out,bundle:true,splitting:true,format:'esm',platform:'browser',target:['safari15'],minify:true,metafile:true,plugins:[offlineFonts],conditions:['production'],define:{'process.env.NODE_ENV':'"production"'},loader:{'.woff2':'file','.woff':'file','.ttf':'file'},assetNames:'assets/[name]-[hash]',chunkNames:'chunks/[name]-[hash]',logLevel:'warning'});
await fs.cp('node_modules/@excalidraw/excalidraw/dist/prod/fonts',out+'/fonts',{recursive:true});
await fs.cp('licenses',out+'/licenses',{recursive:true});
// Preserve notices from every bundled package, not only the top-level editor.
const notices=[];const roots=new Set();
for(const input of Object.keys(result.metafile.inputs)){
 const segments=input.split('/');let last=segments.lastIndexOf('node_modules');if(last<0)continue;
 const end=last+(segments[last+1]?.startsWith('@')?3:2);roots.add(segments.slice(0,end).join('/'));
}
for(const root of [...roots].sort()){
 let pkg;try{pkg=JSON.parse(await fs.readFile(root+'/package.json','utf8'))}catch{continue}
 notices.push(`\n--- ${pkg.name} ${pkg.version} (${pkg.license||'see notice'}) ---\n`);
 for(const file of await fs.readdir(root))if(/^(license|copying|notice)/i.test(file)){try{notices.push(await fs.readFile(root+'/'+file,'utf8'))}catch{}}
}
await fs.writeFile(out+'/THIRD-PARTY-NOTICES.txt',notices.join('\n'));
const hashes={};async function walk(dir){for(const name of await fs.readdir(dir)){const file=dir+'/'+name;if((await fs.stat(file)).isDirectory())await walk(file);else hashes[path.relative(out,file)]=createHash('sha256').update(await fs.readFile(file)).digest('hex')}}
await walk(out);await fs.writeFile(out+'/manifest.json',JSON.stringify({excalidraw:'0.18.1',converter:'2.2.2',hashes},null,2));
console.log('Local canvas bundle:',Object.keys(hashes).length,'files');
