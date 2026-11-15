let packages=[];
const search=document.querySelector('#search');
const status=document.querySelector('#status');
const rows=document.querySelector('#rows');
const empty=document.querySelector('#empty');
const css=s=>s.includes('missing')?'bad':s.includes('preserved')||s==='complete'?'ok':'warn';
function element(tag,text,className){
  const node=document.createElement(tag);
  node.textContent=text;
  if(className)node.className=className;
  return node;
}
function cell(row,node){
  const td=document.createElement('td');
  td.append(node);
  row.append(td);
}
function render(){
  const q=search.value.toLowerCase();
  const filtered=packages.filter(p=>(!status.value||p.artifact_status===status.value)&&JSON.stringify(p).toLowerCase().includes(q));
  rows.replaceChildren(...filtered.map(p=>{
    const row=document.createElement('tr');
    const product=element('span',p.product||(p.package_model==null?'Unclassified firmware':`Package model ${p.package_model}`));
    if(p.model_number)product.append(document.createElement('br'),element('code',p.model_number));
    cell(row,product);
    const repository=p.repository||document.querySelector('#repository').pathname.slice(1);
    const version=element('span',p.version||'—');
    if(p.release_tag&&/^[\w.-]+\/[\w.-]+$/.test(repository)){
      const link=element('a',p.version||p.release_tag);
      link.href=`https://github.com/${repository}/releases/tag/${encodeURIComponent(p.release_tag)}`;
      version.replaceChildren(link);
    }
    cell(row,version);
    cell(row,element('span',p.package_model??'—'));
    for(const state of [p.artifact_status,p.raw_status]){
      const label=String(state||'unknown');
      cell(row,element('span',label,`pill ${css(label)}`));
    }
    cell(row,element('code',p.sha256?p.sha256.slice(0,12)+'…':'—'));
    return row;
  }));
  empty.hidden=filtered.length!==0;
}
const loadCatalog=fetch('../data/catalog.json',{cache:'no-store'}).then(r=>{
  if(!r.ok)throw new Error(`Catalog HTTP ${r.status}`);
  return r.json();
});
loadCatalog.then(data=>{
  if(!Array.isArray(data.packages))throw new Error('Invalid catalog');
  packages=data.packages;
  [...new Set(packages.map(p=>p.artifact_status).filter(Boolean))].sort().forEach(s=>status.add(new Option(s,s)));
  const preserved=packages.filter(p=>String(p.artifact_status).startsWith('preserved')).length;
  const stats=document.querySelector('#stats');
  for(const [count,label] of [[packages.length,'records'],[preserved,'preserved']]){
    const stat=document.createElement('span');
    stat.append(element('b',count),label);
    stats.append(stat);
  }
  render();
}).catch(()=>{
  empty.hidden=false;
  empty.textContent=location.protocol==='file:'
    ?'Serve the repository over HTTP to load the catalog. See the archive reference for instructions.'
    :'Catalog could not be loaded. Serve the repository root so data/catalog.json is available.';
});
search.addEventListener('input',render);status.addEventListener('change',render);
