const puppeteer=require('puppeteer'); const fs=require('fs'); const path=require('path');
const BASE=process.argv[2]; const MIRROR=path.join(__dirname,'mirror');
const map=u=>{if(u.includes('react-dom.production'))return'react-dom.production.min.js';if(u.includes('react.production'))return'react.production.min.js';if(u.includes('echarts'))return'echarts.min.js';return null;};
(async()=>{const b=await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});const p=await b.newPage();
await p.setViewport({width:1440,height:900});
const errs=[];p.on('console',m=>{if(m.type()==='error'&&!/ERR_FAILED|404/.test(m.text()))errs.push(m.text().slice(0,160));});p.on('pageerror',e=>errs.push('PAGEERROR '+e.message.slice(0,160)));
const echartsReq=[];
await p.setRequestInterception(true);
p.on('request',r=>{const f=map(r.url());if(r.url().includes('echarts'))echartsReq.push(Date.now());if(f){r.respond({status:200,headers:{'access-control-allow-origin':'*'},contentType:'application/javascript',body:fs.readFileSync(path.join(MIRROR,f))});}else if(r.url().includes('fonts.goog')||r.url().includes('gstatic')){r.abort();}else r.continue();});
const t0=Date.now();
await p.goto(BASE+'/',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>{const e=document.querySelector('.pill-live');return e&&/live data/i.test(e.textContent);},{timeout:30000});
// (1) immediately after content visible: is echarts loaded yet?
const echartsAtPaint=await p.evaluate(()=>typeof window.echarts);
// (2) wait for idle-prefetch to bring it in (no navigation)
let prefetched='no';
try{await p.waitForFunction(()=>typeof window.echarts!=='undefined',{timeout:8000});prefetched='yes (idle prefetch)';}catch{}
// (3) open a chart and confirm it renders
await p.evaluate(()=>{const b=[...document.querySelectorAll('.sidenav-item')].find(x=>x.textContent.trim().toLowerCase().startsWith('timeline'));b&&b.click();});
let chartOk=false;try{await p.waitForFunction(()=>{let n=0;document.querySelectorAll('div').forEach(d=>{try{if(window.echarts&&window.echarts.getInstanceByDom(d))n++;}catch(e){}});return n>0;},{timeout:8000});chartOk=true;}catch{}
console.log(JSON.stringify({echartsAtFirstContent:echartsAtPaint,echartsLoaded:prefetched,echartsRequestCount:echartsReq.length,echartsLoadedAtMs:echartsReq[0]?echartsReq[0]-t0:null,chartRendered:chartOk,errors:errs.length?errs:'(none)'},null,2));
await b.close();})().catch(e=>console.error('ERR',e.message));
