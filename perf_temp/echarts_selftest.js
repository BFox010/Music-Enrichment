const puppeteer=require('puppeteer'); const fs=require('fs');
(async()=>{const b=await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});const p=await b.newPage();
const ec=fs.readFileSync('mirror/echarts.min.js','utf8');
await p.setContent('<div id="c" style="width:600px;height:400px"></div>');
await p.addScriptTag({content:ec});
const res=await p.evaluate(()=>{
  const el=document.getElementById('c');
  const ch=echarts.init(el);
  ch.setOption({xAxis:{type:'category',data:['a','b','c']},yAxis:{type:'value'},series:[{type:'line',data:[1,2,3]}]});
  return {hasInstance:!!echarts.getInstanceByDom(el),canvases:document.querySelectorAll('canvas').length,canvasW:document.querySelector('canvas')?.width};
});
console.log('ECharts self-test in headless:',JSON.stringify(res));
await b.close();})().catch(e=>console.error('ERR',e.message));
