const fs=require('fs'), path=require('path');
const Babel=require('./node_modules/@babel/standalone');
function now(){return Number(process.hrtime.bigint())/1e6;}
const files=['dashboard.jsx','echarts-charts.jsx','charts.jsx','tweaks-panel.jsx','explorer.jsx'];
let totalBytes=0,totalMs=0;
// warm
Babel.transform('const x=<a/>;',{presets:['react']});
for(const f of files){
  const src=fs.readFileSync(path.join('web',f),'utf8'); totalBytes+=src.length;
  const t=now(); Babel.transform(src,{presets:['react']}); const ms=now()-t; totalMs+=ms;
  console.log(`${f.padEnd(22)} ${src.length.toString().padStart(7)} bytes  transform ${ms.toFixed(1)} ms`);
}
console.log(`TOTAL ${totalBytes} bytes, in-browser Babel transform ${totalMs.toFixed(1)} ms (excludes ~1.5MB Babel lib download+parse)`);
