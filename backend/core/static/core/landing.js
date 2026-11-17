/* One presentation state drives every device. No requests for business data. */
(()=>{
 'use strict';
 const menu=document.querySelector('.mobile-nav');
 if(menu){
  const summary=menu.querySelector('summary'),close=()=>{menu.open=false;summary.setAttribute('aria-expanded','false');};
  summary.setAttribute('aria-expanded',String(menu.open));
  menu.addEventListener('toggle',()=>summary.setAttribute('aria-expanded',String(menu.open)));
  menu.querySelectorAll('a').forEach(link=>link.addEventListener('click',close));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu.open){close();summary.focus();}});
  document.addEventListener('click',event=>{if(menu.open&&!menu.contains(event.target))close();});
  document.addEventListener('focusin',event=>{if(menu.open&&!menu.contains(event.target))close();});
  matchMedia('(min-width: 1201px)').addEventListener('change',event=>{if(event.matches)close();});
 }
 const ui=window.OxxomUI,t=source=>ui?.t(source)||source;
 const tabs=[...document.querySelectorAll('[data-demo-tab]')],panel=document.getElementById('device-demo-panel'),status=document.getElementById('demo-status');
 if(!panel||!tabs.length)return;
 const motion=matchMedia('(prefers-reduced-motion: reduce)');
 const states=ui?.landing?.states;
 if(!states)return;
 let number=new Intl.NumberFormat(ui?.language||'uk',{maximumFractionDigits:0});
 let frame=null;
 const displayed=new WeakMap();
 function format(value,currency){return(currency?'₴ ':'')+number.format(Math.round(value));}
 function setText(selector,text){document.querySelectorAll(selector).forEach(node=>ui?ui.text(node,text):node.textContent=text);}
 function animateValues(data,immediate=false){
  cancelAnimationFrame(frame);
  const values=[];
  document.querySelectorAll('[data-metric]').forEach(node=>{const i=Number(node.dataset.metric);values.push({node,value:data.values[i],money:data.money[i],from:displayed.get(node)??0});});
  document.querySelectorAll('[data-phone-value]').forEach(node=>values.push({node,value:data.values[0],money:data.money[0],from:displayed.get(node)??0}));
  const start=performance.now(),duration=motion.matches||immediate?0:1000;
  function draw(now){const progress=duration?Math.min(1,(now-start)/duration):1,ease=1-Math.pow(1-progress,3);values.forEach(v=>{const value=v.from+(v.value-v.from)*ease;v.node.textContent=format(value,v.money);displayed.set(v.node,value);});if(progress<1)frame=requestAnimationFrame(draw);}
  frame=requestAnimationFrame(draw);
 }
 function render(key,immediate=false){
  const data=states[key];panel.dataset.activeTab=key;panel.setAttribute('aria-labelledby','demo-tab-'+key);
  tabs.forEach(tab=>{const selected=tab.dataset.demoTab===key;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;});
  document.querySelectorAll('[data-device-app],[data-phone-app]').forEach(app=>app.dataset.view=key);
  document.querySelectorAll('[data-app-menu],[data-phone-menu]').forEach(item=>item.classList.toggle('active',(item.dataset.appMenu||item.dataset.phoneMenu)===key));
  setText('[data-app-title],[data-phone-title]',data.title);
  data.labels.forEach((label,i)=>{setText('[data-metric-label="'+i+'"]',label);setText('[data-metric-note="'+i+'"]',data.notes[i]);});
  setText('[data-phone-label]',data.labels[0]);setText('[data-chart-title]',data.chart);setText('[data-donut-title]',data.donut);setText('[data-legend]',data.legend);setText('[data-app-record]',data.record);setText('[data-floating-value]',data.floating);setText('[data-floating-label]',data.floatingLabel);setText('[data-floating-success]',data.success);
  document.querySelectorAll('[data-bars]').forEach(chart=>[...chart.children].forEach((bar,i)=>bar.style.setProperty('--bar',data.bars[i]+'%')));
  document.querySelectorAll('[data-donut]').forEach(donut=>donut.style.background='conic-gradient(#009c6c 0 '+data.share+'%,#79d9b8 '+data.share+'% 84%,#c9f0dd 84%)');
  animateValues(data,immediate);
 }
 function activate(key,manual=false){if(!states[key])return;render(key);if(manual)status.textContent=t('Демонстрація:')+' '+t(states[key].title);}
 tabs.forEach((tab,index)=>{tab.addEventListener('click',()=>activate(tab.dataset.demoTab,true));tab.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index-1+tabs.length)%tabs.length;if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;if(next!==undefined){event.preventDefault();tabs[next].focus();activate(tabs[next].dataset.demoTab,true);}});});
 motion.addEventListener('change',()=>{if(motion.matches){const current=panel.dataset.activeTab||'overview';render(current);}});
 document.addEventListener('oxxom:language-change',()=>{number=new Intl.NumberFormat(ui?.language||'uk',{maximumFractionDigits:0});render(panel.dataset.activeTab||'overview',true);if(status.textContent)status.textContent=t('Демонстрація:')+' '+t(states[panel.dataset.activeTab||'overview'].title);});
 render('overview');
})();
