import csv
import hashlib
import io
import json
import re
import unicodedata
from django.core.exceptions import ValidationError
from django.utils import timezone
from catalog.forms import FORMS,MODELS
from catalog.models import Unit,CatalogEvent
from documents.services import Conflict
from .models import ImportBatch,ImportEvent,preview_expiry

MAX_BYTES=1024*1024
MAX_ROWS=1000
SCHEMAS={
 'units':{'title':'Одиниці виміру','fields':['name','symbol'],'example':['Штука','шт'],'note':'name — назва; symbol — позначення. Дубль визначається за позначенням, включно з архівними записами.'},
 'warehouses':{'title':'Склади','fields':['name'],'example':['Основний склад'],'note':'name — назва. Дубль визначається за назвою, включно з архівними складами.'},
 'products':{'title':'Товари та послуги','fields':['name','kind','sku','unit','price'],'example':['Приклад товару','goods','ART-001','шт','100.00'],'note':'kind: goods — товар, service — послуга. unit — позначення вже створеної активної одиниці (наприклад, шт). price — ціна продажу, не закупівельна. Із артикулом дубль визначається за артикулом; без артикула — за назвою та типом.'},
 'counterparties':{'title':'Контрагенти','fields':['name','kind','code','email','phone','address','bank_details','note'],'example':['Приклад контрагента','company','','','','','',''],'note':'kind: company — юридична особа, sole_trader — ФОП, person — фізична особа. Із кодом дубль визначається за кодом; без коду — за назвою та типом. Початкові нулі в кодах зберігаються.'},
}


def schema(kind):
    if kind not in SCHEMAS:raise ValidationError('Невідомий довідник імпорту.')
    return SCHEMAS[kind]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())


def identity(kind,data):
    if kind=='units':return ('symbol',normalized(data['symbol']))
    if kind=='warehouses':return ('name',normalized(data['name']))
    field='sku' if kind=='products' else 'code'
    if data.get(field):return (field,normalized(data[field]))
    return ('name',data['kind'],normalized(data['name']))


def parse_upload(upload,kind):
    expected=schema(kind)['fields']
    if upload.size>MAX_BYTES:raise ValidationError('Файл завеликий. Ліміт — 1 МБ.')
    content=upload.read(MAX_BYTES+1)
    if len(content)>MAX_BYTES:raise ValidationError('Файл завеликий. Ліміт — 1 МБ.')
    try:text=content.decode('utf-8-sig')
    except UnicodeDecodeError:raise ValidationError('Потрібен CSV у кодуванні UTF-8. Перезбережіть файл у CSV UTF-8.') from None
    if '\x00' in text:raise ValidationError('Файл містить нульові байти. Потрібен текстовий CSV UTF-8.')
    delimiter=None
    for candidate in (';',',','\t'):
        try:header=next(csv.reader(io.StringIO(text,newline=''),delimiter=candidate,strict=True))
        except (StopIteration,csv.Error):continue
        header=[cell.strip().lower() for cell in header]
        if len(header)==len(expected) and len(set(header))==len(header) and set(header)==set(expected):delimiter=candidate;break
    if delimiter is None:raise ValidationError('Заголовок не відповідає шаблону: '+', '.join(expected)+'. Завантажте шаблон цього довідника.')
    reader=csv.reader(io.StringIO(text,newline=''),delimiter=delimiter,strict=True)
    next(reader)
    rows=[]
    try:
        while True:
            line=reader.line_num+1
            try:values=next(reader)
            except StopIteration:break
            if not values or not any(cell.strip() for cell in values):continue
            if len(rows)>=MAX_ROWS:raise ValidationError('Ліміт — 1000 записів у файлі. Розділіть імпорт на кілька файлів.')
            if len(values)!=len(header):raise ValidationError(f'CSV-рядок {line}: кількість колонок не відповідає заголовку.')
            rows.append({'line':line,'data':dict(zip(header,[cell.strip() for cell in values]))})
    except csv.Error:raise ValidationError(f'Помилка структури CSV біля рядка {reader.line_num}. Перевірте лапки й роздільники.') from None
    if not rows:raise ValidationError('У файлі немає записів. Шаблон містить лише заголовок; додайте власні дані.')
    return rows,hashlib.sha256(content).hexdigest()


def existing_index(org,kind):
    index={}
    for obj in MODELS[kind].objects.filter(organization=org).order_by('id'):
        data={field:getattr(obj,field) for field in schema(kind)['fields'] if field!='unit'}
        key=identity(kind,data)
        index.setdefault(key,[]).append(obj)
        # A missing SKU/code must still detect an existing name/type record.
        if kind in ('products','counterparties'):
            fallback=('name',obj.kind,normalized(obj.name))
            if key!=fallback:index.setdefault(fallback,[]).append(obj)
    return index


