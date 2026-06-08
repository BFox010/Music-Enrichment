const puppeteer=require('puppeteer'); const fs=require('fs'); const path=require('path');
const BASE=process.argv[2]; const MIRROR=path.join(__dirname,'mirror');
const map=u=>{if(u.includes('react-dom.production'))return'react-dom.production.min.js';if(u.includes('react.production'))return'react.production.min.js';if(u.includes('echarts'))return'echarts.min.js';return null;};
(async()=>{const b=await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});const p=await b.newPage();
await p.setRequestInterception(true);
p.on('request',r=>{const f=map(r.url());if(f){r.respond({status:200,headers:{'access-control-allow-origin':'*'},contentType:'application/javascript',body:fs.readFileSync(path.join(MIRROR,f))});}else if(r.url().includes('fonts.goog')||r.url().includes('gstatic')){r.abort();}else r.continue();});
await p.goto(BASE+'/',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>{const e=document.querySelector('.pill-live');return e&&/live data/i.test(e.textContent);},{timeout:30000});
await p.waitForFunction(()=>typeof window.echarts!=='undefined',{timeout:10000}).catch(()=>console.log('echarts never loaded'));
// click timeline
const c0=Date.now();
await p.evaluate(()=>{const b=[...document.querySelectorAll('.sidenav-item')].find(x=>/timeline/i.test(x.textContent));b&&b.click();});
let rendered=null;
try{await p.waitForFunction(()=>document.querySelectorAll('canvas').length>0,{timeout:10000});rendered=Date.now()-c0;}catch{}
const info=await p.evaluate(()=>({canvases:document.querySelectorAll('canvas').length,echarts:typeof window.echarts,timelineVisible:!![...document.querySelectorAll('*')].find(e=>/Plays over time|timeline/i.test(e.id||''))}));
console.log('chartRenderMs:',rendered,'| info:',JSON.stringify(info));
await b.close();})().catch(e=>{console.error('ERR',e.message);});
