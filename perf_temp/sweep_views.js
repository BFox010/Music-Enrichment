const puppeteer=require('puppeteer'); const fs=require('fs'); const path=require('path');
const BASE=process.argv[2]; const MIRROR=path.join(__dirname,'mirror');
const map=u=>{if(u.includes('react-dom.production'))return'react-dom.production.min.js';if(u.includes('react.production'))return'react.production.min.js';if(u.includes('echarts'))return'echarts.min.js';return null;};
(async()=>{const b=await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});const p=await b.newPage();
await p.setViewport({width:1440,height:900});
const errs=[];p.on('console',m=>{if(m.type()==='error'&&!/ERR_FAILED|404/.test(m.text()))errs.push(m.text().slice(0,160));});p.on('pageerror',e=>errs.push('PAGEERROR '+e.message.slice(0,160)));
await p.setRequestInterception(true);
p.on('request',r=>{const f=map(r.url());if(f){r.respond({status:200,headers:{'access-control-allow-origin':'*'},contentType:'application/javascript',body:fs.readFileSync(path.join(MIRROR,f))});}else if(r.url().includes('fonts.goog')||r.url().includes('gstatic')){r.abort();}else r.continue();});
await p.goto(BASE+'/',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>{const e=document.querySelector('.pill-live');return e&&/live data/i.test(e.textContent);},{timeout:30000});
await p.waitForFunction(()=>typeof window.echarts!=='undefined',{timeout:10000}).catch(()=>{});
const views=['overview','genres','albums','constellation','audio','coverage','timeline','listening','artists','seasonal','forgotten','tracks','scrobble'];
const results=[];
for(const v of views){
  await p.evaluate((lbl)=>{const b=[...document.querySelectorAll('.sidenav-item')].find(x=>x.textContent.trim().toLowerCase().startsWith(lbl));b&&b.click();},v);
  await new Promise(r=>setTimeout(r,500));
  const info=await p.evaluate(()=>{const main=document.querySelector('.main,main,#root');return{active:document.querySelector('.sidenav-item.active')?.textContent.trim(),contentChars:(document.querySelector('section,.block,.card')?document.body.innerText.length:0)};});
  results.push(v+': '+(info.active||'?')+' ('+info.contentChars+' chars)');
}
console.log(results.join('\n'));
console.log('\nERRORS:',errs.length?errs.join('\n'):'(NONE — all views clean)');
await b.close();})().catch(e=>console.error('ERR',e.message));
