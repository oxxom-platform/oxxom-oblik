import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const source=readFileSync(new URL('../backend/core/static/core/public-preferences.js',import.meta.url),'utf8');
function fixture({blockedStorage=false,blockedCookies=false}={}){
 const values=new Map([['oxxom.oblik.language','ru'],['oxxom.oblik.theme','dark']]);
 const events={},navigation=[],root={lang:'en',dataset:{},style:{}};
 const data={language:'en',locale:'en-GB',strings:{'Демонстрація:':'Demo:'},states:{},urls:{uk:'/',en:'/en/',ru:'/ru/',cs:'/cs/'}};
 let cookie='oxxom_language=ru';
 const login={addEventListener:(name,handler)=>events['login:'+name]=handler};
 const document={documentElement:root,getElementById:()=>({textContent:JSON.stringify(data)}),
  querySelectorAll:selector=>selector.startsWith('a[')?[login]:[],addEventListener:(name,handler)=>events[name]=handler};
 Object.defineProperty(document,'cookie',{get:()=>cookie,set:value=>{if(blockedCookies)throw Error('blocked');cookie=value;}});
 const env={document,location:{protocol:'https:',assign:value=>navigation.push(value)},
  localStorage:{setItem:(key,value)=>{if(blockedStorage)throw Error('blocked');values.set(key,value);},removeItem:key=>{if(blockedStorage)throw Error('blocked');values.delete(key);}}};
 env.window=env;runInNewContext(source,env);
 return{env,values,root,navigation,events,get cookie(){return cookie;}};
}
test('URL language wins over stored preference and reaches workspace links',()=>{
 const f=fixture();assert.equal(f.root.lang,'en');assert.equal(f.env.OxxomUI.language,'en');
 assert.equal(f.values.get('oxxom.oblik.language'),'en');assert.ok(f.cookie.includes('oxxom_language=en'));assert.ok(f.cookie.includes('; Secure'));
 f.values.set('oxxom.oblik.language','cs');f.events['login:click']();assert.equal(f.values.get('oxxom.oblik.language'),'en');
 assert.equal(f.values.has('oxxom.oblik.theme'),false);assert.equal(f.root.dataset.theme,'light');
});
test('language choices navigate only to known public URLs and leave SSR copy alone',()=>{
 const f=fixture();f.env.OxxomUI.chooseLanguage('cs');assert.deepEqual(f.navigation,['/cs/']);
 f.env.OxxomUI.chooseLanguage('constructor');f.env.OxxomUI.chooseLanguage('https://other.invalid');assert.deepEqual(f.navigation,['/cs/']);
 assert.equal(f.root.lang,'en');assert.equal(f.events.storage,undefined);
});
test('public demo continues working with blocked cookies or local storage',()=>{
 for(const blockedStorage of [false,true])for(const blockedCookies of [false,true]){
  const f=fixture({blockedStorage,blockedCookies});const label={textContent:''};
  f.env.OxxomUI.text(label,'Демонстрація:');assert.equal(label.textContent,'Demo:');
  f.env.OxxomUI.text(label,'Already localised');assert.equal(label.textContent,'Already localised');
 }
});
