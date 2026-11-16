const fs = require('node:fs');
const path = require('node:path');
const {checkDocumentLookups} = require('./test_document_lookups.js');
const html = fs.readFileSync(path.join(__dirname, '../backend/inventory/templates/inventory/index.html'), 'utf8');
checkDocumentLookups(html.split('<script>')[1].split('</script>')[0], true, true)
  .then(console.log).catch(error=>{console.error(error);process.exitCode=1;});