def analyze(org,kind,inputs):
    index=existing_index(org,kind)
    unit_index={}
    if kind=='products':
        for unit in Unit.objects.filter(organization=org,is_active=True):unit_index.setdefault(normalized(unit.symbol),[]).append(unit)
    rows=[];forms_by_line={};seen={}
    for item in inputs:
        line=item['line'];raw=item['data'];data=dict(raw);errors=[];reference=None
        if kind=='products':
            units=unit_index.get(normalized(data['unit']),[])
            if len(units)!=1:
                errors.append('Одиниця не знайдена або позначення неоднозначне. Спочатку створіть/уточніть активну одиницю у своїй організації.')
                data['unit']=''
            else:
                unit=units[0];data['unit']=str(unit.pk);reference={'id':str(unit.pk),'version':unit.version,'symbol':unit.symbol}
            price=data['price'].replace('\u00a0','').replace('\u202f','').replace(' ','')
            if not re.fullmatch(r'\d+(?:[.,]\d{1,2})?',price):errors.append('Ціна має бути невід’ємним числом до 2 знаків після крапки або коми.')
            else:data['price']=price.replace(',','.')
        form=FORMS[kind](data,instance=MODELS[kind](organization=org),**({'organization':org} if kind=='products' else {}))
        if not form.is_valid():errors.extend(f'{field}: {message}' for field,messages in form.errors.items() for message in messages)
        row={'line':line,'input':raw,'status':'error' if errors else 'new','errors':errors,'data':{},'reference':reference,'existing':[]}
        if not errors:
            cleaned={field:(str(value.pk) if field=='unit' else format(value,'.2f') if field=='price' else value) for field,value in form.cleaned_data.items()}
            row['data']=cleaned
            key=identity(kind,cleaned)
            if key in seen:
                previous=seen[key]
                if previous['data']!=cleaned:
                    row['status']='error';row['errors']=[f'Той самий ключ уже є у CSV-рядку {previous["line"]}, але дані відрізняються. Уточніть файл.']
                else:
                    row['status']='duplicate';row['errors']=[f'Точний повтор CSV-рядка {previous["line"]}; буде пропущено.']
            elif key in index:
                row['status']='duplicate'
                row['existing']=[{'id':str(obj.pk),'name':obj.name,'version':obj.version,'is_active':obj.is_active} for obj in index[key]]
                row['errors']=['Запис уже є у довіднику (можливо, в архіві). Рядок буде пропущено; наявні дані не змінюються.']
            seen.setdefault(key,row)
            if row['status']=='new':forms_by_line[line]=form
        rows.append(row)
    counts={status:sum(row['status']==status for row in rows) for status in ('new','duplicate','error')}
    return {'rows':rows,'counts':counts},forms_by_line


def event(batch,user,action,details):
    ImportEvent.objects.create(batch=batch,organization=batch.organization,actor=user,action=action,details=details)


def make_preview(org,user,kind,upload):
    inputs,file_digest=parse_upload(upload,kind)
    plan,_=analyze(org,kind,inputs)
    filename=upload.name.replace('\\','/').rsplit('/',1)[-1][:200]
    batch=ImportBatch.objects.create(organization=org,actor=user,kind=kind,filename=filename,file_digest=file_digest,inputs=inputs,plan=plan,counts=plan['counts'],plan_digest=digest(plan))
    event(batch,user,'preview',{'counts':plan['counts'],'version':batch.version})
    return batch


def check_version(batch,data):
    if set(data)!={'version'} or type(data.get('version')) is not int:raise ValidationError('Потрібна лише актуальна версія перегляду.')
    if data['version']!=batch.version:raise Conflict('Перегляд змінено. Завантажте актуальну версію.')


def refresh(batch,user,data):
    check_version(batch,data)
    if batch.status!='preview':raise Conflict('Імпорт уже виконано. Нове завантаження потребує нового перегляду.')
    plan,_=analyze(batch.organization,batch.kind,batch.inputs)
    batch.plan=plan;batch.counts=plan['counts'];batch.plan_digest=digest(plan);batch.version+=1;batch.expires_at=preview_expiry();batch.save(update_fields=['plan','counts','plan_digest','version','expires_at'])
    event(batch,user,'refresh',{'counts':plan['counts'],'version':batch.version})


def confirm(batch,user,data):
    if batch.status=='applied':
        if set(data)=={'version'} and type(data['version']) is int and data['version']==batch.result['confirmed_version']:return batch.result
        raise Conflict('Імпорт уже виконано. Цей перегляд не можна використати для нового імпорту.')
    check_version(batch,data)
    if batch.expires_at<=timezone.now():raise Conflict('Перегляд прострочено. Оновіть перевірку перед підтвердженням.')
    if batch.plan['counts']['error']:raise ValidationError('Є помилки в CSV. Виправте файл та завантажте знову.')
    if not batch.plan['counts']['new']:raise Conflict('Нових записів немає. Усі рядки вже є у довіднику або повторюються.')
    current,forms_by_line=analyze(batch.organization,batch.kind,batch.inputs)
    if digest(current)!=batch.plan_digest:raise Conflict('Довідники змінилися після перегляду. Оновіть перевірку та перегляньте результат ще раз.')
    created=[]
    for row in current['rows']:
        if row['status']!='new':continue
        record=forms_by_line[row['line']].save(commit=False);record.updated_at=timezone.now();record.save()
        CatalogEvent.objects.create(organization=batch.organization,actor=user,kind=batch.kind,object_id=record.pk,action='import',version=record.version)
        created.append({'line':row['line'],'id':str(record.pk)})
    batch.result={'id':str(batch.pk),'status':'applied','confirmed_version':batch.version,'version':batch.version+1,'created':len(created),'skipped':current['counts']['duplicate'],'records':created}
    batch.version+=1;batch.status='applied';batch.applied_at=timezone.now();batch.save(update_fields=['version','status','applied_at','result'])
    event(batch,user,'confirm',{'created':len(created),'skipped':current['counts']['duplicate'],'version':batch.version})
    return batch.result
