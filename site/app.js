let packages=[];
const search=document.querySelector('#search');
const status=document.querySelector('#status');
const rows=document.querySelector('#rows');
const empty=document.querySelector('#empty');
const css=s=>s.includes('missing')?'bad':s.includes('preserved')||s==='complete'?'ok':'warn';
const pill=s=>`<span class="pill ${css(s)}">${s}</span>`;
function render(){
  const q=search.value.toLowerCase();
  const filtered=packages.filter(p=>(!status.value||p.artifact_status===status.value)&&JSON.stringify(p).toLowerCase().includes(q));
  rows.innerHTML=filtered.map(p=>`<tr><td>${p.product}<br><code>${p.model_number}</code></td><td>${p.version}</td><td>${p.package_model}</td><td>${pill(p.artifact_status)}</td><td>${pill(p.raw_status)}</td><td><code>${p.sha256?p.sha256.slice(0,12)+'…':'—'}</code></td></tr>`).join('');
  empty.hidden=filtered.length!==0;
}
fetch('../data/catalog.json').then(r=>r.json()).then(data=>{
  packages=data.packages;
  [...new Set(packages.map(p=>p.artifact_status))].sort().forEach(s=>status.add(new Option(s,s)));
  const preserved=packages.filter(p=>p.artifact_status.startsWith('preserved')).length;
  document.querySelector('#stats').innerHTML=`<span><b>${packages.length}</b>records</span><span><b>${preserved}</b>preserved</span>`;
  render();
}).catch(()=>{empty.hidden=false;empty.textContent='Serve the repository root with a local HTTP server to load the catalog.'});
search.addEventListener('input',render);status.addEventListener('change',render);

