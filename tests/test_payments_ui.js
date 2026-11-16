async function checkPaymentsUI(source){
  const nodes=new Map(),created=[];
  function node(id){
    if(nodes.has(id))return nodes.get(id);
    const item={id,value:'',disabled:false,hidden:false,textContent:'',options:[],children:[],dataset:{},open:false,
      add(option){this.options.push(option);},replaceChildren(...children){this.options=children;this.children=children;this.value='';},append(...children){this.children.push(...children);},
      setAttribute(name,value){this[name]=value;},getAttribute(name){return this[name];},addEventListener(){},querySelectorAll(){return this.children.flatMap(c=>[c,...c.children.flatMap(d=>d.children)]);},scrollIntoView(){},reset(){},showModal(){this.open=true;},close(){this.open=false;}};
    nodes.set(id,item);return item;
  }
  const document={cookie:'csrftoken=test',getElementById:node,createElement(tag){const item=node('created-'+created.length);item.tag=tag;created.push(item);return item;},querySelectorAll(){return [...nodes.values()];},querySelector(){return null;}};
  class Option{constructor(text,value){this.textContent=text;this.value=value;}}
  class FormData{constructor(){this.fixture='catalog-only';}}
  const cut=source.lastIndexOf('\ninitialize().catch');if(cut<0)throw Error('Missing initialization boundary');
  let key=0;
  return new Function('document','Option','FormData','crypto','window','confirm','node','created',`return(async()=>{
    ${source.slice(0,cut)}
    const assert=(condition,message)=>{if(!condition)throw Error(message);};
    node('organization').value='O';node('editor').hidden=true;
    const sourceLoader=loadSources;
    accountItems=[{id:'A',name:'Cash',kind:'cash',is_active:true,balance:'0.00'}];counterparties=[{id:'C',name:'Client'}];
    showPayment(null);node('account').value='A';node('counterparty').value='C';node('amount').value='1000.01';node('purpose').value='Keep this purpose';node('date').value='2026-11-15';
    assert(!node('save-post').hidden&&!node('save-draft').hidden,'New payment must offer primary combined action and draft');
    assert(money('9007199254740993.01')===900719925474099301n,'UI money preview must avoid float precision loss');
    assert(money('1.001')===null&&money('NaN')===null,'Preview must reject invalid decimals');
    rows=[{source:'sale',document:'S',amount:'0.01'}];summary();assert(node('allocation-summary').textContent.includes('1000.00'),'Exact cent remainder must be displayed');
    let calls=[],fail=true;
    const dto={id:'P',number:'ОП-2026-000001',direction:'in',date:'2026-11-15',account:'A',account_name:'Cash',counterparty:'C',counterparty_snapshot:{name:'Client'},amount:'1000.01',purpose:'Keep this purpose',allocations:[{source:'sale',document:'S',amount:'0.01',number:'Sale'}],status:'posted',version:2,is_active:true};
    api=async(url,data)=>{calls.push({url,data});if(fail){fail=false;throw Error('Network');}return {payment:dto};};refreshOverview=async()=>{};loadSources=async()=>{};
    await submit('post');assert(current===null&&node('purpose').value==='Keep this purpose','Unknown network outcome must retain form');
    assert(node('feedback').textContent.includes('Немає підтвердження'),'Unknown result must not claim no commit');
    await submit('post');assert(calls.length===2&&calls.every(c=>c.url.endsWith('/payments/save-and-post')),'Primary must send one atomic API action');
    assert(calls[0].data.idempotency_key===calls[1].data.idempotency_key,'Retry must reuse the same key');
    assert(calls[1].data.payment.counterparty==='C'&&calls[1].data.payment.allocations[0].amount==='0.01','Combined action must carry current selection and allocation');
    controls();assert(current.status==='posted'&&node('account').disabled&&node('save-post').hidden&&!node('edit-allocation').hidden,'Posted payment must lock header and expose allocation action');
    allocationEditing=true;rows=[{source:'sale',document:'S',amount:'100'}];calls=[];
    api=async(url,data)=>{calls.push({url,data});return {payment:{...dto,version:3,allocations:[]}};};
    await submit('allocate');assert(calls.length===1&&calls[0].url.endsWith('/P/allocate'),'Posted redistribution uses dedicated operation');
    assert(!('amount' in calls[0].data)&&calls[0].data.allocations[0].amount==='100','Redistribution must not alter cash header');
    showPayment(null);node('account').value='A';node('counterparty').value='C';node('amount').value='20';node('purpose').value='Draft';node('date').value='2026-11-15';calls=[];
    api=async(url,data)=>{calls.push({url,data});return {payment:{...dto,status:'draft',amount:'20',purpose:'Draft',allocations:[],version:1}};};
    await submit('draft');assert(calls[0].url.endsWith('/payments/add')&&!('idempotency_key' in calls[0].data),'Secondary must save only draft');
    assert(node('feedback').textContent.includes('не змінювалися'),'Draft result must state money did not move');
    const kept=current;rows=[{source:'sale',document:'S',amount:'21'}];calls=[];api=async(url,data)=>{calls.push({url,data});const e=Error('Too much');e.status=409;throw e;};
    await submit('post');assert(current===kept&&rows[0].amount==='21'&&node('purpose').value==='Draft','Validation failure must keep form and original DTO');
    const oldKey=calls[0].data.idempotency_key;rows[0].amount='19';await submit('post');assert(calls[1].data.idempotency_key!==oldKey,'Changed payload must receive a fresh key');
    current=null;node('account').value='';node('counterparty').value='C';node('amount').value='20';node('purpose').value='Preserve';rows=[];
    showAccount(null,true);node('account-name').value='New bank';node('account-kind').value='bank';node('account-details').value='UA test';node('account-active').value='true';
    loadAccounts=async()=>{};api=async()=>({item:{id:'B',name:'New bank',kind:'bank',is_active:true,balance:'0'}});
    await node('account-form').onsubmit({preventDefault(){}});await Promise.resolve();await Promise.resolve();
    assert(node('account').value==='B'&&node('purpose').value==='Preserve'&&node('amount').value==='20','Inline account creation must select account without losing payment fields');
    assert(!node('account-dialog').open,'Account dialog must close after success');
    // A delayed old lookup must not replace a newer organization context.
    loadSources=sourceLoader;busy=false;node('counterparty').value='C';node('direction').value='in';
    let resolveOld;api=async()=>new Promise(resolve=>resolveOld=resolve);
    const oldRequest=loadSources();generation++;
    resolveOld({items:[{id:'Old',number:'Old source',date:'2026-11-15',available:'10'}],total:1});await oldRequest;
    assert(sources.length===0,'Old organization response must not populate document options');
    api=async()=>({items:[],total:0});await loadSources();
    assert(node('source-state').textContent.includes('аванс'),'Empty documents must explain advance workflow');
    api=async()=>{throw Error('Offline');};try{await loadSources();}catch{}
    assert(node('source-state').textContent.includes('Не вдалося'),'Network failure must not be disguised as empty catalog');
    return 'Payment UI checks passed: atomic action, retry, drafts, allocation, errors, precision and inline account';
  })()`)(document,Option,FormData,{randomUUID:()=>String(++key)},{addEventListener(){}},()=>true,node,created);
}
if(typeof module!=='undefined'){
  module.exports={checkPaymentsUI};
  if(require.main===module){const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../backend/payments/templates/payments/index.html'),'utf8');checkPaymentsUI(html.split('<script>')[1].split('</script>')[0]).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});}
}
