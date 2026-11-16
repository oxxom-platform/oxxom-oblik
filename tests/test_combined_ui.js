const fs=require('node:fs'),path=require('node:path');
const {checkDocumentLookups}=require('./test_document_lookups.js');
const {checkOperationsUI}=require('./test_operations_ui.js');
(async()=>{for(const app of ['documents','inventory']){const html=fs.readFileSync(path.join(__dirname,`../backend/${app}/templates/${app}/index.html`),'utf8');console.log(await checkDocumentLookups(html.split('<script>')[1].split('</script>')[0],app==='inventory',app==='inventory',true,true));}const html=fs.readFileSync(path.join(__dirname,'../backend/operations/templates/operations/index.html'),'utf8');console.log(await checkOperationsUI(html.split('<script>')[1].split('</script>')[0],true));})().catch(error=>{console.error(error);process.exitCode=1;});
