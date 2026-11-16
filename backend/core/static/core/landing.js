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
 const states={
  overview:{title:'Огляд бізнесу',labels:['Виручка','Валовий результат','Документи'],values:[2480000,864000,1286],money:[true,true,false],notes:['За обраний період','Ілюстративний приклад','У робочому просторі'],bars:[32,49,39,66,57,81,71,94],chart:'Динаміка продажів',donut:'Структура доходу',share:64,legend:'Товари та послуги',record:'Документи та розрахунки — поруч',floating:'↗ 5 розділів',floatingLabel:'Спільний робочий простір',success:'Операцію успішно проведено'},
  finance:{title:'Рух грошей',labels:['Надходження','Виплати','Залишок коштів'],values:[2480000,1616000,864000],money:[true,true,true],notes:['За обраний період','Внутрішній облік','Каси та рахунки'],bars:[40,55,37,72,64,81,68,91],chart:'Рух коштів',donut:'Надходження й виплати',share:61,legend:'Розподіл платежів',record:'Платіж пов’язано з документом',floating:'₴ Оплати',floatingLabel:'Розрахунки під контролем',success:'Платіж збережено та проведено'},
  stock:{title:'Залишки на складі',labels:['Вартість товарів','Товарів у наявності','Складські документи'],values:[485000,248,36],money:[true,false,false],notes:['Ілюстративна оцінка','Одиниць товару','За обраний період'],bars:[83,64,74,52,67,40,62,55],chart:'Товарні рухи',donut:'Залишки за групами',share:56,legend:'Наявні товари',record:'Залишки оновлено після проведення',floating:'▣ Склад',floatingLabel:'Знайте, що є в наявності',success:'Товарне надходження проведено'},
  documents:{title:'Документи',labels:['Сума реалізацій','Проведено','Чернетки'],values:[2480000,1286,12],money:[true,false,false],notes:['За обраний період','Документів','Очікують перевірки'],bars:[31,45,57,48,69,62,81,93],chart:'Операції за період',donut:'Стан документів',share:78,legend:'Проведені документи',record:'Рахунок, накладна та акт — поруч',floating:'▤ Документи',floatingLabel:'Від чернетки до результату',success:'Документ готовий до друку'},
  analytics:{title:'Аналітика',labels:['Продажі','Валовий результат','Реалізації'],values:[2480000,864000,1286],money:[true,true,false],notes:['За обраний період','Попередній показник','Ілюстративний приклад'],bars:[22,35,43,38,61,73,68,96],chart:'Динаміка показників',donut:'Продажі за групами',share:68,legend:'Управлінський огляд',record:'Звіт можна експортувати в CSV',floating:'↗ Аналітика',floatingLabel:'Від цифр до рішень',success:'Показники оновлено'}
 };
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
