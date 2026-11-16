// Exercise the actual language engine without a server or business records.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const source=readFileSync(new URL('../backend/core/static/core/ui/preferences.js',import.meta.url),'utf8');
function fixture({stored={},blocked=false,workspace=false,initial="uk",allowNavigation=true,dirty=false,cookie=""}={}){
 const events=new Map(),storage=new Map(Object.entries(stored));
 const navigation=[];
 const root={lang:initial,hasAttribute:key=>workspace&&key==='data-ui-workspace',dataset:{theme:'dark',themePreference:'system'},style:{colorScheme:'dark'}},meta={content:'#263d35'};
 const on=(name,fn)=>events.set(name,[...(events.get(name)||[]),fn]);
 const env={Map,Event,window:null,location:{protocol:'http:',href:'http://example.test/app/',reload:()=>navigation.push('reload'),replace:()=>navigation.push('replace')},OxxomWorkspace:{canNavigate:()=>allowNavigation,hasUnsavedChanges:()=>dirty},document:{cookie,documentElement:root,querySelector:selector=>selector==='meta[name="theme-color"]'?meta:null,querySelectorAll:()=>[],addEventListener:on,dispatchEvent:event=>(events.get(event.type)||[]).forEach(fn=>fn(event))},matchMedia:()=>{throw Error('OS theme must not be consulted');},addEventListener:on,
 localStorage:{getItem:key=>{if(blocked)throw Error('blocked');return storage.get(key);},setItem:(key,value)=>{if(blocked)throw Error('blocked');storage.set(key,value);},removeItem:key=>{if(blocked)throw Error('blocked');storage.delete(key);}},OxxomTranslations:{'Вхід':['Sign in','Вход','Přihlášení']}};
 env.window=env;runInNewContext(source,env);
 return {env,root,meta,storage,navigation,emit:(name,event)=>(events.get(name)||[]).forEach(fn=>fn(event))};
}
test('light appearance overrides old dark/system preferences even with blocked storage',()=>{
 for(const theme of ['dark','system','light','invalid'])for(const blocked of [false,true]){
  const f=fixture({stored:{'oxxom.oblik.theme':theme},blocked});assert.equal(f.root.dataset.theme,'light');assert.equal(f.root.style.colorScheme,'light');assert.equal(f.meta.content,'#ffffff');assert.equal(f.root.dataset.themePreference,undefined);
  if(!blocked)assert.equal(f.storage.has('oxxom.oblik.theme'),false);
 }
});
test('language persists and synchronises across tabs, including storage clear',()=>{
 const f=fixture();f.env.OxxomUI.chooseLanguage('cs');assert.equal(f.storage.size,1);assert.equal(f.storage.get('oxxom.oblik.language'),'cs');assert.equal(f.env.OxxomUI.t('Вхід'),'Přihlášení');
 const restored=fixture({stored:Object.fromEntries(f.storage)});assert.equal(restored.env.OxxomUI.language,'cs');
 f.emit('storage',{key:'oxxom.oblik.language',newValue:'ru'});assert.equal(f.env.OxxomUI.t('Вхід'),'Вход');
 f.emit('storage',{key:'oxxom.oblik.theme',newValue:'dark'});assert.equal(f.root.dataset.theme,'light');assert.equal(f.env.OxxomUI.language,'ru');
 f.emit('storage',{key:null,newValue:null});assert.equal(f.env.OxxomUI.language,'uk');
});
test('unsupported or blocked locale storage falls back safely without changing supplied data',()=>{
 for(const blocked of [false,true]){const f=fixture({blocked});f.env.OxxomUI.chooseLanguage('constructor');assert.equal(f.env.OxxomUI.language,'uk');
 for(const lang of ['uk','en','ru','cs']){f.env.OxxomUI.chooseLanguage(lang);assert.equal(f.env.OxxomUI.t('An organisation’s own name'),'An organisation’s own name');}}
});

test('landing preference reaches server-rendered pages; changing workspace language confirms unsaved changes',()=>{
 const blocked=fixture({workspace:true,initial:'ru',stored:{'oxxom.oblik.language':'ru'},allowNavigation:false});blocked.env.OxxomUI.chooseLanguage('cs');assert.equal(blocked.env.OxxomUI.language,'ru');assert.equal(blocked.navigation.length,0);
 const changed=fixture({workspace:true,initial:'ru',stored:{'oxxom.oblik.language':'ru'}});changed.env.OxxomUI.chooseLanguage('cs');assert.equal(changed.env.OxxomUI.language,'cs');assert.equal(changed.navigation[0],'reload');assert.ok(changed.env.document.cookie.includes('oxxom_language=cs'));
 const migrated=fixture({workspace:true,initial:'uk',stored:{'oxxom.oblik.language':'ru'}});assert.equal(migrated.navigation[0],'replace');
 const dirty=fixture({workspace:true,initial:'ru',stored:{'oxxom.oblik.language':'ru'},dirty:true});dirty.emit('storage',{key:'oxxom.oblik.language',newValue:'cs'});assert.equal(dirty.env.OxxomUI.language,'ru');assert.equal(dirty.navigation.length,0);
});
