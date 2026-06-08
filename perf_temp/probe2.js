const puppeteer=require('puppeteer'); const fs=require('fs'); const path=require('path');
const BASE=process.argv[2]; const MIRROR=path.join(__dirname,'mirror');
const map=u=>{if(u.includes('react-dom.production'))return'react-dom.production.min.js';if(u.includes('react.production'))return'react.production.min.js';if(u.includes('echarts'))return'echarts.min.js';return null;};
(async()=>{const b=await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});const p=await b.newPage();
await p.setViewport({width:1440,height:900});
await p.setRequestInterception(true);
p.on('request',r=>{const f=map(r.url());if(f){r.respond({status:200,headers:{'access-control-allow-origin':'*'},contentType:'application/javascript',body:fs.readFileSync(path.join(MIRROR,f))});}else if(r.url().includes('fonts.goog')||r.url().includes('gstatic')){r.abort();}else r.continue();});
const apiTimes={};
p.on('response',r=>{const u=r.url();if(u.includes('/api/')){apiTimes[u.split('/api/')[1].split('?')[0]]=Date.now();}});
await p.goto(BASE+'/',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>{const e=document.querySelector('.pill-live');return e&&/live data/i.test(e.textContent);},{timeout:30000});
await p.waitForFunction(()=>typeof window.echarts!=='undefined',{timeout:10000}).catch(()=>{});
async function clickAndTime(label){
  const t0=Date.now();
  await p.evaluate((lbl)=>{const b=[...document.querySelectorAll('.sidenav-item')].find(x=>x.textContent.trim().toLowerCase().startsWith(lbl));b&&b.click();},label);
  let ms=null;
  try{await p.waitForFunction(()=>{const c=[...document.querySelectorAll('canvas')];return c.some(x=>x.width>0&&x.getBoundingClientRect().width>0);},{timeout:8000});ms=Date.now()-t0;}catch{}
  return ms;
}
const timeline=await clickAndTime('timeline');
const audio=await clickAndTime('audio');
const cnv=await p.evaluate(()=>document.querySelectorAll('canvas').length);
console.log(JSON.stringify({timelineClickToCanvasMs:timeline,audioClickToCanvasMs:audio,totalCanvases:cnv}));
await b.close();})().catch(e=>console.error('ERR',e.message));
