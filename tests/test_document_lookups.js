/* UI workflow regression checks with a minimal DOM; no browser or dependencies. */
async function checkDocumentLookups(source, stockMode = false, productFlow = false, postingFlow = false, combinedFlow = false) {
  const elements = new Map();
  class Option {
    constructor(text, value) { this.textContent = text; this.value = value; }
  }
  function node(id) {
    if (elements.has(id)) return elements.get(id);
    const n = {id, value: '', disabled: false, hidden: false, textContent: '', options: [], open: false, children: [],
      setAttribute(key,value) { this[key]=value; }, getAttribute(key) { return this[key]; },
      append(...children) { this.children.push(...children); },
      add(o) { this.options.push(o); },
      replaceChildren(...values) { this.options = [...values]; this.value = ''; },
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {}, scrollIntoView() {}, addEventListener() {},
      reset() { const fields=this.id==='unit-form'?['unit-name','unit-symbol']:this.id==='product-form'?['product-name','product-sku','product-unit']:['quick-name','quick-code'];for(const key of fields)node(key).value='';if(this.id==='quick-form')node('quick-kind').value='company'; }
    };
    Object.defineProperty(n, 'selectedOptions', {get() {return n.options.filter(o => o.value === n.value);}});
    elements.set(id, n); return n;
  }
  const document = {cookie: 'csrftoken=test', getElementById: node,
    querySelectorAll(selector) {
      if (selector.startsWith('#quick-form')) return ['quick-name', 'quick-kind', 'quick-code', 'quick-save', 'quick-cancel'].map(node);
      return [...elements.values()];
    }};
  class FormData {
    constructor(form) {
      this.values = new Map();
      const fields = form.id==='unit-form'?[['name','unit-name'],['symbol','unit-symbol']]:form.id==='product-form'?[['name','product-name'],['sku','product-sku'],['unit','product-unit'],['price','product-price']]:[['name','quick-name'],['kind','quick-kind'],['code','quick-code']];
      for(const [field,id] of fields)if(!node(id).disabled)this.values.set(field,node(id).value);
    }
    get(key) {return this.values.get(key);}
    set(key,value) {this.values.set(key,value);}
  }
  let fail = false;
  const sent = []; const units = [];
  async function fetch(url, options) {
    if(!options.body){if(fail)return {ok:false,status:503,json:async()=>({error:'Offline'})};return {ok:true,status:200,json:async()=>({items:url.includes('status=archived')?[]:units,total:url.includes('status=archived')?0:units.length})};}
    if (!(options.body instanceof FormData)) throw Error('Catalog creation must use form encoding');
    if (options.headers['X-CSRFToken'] !== 'test') throw Error('Missing CSRF');
    sent.push({url, name: options.body.get('name'), kind: options.body.get('kind')});
    if (fail) return {ok:false, status:400, json:async()=>({error:'Invalid name'})};
    if(url.includes('/units/add')){const unit={id:'new-unit',name:options.body.get('name'),symbol:options.body.get('symbol')};units.push(unit);return {ok:true,status:201,json:async()=>({item:unit})};}
    if(url.includes('/products/add'))return {ok:true,status:201,json:async()=>({item:{id:'new-product',name:options.body.get('name'),kind:options.body.get('kind'),unit:options.body.get('unit'),unit_name:'шт',price:options.body.get('price')}})};
    const warehouse = url.includes('/warehouses/');
    return {ok:true, status:201, json:async()=>({item:{id:warehouse?'new-warehouse':'new-customer', name:options.body.get('name'), is_active:true, version:1}})};
  }
  const start = source.lastIndexOf('\ninit().catch');
  if (start < 0) throw Error('Missing init boundary');
  const run = new Function('document','Option','FormData','fetch','node','sent','setFail','stockMode','productFlow','postingFlow','combinedFlow','crypto', `return (async()=>{
    ${source.slice(0, start)}
    const assert=(condition,message)=>{if(!condition)throw Error(message);};
    const originalOpenDocument=openDocument;const originalRenderLines=renderLines;renderLines=()=>{};loadList=async()=>{};
    rights={read:true,write:true,show_cost:true};node('stock-kind').value='receipt';catalogRights={write:['counterparties','warehouses']};
    node('organization').value='org-A';
    node('date').value='2026-11-15';node('note').value='Unsaved note';
    rows=[{product:'product-A',quantity:'2',price:'1500.00'}];
    const originalRows=JSON.stringify(rows);
    quickCreate('counterparties');node('quick-name').value='Customer A';node('quick-kind').value='person';
    await node('quick-form').onsubmit({preventDefault(){}});
    assert(node('counterparty').value==='new-customer','New customer must be selected');
    assert(!node('quick-create').open,'Dialog must close after success');
    assert(!node('save').disabled,'Document Save must work after creating a customer');
    assert(JSON.stringify(rows)===originalRows,'Creating customer must preserve lines');
    assert(node('note').value==='Unsaved note'&&node('date').value==='2026-11-15','Document fields must survive');
    assert(sent[0].kind==='person'&&sent[0].name==='Customer A','Customer form fields must be posted');
    quickCreate('warehouses');node('quick-name').value='Warehouse A';
    await node('quick-form').onsubmit({preventDefault(){}});
    assert(node('warehouse').value==='new-warehouse','New warehouse must be selected');
    assert(sent[1].kind===undefined,'Hidden counterparty fields must not be posted for warehouse');
    setFail(true);quickCreate('counterparties');node('quick-name').value='Keep this name';
    await node('quick-form').onsubmit({preventDefault(){}});
    assert(node('quick-create').open&&node('quick-name').value==='Keep this name','Failed creation must preserve dialog');
    assert(JSON.stringify(rows)===originalRows,'Failed creation must preserve document');
    setFail(false);node('quick-create').close();
    loadCatalog=async()=>({products:[],counterparties:[{id:'new-customer',name:'Customer A'}],warehouses:[{id:'new-warehouse',name:'Warehouse A'}]});
    await refreshLookups();
    assert(node('counterparty').value==='new-customer'&&node('warehouse').value==='new-warehouse','Refresh must preserve choices');
    assert(JSON.stringify(rows)===originalRows&&node('note').value==='Unsaved note','Refresh must preserve lines and note');
    node('save').disabled=true;restoreControls();assert(!node('save').disabled,'Restore must re-enable Save');
    rights.write=false;lookupState();assert(node('create-counterparty').hidden&&node('create-warehouse').hidden,'Read-only users must not see creation buttons');
    if(stockMode){
      rights={read:true,write:true,show_cost:true};documentData=null;
      const created=[];
      document.createElement=tag=>{const element=node('created-'+created.length);element.tag=tag;element.children=[];element.append=(...children)=>element.children.push(...children);element.setAttribute=()=>{};created.push(element);return element;};
      catalog.products=[{id:'goods-A',name:'Goods A',unit_name:'шт',price:'999.00'}];
      rows=[{product:'',quantity:'10',price:''}];
      originalRenderLines();
      const choice=created.find(element=>element.tag==='select');choice.value='goods-A';choice.onchange();
      assert(rows[0].price==='','Sales price must never become purchase price');
      assert(rows[0].product==='goods-A'&&rows[0].unit_symbol==='шт','Goods choice must retain its unit');
    }
    if(productFlow){
      rights={read:true,write:true,show_cost:true};catalogRights={write:['products','units','warehouses','counterparties']};
      rows=[{id:'existing-line',product:'',quantity:'10',price:'60.00'}];
      const target=rows[0];
      node('note').value='Keep draft';
      await openProduct(target);
      assert(node('product-create').open,'Product dialog must open');
      assert(node('product-save').disabled,'Cannot create product without a unit');
      node('product-name').value='New Goods';node('product-price').value='100.00';
      node('create-unit').onclick();node('unit-name').value='Штука';node('unit-symbol').value='шт';
      await node('unit-form').onsubmit({preventDefault(){}});
      assert(node('product-unit').value==='new-unit','Created unit must be selected');
      assert(node('product-name').value==='New Goods','Unit creation must preserve product name');
      assert(!node('product-save').disabled,'Product save must become available');
      await node('product-form').onsubmit({preventDefault(){}});
      assert(target.product==='new-product','Created goods must select the original row');
      assert(target.quantity==='10'&&target.price==='60.00'&&target.id==='existing-line','Do not overwrite quantity, purchase price or line UUID');
      assert(catalog.products.find(p=>p.id==='new-product').price==='100.00','Catalog sales price must be separate');
      assert(!node('product-create').open&&node('note').value==='Keep draft','Save closes only dialog and preserves draft');
      const count=rows.length;await openProduct();node('product-cancel').onclick();assert(rows.length===count,'Cancel must not add an empty line');
      setFail(true);await openProduct(target);
      assert(!node('retry-units').hidden,'Network failure must offer retry');
      assert(node('unit-state').textContent.includes('Не вдалося'),'Network failure must not look like an empty catalog');
      setFail(false);await loadUnits();node('product-name').value='Keep failed product';node('product-price').value='25';
      setFail(true);await node('product-form').onsubmit({preventDefault(){}});
      assert(node('product-create').open&&node('product-name').value==='Keep failed product','Validation failure must preserve product form');
      assert(target.price==='60.00'&&target.quantity==='10','Failed creation must preserve stock line');
      setFail(false);closeProduct();
      catalog.products=[];catalog.serviceCount=1;catalog.archivedGoodsCount=2;lookupState();
      assert(node('empty-hint').textContent.includes('послуги')&&node('empty-hint').textContent.includes('архів'),'Explain services and archived goods');
      catalogRights={write:[]};lookupState();await openProduct(target);
      assert(node('create-product').hidden&&!node('product-create').open,'Cannot create goods without permission');
    }
    if(postingFlow){
      renderLines=()=>{};rights={read:true,write:true,show_cost:true,post:true};
      documentData={id:'posted-doc',version:1,status:'draft',is_active:true};
      rows=[{product:'goods-A',quantity:'10',price:'60.00'}];
      savedSignature=editorSignature();postingState();
      assert(!node('post-document').hidden&&!node('post-document').disabled,'Saved draft must allow posting');
      const requests=[];let failNext=true,nextStatus='posted';
      api=async(url,payload)=>{requests.push({...payload});if(failNext){failNext=false;throw Error('Retry me');}return {};};
      openDocument=async()=>{documentData.status=nextStatus;documentData.version++;savedSignature=editorSignature();postingState();};
      rows[0].quantity='11';postingState();await applyPosting('post');
      assert(!node('post-document').disabled&&requests.length===0,'Combined action stays available; legacy post-only action still protects unsaved data');
      rows[0].quantity='10';savedSignature=editorSignature();
      await applyPosting('post');postingState();await applyPosting('post');
      assert(requests.length===2&&requests[0].idempotency_key===requests[1].idempotency_key,'Retry must preserve idempotency key');
      assert(!editable()&&node('post-document').hidden&&!node('reverse-document').hidden,'Posted document must be read only and allow reversal');
      nextStatus='reversed';await applyPosting('reverse');
      assert(requests[2].idempotency_key!==requests[0].idempotency_key,'Reversal needs its own key');
      assert(node('reverse-document').hidden&&node('post-document').hidden,'Reversed document must not allow posting again');
      rights.post=false;await applyPosting('post');assert(requests.length===3,'Role without posting right must not send request');
    }
    if(combinedFlow){
      rights={read:true,write:true,show_cost:true,post:true};documentData=null;combinedAttempt=null;
      catalog={products:[{id:'G',name:'Goods',unit_name:'шт',kind:'goods',price:'100'}],warehouses:[{id:'A',name:'A'},{id:'B',name:'B'}],counterparties:[{id:'C',name:'C'},{id:'Other',name:'Other'}]};
      node('organization').value='org-A';node('stock-kind').value='receipt';node('counterparty').value='C';node('warehouse').value='A';node('date').value='2026-11-15';node('note').value='Keep draft';
      rows=[{product:'G',quantity:'10',price:'60.00',unit_symbol:'шт'}];
      const requests=[];let failFirst=true,responseStatus='posted';
      api=async(url,data)=>{requests.push({url,data});if(failFirst){failFirst=false;throw Error('Connection lost');}return {document:{id:'Saved',number:'Saved number',date:'2026-11-15',kind:'receipt',counterparty:'C',counterparty_snapshot:{name:'C'},warehouse:'A',warehouse_name:'A',note:'Keep draft',total:'600.00',status:responseStatus,version:2,is_active:true,lines:[{id:'Line',product:'G',name:'Goods',unit_symbol:'шт',quantity:'10.000',price:'60.00'}]}};};
      openDocument=async(id,prefetched)=>{assert(prefetched,'Save action must render its response without another request');await originalOpenDocument(id,prefetched);postingState();};
      renderLines=()=>{};loadList=async()=>{};
      const event={preventDefault(){},submitter:{value:'post'}};
      postingState();assert(!node('post-document').hidden&&!node('post-document').disabled,'New document must allow combined action');
      await node('document-form').onsubmit(event);
      assert(documentData===null&&rows[0].quantity==='10','Failed combined request must preserve unsaved form');
      await node('document-form').onsubmit(event);
      assert(requests.length===2&&requests.every(r=>r.url.endsWith('/save-and-post')),'One action must use combined endpoint');
      assert(requests[0].data.idempotency_key===requests[1].data.idempotency_key,'Retry must keep combined key');
      assert(requests[1].data.document.counterparty==='C','Supplier selection must be included in same request');
      assert(!editable()&&documentData.status==='posted','Combined success must show posted read-only document');
      assert(node('stock-result-link').href.includes('org-A'),'Result link must retain active organization');
      assert(node('warehouse').options.length===3,'Rendered response must retain lookup choices');
      documentData={id:'Existing',version:1,status:'draft',is_active:true};node('counterparty').value='Other';savedSignature='old';rows=[{product:'G',quantity:'1',price:'60'}];
      await node('document-form').onsubmit(event);
      assert(requests[2].url.includes('/Existing/save-and-post')&&requests[2].data.document.version===1,'Dirty existing document must save and post directly');
      documentData=null;responseStatus='draft';rows=[{product:'G',quantity:'1',price:'60'}];
      await node('document-form').onsubmit({preventDefault(){},submitter:{value:'draft'}});
      assert(requests[3].url.endsWith('/add')&&!requests[3].data.document,'Secondary action must save draft only');
      assert(editable()&&documentData.status==='draft','Draft success must remain editable');
    }
    return stockMode?'Inventory UI workflow checks passed':'Document lookup workflow checks passed';
  })()`);
  return run(document, Option, FormData, fetch, node, sent, value=>fail=value, stockMode, productFlow, postingFlow, combinedFlow, {randomUUID:()=>{sent.push({generated:true});return 'request-'+sent.length;}});
}
if (typeof module !== 'undefined') {
  module.exports = {checkDocumentLookups};
  if (require.main === module) {
    const fs = require('node:fs'); const path = require('node:path');
    const html = fs.readFileSync(path.join(__dirname,'../backend/documents/templates/documents/index.html'),'utf8');
    checkDocumentLookups(html.split('<script>')[1].split('</script>')[0]).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});
  }
}
