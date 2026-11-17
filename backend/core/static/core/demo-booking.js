(()=>{
 'use strict';
 const form=document.getElementById('booking-form');
 if(!form)return;
 const hero=document.querySelector('.booking-hero');
 if(hero){
  const cue=document.createElement('a');cue.className='booking-form-cue';cue.href='#booking-form';
  cue.setAttribute('aria-label',document.getElementById('booking-form-title').textContent.trim());
  cue.innerHTML='<svg viewBox="0 0 32 56" width="28" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 5v39M7 35l9 9 9-9"/></svg>';
  hero.appendChild(cue);
  cue.addEventListener('click',event=>{
   event.preventDefault();form.setAttribute('tabindex','-1');form.focus({preventScroll:true});
   form.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  });
 }
 const mobile=document.querySelector('.mobile-nav');
 if(mobile){
  const summary=mobile.querySelector('summary');summary.setAttribute('aria-expanded','false');
  mobile.addEventListener('toggle',()=>summary.setAttribute('aria-expanded',String(mobile.open)));
  mobile.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>{mobile.open=false;}));
  document.addEventListener('click',event=>{if(!mobile.contains(event.target))mobile.open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&mobile.open){mobile.open=false;summary.focus();}});
 }
 const data=JSON.parse(document.getElementById('booking-data').textContent),messages=data.messages;
 const button=document.getElementById('booking-submit'),label=button.querySelector('span');
 const feedback=document.getElementById('booking-feedback'),receipt=document.getElementById('booking-receipt');
 let pending=false,complete=false;
 button.disabled=false;
 function show(message,error){feedback.textContent=message;feedback.dataset.error=String(error);feedback.hidden=false;feedback.focus();}
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(pending||complete)return;
  pending=true;button.disabled=true;form.setAttribute('aria-busy','true');label.textContent=messages.sending;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
  try{
   const body=new FormData(form);
   const response=await fetch(form.action,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'X-CSRFToken':body.get('csrfmiddlewaretoken'),'Accept':'application/json'},body:new URLSearchParams(body)});
   const result=await response.json();
   if(!response.ok){show(response.status===429?messages.limited:(result.error==='expired_ticket'?messages.expired:messages.failure),true);return;}
   if(result.status!=='test_received'||!/^[-a-f0-9]{36}$/.test(result.id))throw Error('Unexpected response');
   complete=true;show(messages.success,false);receipt.querySelector('code').textContent=result.id;receipt.hidden=false;
   // No personal values, external trackers, automatic retries or slot booking.
   document.dispatchEvent(new CustomEvent('oxxom:demo-request',{detail:{product:'oxxom-oblik',mode:'test',status:'received'}}));
  }catch{show(messages.failure,true);}
  finally{clearTimeout(timeout);pending=false;button.disabled=complete;form.removeAttribute('aria-busy');label.textContent=messages.submit;}
 });
})();
