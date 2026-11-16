function printDOM(){
 const nodes=new Map();function node(id){if(nodes.has(id))return nodes.get(id);const n={id,value:'',textContent:'',hidden:false,disabled:false,options:[],children:[],attributes:{},events:{},add(o){this.options.push(o);},append(...c){this.children.push(...c);},replaceChildren(...c){this.children=c;this.options=c;this.value='';},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},addEventListener(k,v){this.events[k]=v;},querySelectorAll(){return[];},focus(){},scrollIntoView(){},close(){},showModal(){},reset(){}};nodes.set(id,n);return n;}
 const document={cookie:'csrftoken=test',getElementById:node,createElement:()=>node('created-'+nodes.size),querySelectorAll:()=>[]};return{node,document};
}

async function checkPrintSettings(source){
 const {node,document}=printDOM();const cut=source.lastIndexOf('\ninitialize().catch');if(cut<0)throw Error('No initialization boundary');
 return new Function('document','window','confirm','node',`return(async()=>{
  ${source.slice(0,cut)}
  const assert=(condition,message)=>{if(!condition)throw Error(message);};
  node('organization').value='O';let data={profile:{legal_name:'Original',kind:'company',code:'1234',address:'Address',bank_details:'Bank'},version:0,configured:false,write:true};
  show(data);assert(!node('save').disabled&&node('legal_name').value==='Original','Owner must edit real profile defaults');
  let sent;history=async()=>{};api=async(url,payload)=>{sent=payload;return{...data,profile:{...data.profile,legal_name:payload.legal_name},version:1,configured:true};};
  node('legal_name').value='Updated';await node('profile-form').onsubmit({preventDefault(){}});
  assert(sent.version===0&&sent.legal_name==='Updated'&&version===1,'Profile save must send optimistic version and retain returned version');
  node('legal_name').value='Keep this edit';api=async()=>{throw Error('Conflict');};await node('profile-form').onsubmit({preventDefault(){}});
  assert(node('legal_name').value==='Keep this edit'&&node('form-message').textContent==='Conflict','Failed save must keep user input');
  show({...data,write:false});assert(node('save').hidden&&node('legal_name').disabled,'Read-only role must not edit profile');
  let resolveOld;api=async()=>new Promise(resolve=>resolveOld=resolve);const request=load();generation++;node('organization').value='B';resolveOld(data);await request;
  assert(!loaded&&node('editor').hidden,'Old organization response must not populate current form');
  return 'Print settings checks passed: versions, input retention, read-only access and organization races';
 })()`)(document,{addEventListener(){}},()=>true,node);
}

async function checkDocumentPrintLinks(source){
 const {node,document}=printDOM();const cut=source.lastIndexOf('\ninit().catch');if(cut<0)throw Error('No init boundary');
 return new Function('document','node',`return(async()=>{
  ${source.slice(0,cut)}
  const assert=(condition,message)=>{if(!condition)throw Error(message);};
  node('organization').value='O';node('date').value='2026-11-15';node('counterparty').value='C';node('warehouse').value='W';
  documentData={id:'D',version:2,status:'posted',is_active:true,lines:[{kind:'goods'},{kind:'service'}]};rows=[];savedSignature=editorSignature();printState();
  assert(!node('print-actions').hidden&&!node('print-delivery').hidden&&!node('print-act').hidden,'Posted mixed sale must offer all three forms');
  assert(node('print-invoice').href==='/print/O/D/invoice/?version=2','Print link must bind organization, document, form and version');
  node('note').value='Unsaved';printState();let prevented=false;node('print-invoice').onclick({preventDefault(){prevented=true;}});
  assert(prevented&&node('print-invoice').getAttribute('aria-disabled')==='true','Dirty draft must not print saved data as edited data');
  documentData.status='draft';printState();assert(node('print-act').hidden&&node('print-delivery').hidden,'Draft must offer invoice only');
  documentData=null;printState();assert(node('print-actions').hidden,'Unsaved new document must not offer print links');
  return 'Document print links checked: form subsets, saved version, dirty form and new document';
 })()`)(document,node);
}

if(typeof module!=='undefined'){
 module.exports={checkPrintSettings,checkDocumentPrintLinks};
 if(require.main===module){const fs=require('node:fs'),path=require('node:path');const script=file=>fs.readFileSync(path.join(__dirname,file),'utf8').split('<script>')[1].split('</script>')[0];(async()=>{console.log(await checkPrintSettings(script('../backend/printing/templates/printing/settings.html')));console.log(await checkDocumentPrintLinks(script('../backend/documents/templates/documents/index.html')));})().catch(error=>{console.error(error);process.exitCode=1;});}
}
