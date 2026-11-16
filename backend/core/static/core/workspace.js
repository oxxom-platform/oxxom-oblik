/* The current org is validated on the server; URL context survives tabs and links. */
(()=>{
 'use strict';
 const node=document.getElementById('workspace-config');if(!node)return;
 const config=JSON.parse(node.textContent),guards=[],editedControls=new Map();
 let leaving=false;
 const dirty=()=>guards.some(check=>{try{return check();}catch{return true;}});
 const mayLeave=()=>!dirty()||window.confirm(globalThis.OxxomUI.t("Є незбережені зміни. Залишити сторінку без збереження?"));
 window.OxxomWorkspace={registerGuard:check=>guards.push(check),hasUnsavedChanges:dirty,canNavigate:()=>{if(!mayLeave())return false;leaving=true;return true;}};
 // Dialogs and per-member controls keep their own baseline before user edits.
 const valueOf=control=>control.type==='checkbox'?control.checked:control.value;
 document.addEventListener('focusin',event=>{const control=event.target;if(control.matches('input,select,textarea')&&control.closest('dialog,#members')&&!editedControls.has(control))editedControls.set(control,valueOf(control));});
 guards.push(()=>Array.from(editedControls).some(([control,value])=>control.isConnected&&(!control.closest('dialog')||control.closest('dialog').open)&&valueOf(control)!==value));
 document.addEventListener('close',event=>{if(event.target.tagName==='DIALOG')for(const control of editedControls.keys())if(event.target.contains(control))editedControls.delete(control);},true);
 const current=new URL(location.href);
 if(config.organization&&current.searchParams.get('organization')!==config.organization){current.searchParams.set('organization',config.organization);history.replaceState(null,'',current);}
 document.addEventListener('change',event=>{
  if(event.target.id!=='organization')return;
  const value=event.target.value;
  event.stopImmediatePropagation();
  if(!config.organizations.includes(value)||value===config.organization){event.target.value=config.organization;return;}
  if(!mayLeave()){event.target.value=config.organization;return;}
  const next=new URL(location.href);next.searchParams.set('organization',value);next.searchParams.delete('new');next.searchParams.delete('document');
  leaving=true;location.assign(next.href);
 },true);
 document.addEventListener('click',event=>{
  const link=event.target.closest('a[href]');if(!link)return;
  const next=new URL(link.href,location.href);
  if(next.origin!==location.origin||!config.paths.includes(next.pathname))return;
  // Explicit org links (including another tab) keep their own valid context.
  if(config.organization&&!next.searchParams.has('organization')){next.searchParams.set('organization',config.organization);link.href=next.href;}
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target==='_blank')return;
  if(next.href===location.href)return;
  if(!mayLeave()){event.preventDefault();event.stopImmediatePropagation();return;}
  leaving=true;
 },true);
 window.addEventListener('beforeunload',event=>{if(!leaving&&dirty()){event.preventDefault();event.returnValue='';}});
 window.addEventListener('pageshow',()=>{leaving=false;});
 document.addEventListener('DOMContentLoaded',()=>{
  const main=document.querySelector('body>main');if(main){main.id='workspace-main';main.tabIndex=-1;}
  const menu=document.getElementById('shell-navigation'),toggle=document.getElementById('shell-menu-toggle');
  const close=()=>{menu.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');};
  toggle.onclick=()=>{const open=!menu.classList.contains('is-open');menu.classList.toggle('is-open',open);toggle.setAttribute('aria-expanded',String(open));if(open)menu.querySelector('a')?.focus();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&menu.classList.contains('is-open')){close();toggle.focus();}});
  document.addEventListener('click',e=>{if(!menu.contains(e.target)&&e.target!==toggle)close();});
  document.getElementById('shell-signout').onclick=async()=>{
   if(!mayLeave())return;
   const button=document.getElementById('shell-signout'),message=document.getElementById('shell-message');button.disabled=true;message.textContent='';
   try{const csrf=document.cookie.split('; ').find(c=>c.startsWith('csrftoken='))?.split('=')[1];const response=await fetch('/api/v1/auth/logout',{method:'POST',headers:{'X-CSRFToken':csrf||''},body:new FormData()});if(!response.ok&&response.status!==401)throw Error(globalThis.OxxomUI.t("Не вдалося вийти. Повторіть."));leaving=true;location.assign('/login/');}catch(error){message.textContent=error.message;button.disabled=false;}
  };
 });
})();
