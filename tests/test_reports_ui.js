async function checkReportsUI(source){
  const nodes=new Map(),created=[];
  function node(id){if(nodes.has(id))return nodes.get(id);const n={id,value:'',textContent:'',hidden:false,disabled:false,children:[],options:[],events:{},add(o){this.options.push(o);},replaceChildren(...c){this.children=c;this.options=c;this.value='';},append(...c){this.children.push(...c);},addEventListener(name,cb){this.events[name]=cb;},click(){this.clicked=true;},remove(){this.removed=true;}};nodes.set(id,n);return n;}
  const document={getElementById:node,body:node('body'),createElement(tag){const n=node('created-'+created.length);n.tag=tag;created.push(n);return n;}};
  class Option{constructor(text,value){this.textContent=text;this.value=value;}}
  class Params{constructor(){this.values=new Map();}set(k,v){this.values.set(k,v);}toString(){return [...this.values].map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');}}
  let fetched=[],revoked=[];
  const fetch=async url=>{fetched.push(url);return {ok:true,status:200,blob:async()=>({fixture:true})};};
  const URL={createObjectURL:()=> 'blob:test',revokeObjectURL:url=>revoked.push(url)};
  const cut=source.lastIndexOf('\ninitialize().catch');if(cut<0)throw Error('Missing initialization boundary');
  return new Function('document','Option','URLSearchParams','URL','fetch','node','created','fetched','revoked',`return(async()=>{
    ${source.slice(0,cut)}
    const assert=(condition,message)=>{if(!condition)throw Error(message);};
    node('organization').value='O';schemas=[{kind:'sales',title:'Sales',filters:['from','to','counterparty','warehouse'],note:'Current'},{kind:'balances',title:'Balances',filters:['warehouse','product'],note:'Current balance'}];
    node('kind').value='sales';lookupsReady=true;node('from').value='2026-11-01';node('to').value='2026-11-15';node('counterparty').value='C';node('warehouse').value='W';node('product').value='G';node('account').value='A';
    const query=String(selectedFilters());assert(query.includes('counterparty=C')&&!query.includes('product=')&&!query.includes('account='),'Only supported visible filters must be sent');
    showFilters();assert(node('product-field').hidden&&!node('from-field').hidden,'Report type controls visible filters');
    const dto={kind:'sales',title:'Sales',note:'Current state',columns:[{key:'counterparty',label:'Client',type:'text'},{key:'total',label:'Total',type:'money'}],summary:{total:'500.00'},total:51,page:1,items:[{counterparty:'<img onerror=evil()>',total:'10.00'}]};
    let requests=[];api=async url=>{requests.push(url);return dto;};
    await loadReport();assert(last&&node('result').hidden===false,'Report result must be displayed');
    assert(node('summary').children[0].children[1].textContent==='500.00','Summary must use full server total, not visible rows');
    assert(node('items').children[0].children[0].textContent==='<img onerror=evil()>','Names must render as text, never markup');
    assert(!node('next').disabled&&node('prev').disabled,'Pagination must respect total');
    await exportCSV();assert(fetched.length===1&&fetched[0].includes('/reports/sales/export?'+query),'CSV must use same organization, type and filters');
    assert(!fetched[0].includes('page=')&&revoked[0]==='blob:test','Export must download all filtered rows and release blob');
    node('counterparty').value='Changed';controls();assert(node('export').disabled,'Changed filters invalidate export');
    let rejected=false;try{await exportCSV();}catch{rejected=true;}assert(rejected&&fetched.length===1,'Stale report must not export with changed filters');
    api=async()=>{throw Error('Offline');};try{await loadReport();}catch{}
    assert(node('result').hidden&&last===null&&node('message').textContent.includes('не сформовано'),'Failed request must hide old results and explain failure');
    node('kind').value='balances';showFilters();assert(node('from-field').hidden&&!node('product-field').hidden,'Balances must not imply historical date filtering');
    assert(!String(selectedFilters()).includes('from=')&&!String(selectedFilters()).includes('counterparty='),'Hidden date/customer must not leak to balance API');
    let resolveOld;api=async()=>new Promise(resolve=>resolveOld=resolve);
    const pending=loadReport();generation++;node('organization').value='B';resolveOld(dto);await pending;
    assert(last===null&&node('result').hidden,'Old organization response must not replace the current result');
    api=async()=>({...dto,kind:'balances',items:[],summary:{value:'0.00'},total:0});await loadReport();
    assert(!node('empty-hint').hidden&&node('next').disabled,'Empty report must explain drafts and filters');
    api=async()=>({read:false,reports:[]});await selectOrg();
    assert(node('workspace').hidden&&last===null&&node('message').textContent.includes('власнику'),'Denied organization must clear and hide financial data');
    return 'Report UI checks passed: filters, summaries, export, escaping, empty/error states, pagination and organization races';
  })()`)(document,Option,Params,URL,fetch,node,created,fetched,revoked);
}
if(typeof module!=='undefined'){
 module.exports={checkReportsUI};
 if(require.main===module){const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../backend/reports/templates/reports/index.html'),'utf8');checkReportsUI(html.split('<script>')[1].split('</script>')[0]).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});}
}
