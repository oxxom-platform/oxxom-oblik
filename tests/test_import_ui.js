async function checkImportUI(source){
 const nodes=new Map(),created=[];function node(id){if(nodes.has(id))return nodes.get(id);const n={id,value:'',checked:false,disabled:false,hidden:false,textContent:'',files:[],children:[],options:[],append(...c){this.children.push(...c);},replaceChildren(...c){this.children=c;this.options=c;this.value='';},add(o){this.options.push(o);},scrollIntoView(){}};nodes.set(id,n);return n;}
 const document={cookie:'csrftoken=test',getElementById:node,createElement(tag){const n=node('created-'+created.length);created.push(n);n.tag=tag;return n;},querySelectorAll:()=>[...nodes.values()]};
 class FormData{constructor(){this.values=new Map();}set(k,v){this.values.set(k,v);}get(k){return this.values.get(k);}}
 const cut=source.lastIndexOf('\ninitialize().catch');if(cut<0)throw Error('Missing init boundary');
 return new Function('document','FormData','node',`return(async()=>{
  ${source.slice(0,cut)}
  const assert=(condition,message)=>{if(!condition)throw Error(message);};
  kinds=[{kind:'warehouses',title:'Warehouses',fields:['name'],note:'Warehouse note',example:['New']},{kind:'products',title:'Products',fields:['name','kind','sku','unit','price'],note:'Product note',example:['P','goods','','шт','100']}];node('organization').value='O';node('kind').value='warehouses';
  const dto={id:'B',kind:'warehouses',filename:'test.csv',status:'preview',version:1,expired:false,expires_at:'2026-11-16T12:00:00Z',counts:{new:1,duplicate:1,error:0},page:1,total:2,rows:[{line:2,input:{name:'<img onerror=evil()>'},status:'new',errors:[]},{line:3,input:{name:'Existing'},status:'duplicate',errors:['Will skip']}]};
  renderBatch(dto);assert(node('confirm').disabled&&!node('ack').disabled,'Preview must require explicit review acknowledgement');
  assert(node('rows').children[0].children[1].textContent==='<img onerror=evil()>','CSV cells must render as inert text');
  node('ack').checked=true;controls();assert(!node('confirm').disabled,'Reviewed valid preview may confirm');
  renderBatch({...dto,page:2,rows:[]});assert(node('ack').checked,'Pagination must preserve acknowledgement for same batch version');
  renderBatch({...dto,version:2});assert(!node('ack').checked,'New review version requires new acknowledgement');
  renderBatch({...dto,counts:{new:1,duplicate:0,error:1}});node('ack').checked=true;controls();assert(node('confirm').disabled,'Any error must block entire import');
  renderBatch({...dto,expired:true});assert(node('confirm').disabled,'Expired preview cannot confirm');
  renderBatch({...dto,counts:{new:0,duplicate:2,error:0}});assert(node('confirm').disabled,'Duplicate-only file cannot create records');
  renderBatch(dto);node('ack').checked=true;let calls=[],fail=true;loadHistory=async()=>{};
  api=async(url,data)=>{calls.push({url,data});if(fail){fail=false;throw Error('Offline');}return {id:'B',status:'applied',version:2,confirmed_version:1,created:1,skipped:1};};
  await confirmImport();assert(current.status==='preview'&&current.version===1&&node('ack').checked,'Unknown result must retain same batch/version for safe retry');
  await confirmImport();assert(calls.every(c=>c.url.endsWith('/imports/B/confirm')&&c.data.version===1),'Confirmation retry must target same preview version');
  assert(current.status==='applied'&&node('confirm').hidden&&node('feedback').textContent.includes('Імпорт виконано'),'Success must show completed state without another upload');
  renderBatch(dto);node('ack').checked=true;api=async()=>{const error=Error('Changed catalog');error.status=409;throw error;};await confirmImport();assert(!node('ack').checked&&current.status==='preview','Conflict must clear acknowledgement and keep preview for refresh');
  api=async()=>({batch:{...dto,version:3}});await refreshPreview();assert(current.version===3&&!node('ack').checked,'Refresh must not automatically confirm or acknowledge');
  let refreshCalls=0;api=async()=>{if(!refreshCalls++){const error=Error('Stale');error.status=409;throw error;}return{batch:{...dto,version:4}};};await refreshPreview();assert(current.version===4&&!node('ack').checked,'Stale refresh must load latest saved preview without applying it');
  renderBatch({...dto,kind:'products'});assert(node('schema-note').textContent==='Product note'&&!node('units-link').hidden,'Opening product history must update schema help and unit link');
  node('kind').value='warehouses';node('file').files=[{name:'test.csv',size:10}];api=async(url,data)=>{assert(data instanceof FormData&&data.get('file').name==='test.csv','Preview must upload selected file as multipart');return{batch:dto};};await previewFile();assert(current.id==='B'&&!node('ack').checked,'Upload only creates an unacknowledged preview');
  node('file').onchange();assert(current===null&&node('review').hidden,'Choosing another file must invalidate previous confirmation');
  node('file').files=[{name:'large.csv',size:1048577}];let oversized=false;try{await previewFile();}catch{oversized=true;}assert(oversized&&current===null,'Oversize file must not retain old review');
  let resolveOld;api=async()=>new Promise(resolve=>resolveOld=resolve);node('file').files=[{name:'test.csv',size:10}];const pending=previewFile();generation++;node('organization').value='OTHER';resolveOld({batch:dto});await pending;assert(current===null,'Old organization upload response must not populate current context');
  return 'Import UI checks passed: review, error/expiry gates, text escaping, retry, refresh, schema, file invalidation and organization races';
 })()`)(document,FormData,node);
}
if(typeof module!=='undefined'){
 module.exports={checkImportUI};
 if(require.main===module){const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../backend/catalog_imports/templates/catalog_imports/index.html'),'utf8');checkImportUI(html.split('<script>')[1].split('</script>')[0]).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});}
}
