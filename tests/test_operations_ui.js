async function checkOperationsUI(source,combinedFlow=false){
  const nodes=new Map(),created=[];
  function node(id){
    if(nodes.has(id))return nodes.get(id);
    const item={id,value:'',disabled:false,hidden:false,textContent:'',options:[],children:[],open:false,
      add(option){this.options.push(option);},replaceChildren(...children){this.options=children;this.children=children;this.value='';},append(...children){this.children.push(...children);},
      setAttribute(){},addEventListener(){},focus(){},scrollIntoView(){},reset(){},showModal(){this.open=true;},close(){this.open=false;}};
    Object.defineProperty(item,'selectedOptions',{get(){return item.options.filter(x=>x.value===item.value);}});
    nodes.set(id,item);return item;
  }
  const document={cookie:'csrftoken=test',getElementById:node,createElement(tag){const item=node('created-'+created.length);item.tag=tag;created.push(item);return item;},querySelectorAll(selector){return selector==='.financial'?[...nodes.values()].filter(n=>n.className==='financial'):[...nodes.values()];}};
  class Option{constructor(text,value){this.textContent=text;this.value=value;}}
  const cut=source.lastIndexOf('\ninit().catch');
  if(cut<0)throw Error('Missing init boundary');
  let key=0;
  const run=new Function('document','Option','crypto','node','created','combinedFlow',`return (async()=>{
    ${source.slice(0,cut)}
    const assert=(value,message)=>{if(!value)throw Error(message);};
    rights={read:['transfer','writeoff','return'],write:['transfer','writeoff','return'],post:true,show_cost:true};catalogRights={write:['products','units','warehouses']};
    catalog={products:[{id:'G',name:'Goods',unit_name:'шт'}],warehouses:[{id:'A',name:'A'},{id:'B',name:'B'}]};
    node('organization').value='O';node('kind').value='transfer';node('warehouse').value='A';node('target').value='B';node('date').value='2026-11-15';node('note').value='Keep this note';
    lookupState();assert(!node('target-block').hidden&&node('source-block').hidden,'Transfer must choose two warehouses');
    node('add-line').onclick();let selector=created.filter(n=>n.tag==='select').at(-1);selector.value='G';selector.onchange();
    assert(rows[0].product==='G'&&rows[0].unit_symbol==='шт','Only selected goods must populate line');
    let quantity=created.filter(n=>n.tag==='input').at(-1);quantity.value='10';quantity.oninput();assert(rows[0].quantity==='10','Quantity input must persist');
    node('kind').value='return';node('kind').onchange();assert(rows.length===0,'Changing operation kind must clear incompatible lines');
    api=async()=>({items:[{id:'L',product:'G',name:'Goods snapshot',unit_symbol:'шт',remaining:'2.000'}],warehouse:'A',warehouse_name:'A'});
    node('source-sale').value='S';await node('source-sale').onchange();node('add-line').onclick();
    selector=created.filter(n=>n.tag==='select').at(-1);selector.value='L';selector.onchange();
    assert(rows[0].source_line==='L'&&rows[0].product==='G','Return must select a line of the source sale');
    assert(node('warehouse').value==='A'&&node('warehouse').disabled,'Return warehouse comes from sale');
    const requests=[];let nextStatus='draft',fail=false;
    api=async(url,data)=>{requests.push({url,data});if(fail){fail=false;throw Error('Offline');}return {document:{id:'D'}};};
    loadList=async()=>{};openDocument=async()=>{documentData={id:'D',kind:'return',version:(documentData?.version||0)+1,status:nextStatus,is_active:true};savedSignature=signature();};
    await node('operation-form').onsubmit({preventDefault(){}});
    const saved=requests[0].data;assert(saved.source_sale==='S'&&saved.lines[0].source_line==='L','Save must bind return to sale line');
    assert(!('price' in saved.lines[0])&&!('cost' in saved.lines[0]),'Client must not set cost');
    assert(node('form-message').textContent.includes('збережено'),'Save result must be visible beside buttons');
    rows[0].quantity='2';postingState();await applyPosting('post');assert(requests.length===1,'Dirty document must not post');
    rows[0].quantity='1';savedSignature=signature();nextStatus='posted';fail=true;
    await applyPosting('post');await applyPosting('post');
    assert(requests[1].data.idempotency_key===requests[2].data.idempotency_key,'Retry must use the same key');
    assert(!editable()&&node('post').hidden&&!node('reverse').hidden,'Posted operation must be immutable');
    nextStatus='reversed';await applyPosting('reverse');assert(node('post').hidden&&node('reverse').hidden,'Reversed operation must not post again');
    assert(node('note').value==='Keep this note','Rows and actions must not erase note');
    // A slow response for sale A must not overwrite a later choice of sale B.
    let resolveA;api=async url=>url.endsWith('/a')?new Promise(resolve=>resolveA=resolve):{items:[{id:'B-line'}],warehouse:'B',warehouse_name:'B'};
    node('source-sale').value='a';const pending=fetchSource('a');node('source-sale').value='b';await fetchSource('b');
    resolveA({items:[{id:'A-line'}],warehouse:'A',warehouse_name:'A'});await pending;
    assert(node('warehouse').value==='B'&&sourceRows[0].id==='B-line','Old source response must not overwrite new selection');
    if(combinedFlow){
      rights={read:['writeoff'],write:['writeoff'],post:true,show_cost:true};documentData=null;combinedAttempt=null;
      node('kind').value='writeoff';node('source-sale').value='';node('warehouse').value='A';node('note').value='One action';rows=[{product:'G',quantity:'1'}];
      const combinedRequests=[];let failNext=true;
      api=async(url,data)=>{combinedRequests.push({url,data});if(failNext){failNext=false;throw Error('Network failed');}return {document:{id:'Combined',status:'posted',version:2,is_active:true,kind:'writeoff'}};};
      openDocument=async(id,prefetched)=>{assert(prefetched,'Combined response must be rendered directly');documentData=prefetched;savedSignature=signature();postingState();};
      const event={preventDefault(){},submitter:{value:'post'}};
      await node('operation-form').onsubmit(event);await node('operation-form').onsubmit(event);
      assert(combinedRequests.length===2&&combinedRequests.every(r=>r.url.endsWith('/operations/save-and-post')),'Combined operation must use one endpoint');
      assert(combinedRequests[0].data.idempotency_key===combinedRequests[1].data.idempotency_key,'Combined retry must reuse key');
      assert(combinedRequests[1].data.document.kind==='writeoff'&&combinedRequests[1].data.document.lines[0].quantity==='1','Combined action must include current form');
      assert(!editable()&&node('post').hidden&&node('form-message').textContent.includes('проведено'),'Combined operation success must show posted state');
    }
    return 'Operation UI workflow checks passed';
  })()`);
  return run(document,Option,{randomUUID:()=>String(++key)},node,created,combinedFlow);
}
if(typeof module!=='undefined'){
 module.exports={checkOperationsUI};
 if(require.main===module){const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../backend/operations/templates/operations/index.html'),'utf8');checkOperationsUI(html.split('<script>')[1].split('</script>')[0]).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});}
}
