/* Light presentation and language preferences only. */
(()=>{
 'use strict';
 const root=document.documentElement,languageKey='oxxom.oblik.language',workspace=root.hasAttribute('data-ui-workspace');
 const cookieLanguage=()=>{const match=(document.cookie||'').match(/(?:^|;\s*)oxxom_language=([^;]*)/);return match?match[1]:null;};
 const validLanguage=value=>['uk','en','ru','cs'].includes(value)?value:'uk';
 const read=key=>{try{return localStorage.getItem(key);}catch{return null;}};
 const save=(key,value)=>{try{localStorage.setItem(key,value);}catch{/* Preferences still work for this page. */}};
 // Ignore and retire the old theme preference, including devices using dark mode.
 root.dataset.theme='light';delete root.dataset.themePreference;root.style.colorScheme='light';
 const meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.content='#ffffff';
 try{localStorage.removeItem('oxxom.oblik.theme');}catch{/* Storage may be blocked. */}
 let language=validLanguage(read(languageKey)||cookieLanguage()||root.lang);
 function saveCookie(){try{document.cookie='oxxom_language='+language+'; Path=/; Max-Age=31536000; SameSite=Lax'+(globalThis.location?.protocol==='https:'?'; Secure':'');}catch{}}
 saveCookie();
 const sources=new Map(),attributes=new Map(),dynamic=new Map();
 function t(source){const row=window.OxxomTranslations?.[source];return language==='uk'||!row?source:row[{en:0,ru:1,cs:2}[language]];}
 function format(source,...values){return t(source).replace(/\{(\d+)\}/g,(match,index)=>index<values.length?String(values[index]):match);}
 function displayText(node,value){
  const element=node.nodeType===3?node.parentElement:node;
  return document.body?.dataset.uiPage==='landing'&&element.closest('h1,h2,h3,h4,h5,h6')?value.replace(/\.+(\s*)$/u,'$1'):value;
 }
 function text(node,source){if(!node)return;dynamic.set(node,source);node.textContent=displayText(node,t(source));}
 function controls(){
  document.querySelectorAll('[data-language-select]').forEach(select=>select.value=language);
 }
 function translate(){
  for(const [node,source] of sources){if(node.isConnected)node.nodeValue=displayText(node,source.replace(source.trim(),t(source.trim())));else sources.delete(node);}
  for(const [node,entries] of attributes){if(node.isConnected)for(const [key,source] of entries)node.setAttribute(key,t(source));else attributes.delete(node);}
  for(const [node,source] of dynamic){if(node.isConnected)node.textContent=displayText(node,t(source));else dynamic.delete(node);}
  if(document.body?.hasAttribute('data-ui-public'))root.lang=language;
  document.querySelectorAll('[data-ui-scope]').forEach(scope=>scope.lang=language);
  const locale=document.querySelector('meta[property="og:locale"]');if(locale)locale.content={uk:'uk_UA',en:'en_GB',ru:'ru_UA',cs:'cs_CZ'}[language];
  controls();
 }
 function chooseLanguage(value){
  const next=validLanguage(value);if(next===language){controls();return;}
  if(workspace&&globalThis.OxxomWorkspace?.canNavigate&&!globalThis.OxxomWorkspace.canNavigate()){controls();return;}
  language=next;save(languageKey,language);saveCookie();
  if(workspace){location.reload();return;}
  translate();document.dispatchEvent(new Event('oxxom:language-change'));
 }
 window.OxxomUI={t,text,format,get language(){return language;},get locale(){return {uk:'uk-UA',en:'en-GB',ru:'ru-UA',cs:'cs-CZ'}[language];},chooseLanguage};
 document.addEventListener('DOMContentLoaded',()=>{
  // Register only authored public copy and preference controls, never user records,
  // credentials, recovery codes or organisation names.
  const scopes=document.body.hasAttribute('data-ui-public')?[document.head,document.body]:[...document.querySelectorAll('[data-ui-scope]')];
  for(const scope of scopes){
   const walker=document.createTreeWalker(scope,NodeFilter.SHOW_TEXT);let node;
   while((node=walker.nextNode())){if(!node.parentElement.closest('script,style,textarea,input,#codes,#orgs,[data-ui-private]')&&(window.OxxomTranslations?.[node.nodeValue.trim()]||(document.body.dataset.uiPage==='landing'&&node.parentElement.closest('h1,h2,h3,h4,h5,h6'))))sources.set(node,node.nodeValue);}
   scope.querySelectorAll('[aria-label],[alt],[title],meta[content]').forEach(element=>{
    const values=[];for(const key of ['aria-label','alt','title','content']){const value=element.getAttribute(key);if(value&&window.OxxomTranslations?.[value])values.push([key,value]);}if(values.length)attributes.set(element,values);
   });
  }
  translate();
 });
 document.addEventListener('invalid',event=>{
  const input=event.target;if(!workspace||!input.validity||!input.setCustomValidity)return;
  const v=input.validity;let message='Перевірте введене значення.';
  if(v.valueMissing)message=input.tagName==='SELECT'?'Оберіть значення зі списку.':'Заповніть це поле.';
  if(v.typeMismatch&&input.type==='email')message='Вкажіть коректну email-адресу.';
  if(v.tooShort){input.setCustomValidity(format('Введіть щонайменше {0} символів.',input.minLength));return;}
  if(v.tooLong){input.setCustomValidity(format('Введіть не більше {0} символів.',input.maxLength));return;}
  if(v.rangeUnderflow){input.setCustomValidity(format('Мінімальне значення: {0}.',input.min));return;}
  if(v.rangeOverflow){input.setCustomValidity(format('Максимальне значення: {0}.',input.max));return;}
  input.setCustomValidity(t(message));
 },true);
 document.addEventListener('input',event=>{if(workspace&&event.target.setCustomValidity)event.target.setCustomValidity('');});
 document.addEventListener('change',event=>{if(event.target.setCustomValidity)event.target.setCustomValidity('');if(event.target.matches('[data-language-select]'))chooseLanguage(event.target.value);});
 window.addEventListener('storage',event=>{if(event.key===languageKey||event.key===null){
  if(workspace&&globalThis.OxxomWorkspace?.hasUnsavedChanges?.())return;
  const next=validLanguage(event.newValue);if(next===language)return;language=next;saveCookie();
  if(workspace){location.reload();return;}translate();document.dispatchEvent(new Event('oxxom:language-change'));
 }});
 // A saved landing preference reaches server-rendered pages even on the first
 // direct workspace visit after this update. Cookie confirmation prevents loops.
 if(workspace&&root.lang!==language&&cookieLanguage()===language)location.replace(location.href);
})();
