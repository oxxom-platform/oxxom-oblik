/* The public URL determines the page language; it also reaches the workspace. */
(()=>{
 'use strict';
 const data=JSON.parse(document.getElementById('landing-data').textContent);
 const key='oxxom.oblik.language',root=document.documentElement;
 function persist(){
  try{localStorage.setItem(key,data.language);localStorage.removeItem('oxxom.oblik.theme');}catch{}
  try{document.cookie='oxxom_language='+data.language+'; Path=/; Max-Age=31536000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');}catch{}
 }
 persist();root.dataset.theme='light';root.style.colorScheme='light';
 const t=source=>data.strings[source]||source;
 window.OxxomUI={language:data.language,locale:data.locale,landing:data,t,
  text:(node,source)=>{if(node)node.textContent=t(source);},
  chooseLanguage:language=>{if(Object.hasOwn(data.urls,language))location.assign(data.urls[language]);}
 };
 // Another tab may choose its own language; do not rewrite this URL's HTML.
 document.querySelectorAll('a[href^="/login/"]').forEach(link=>link.addEventListener('click',persist));
 const menus=[...document.querySelectorAll('.public-language')];
 menus.forEach(menu=>{
  const summary=menu.querySelector('summary');summary.setAttribute('aria-expanded','false');
  menu.addEventListener('toggle',()=>summary.setAttribute('aria-expanded',String(menu.open)));
 });
 document.addEventListener('click',event=>menus.forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
 document.addEventListener('keydown',event=>{if(event.key==='Escape')menus.forEach(menu=>{if(menu.open){menu.open=false;menu.querySelector('summary').focus();}});});
})();
