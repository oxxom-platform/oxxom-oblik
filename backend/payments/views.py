from decimal import Decimal
import hashlib
import json
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from accounts.access import require_permission, memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected
from accounts.models import Organization
from documents.models import Document
from documents.views import body
from documents.services import Conflict, uuid
from inventory.models import StockDraft
from posting.models import RequestRecord
from .models import MoneyAccount, Payment, Allocation, MoneyMovement, PaymentEvent
from .services import write_draft, post_payment, reverse_payment, replace_allocations, check_version, allocations_data, balance, settlement, cents, total, event


def serialize(payment, detail=False):
    allocated = sum((a.amount for a in payment.allocations.all()), start=Decimal('0.00'))
    data = {'id':str(payment.pk),'number':payment.display_number,'direction':payment.direction,'date':payment.date.isoformat(),'account':str(payment.account_id),'account_name':payment.account_name,'account_details':payment.account_details,'counterparty':str(payment.counterparty_id),'counterparty_snapshot':payment.counterparty_snapshot,'amount':str(payment.amount),'allocated':str(allocated),'advance':str(payment.amount-allocated),'purpose':payment.purpose,'status':payment.status,'version':payment.version,'is_active':payment.is_active}
    if detail:
        data['allocations'] = [{**row,'number':a.sale.display_number if a.sale_id else a.receipt.display_number} for row,a in zip(allocations_data(payment),payment.allocations.all())]
    return data


def account_data(account):
    return {'id':str(account.pk),'name':account.name,'kind':account.kind,'details':account.details,'is_active':account.is_active,'version':account.version,'balance':str(balance(account))}


def page_number(request):
    try: return max(1,min(int(request.GET.get('page','1')),10000))
    except ValueError: raise ValidationError('Невірна сторінка.') from None


@protected
def _page(request): return render(request,'payments/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def permissions(request,organization_id):
    member=memberships_for(request.user).filter(organization_id=organization_id).first()
    allowed=bool(member and 'payments.manage' in ROLE_PERMISSIONS.get(member.role,()))
    return JsonResponse({'read':allowed,'write':allowed})


def read_api(handler):
    # All financial endpoints require the same explicit organization permission.
    @require_GET
    @protected
    def wrapped(request,organization_id,**kwargs):
        try:
            require_permission(request.user,organization_id,'payments.manage')
            return handler(request,organization_id,**kwargs)
        except PermissionDenied: return JsonResponse({'error':'Фінанси доступні власнику та бухгалтеру.'},status=403)
        except ValidationError as exc: return JsonResponse({'error':' '.join(exc.messages)},status=400)
    return wrapped


@read_api
def accounts(request,organization_id):
    query=MoneyAccount.objects.filter(organization_id=organization_id).order_by('name','id')
    state=request.GET.get('state','active')
    if state not in ('active','archived','all'): raise ValidationError('Невірний стан рахунків.')
    if state!='all': query=query.filter(is_active=state=='active')
    page=page_number(request)
    return JsonResponse({'items':[account_data(a) for a in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@read_api
def listing(request,organization_id):
    query=Payment.objects.filter(organization_id=organization_id).prefetch_related('allocations').order_by('-date','-created_at','-id')
    status=request.GET.get('status','active')
    if status not in ('active','archived','all'): raise ValidationError('Невірний стан платежів.')
    if status!='all': query=query.filter(is_active=status=='active')
    search=request.GET.get('q','').strip()[:100]
    if search: query=query.filter(Q(purpose__icontains=search)|Q(counterparty_snapshot__name__icontains=search))
    page=page_number(request)
    return JsonResponse({'items':[serialize(p) for p in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@read_api
def detail(request,organization_id,payment_id):
    return JsonResponse({'payment':serialize(get_object_or_404(Payment,organization_id=organization_id,pk=payment_id),True)})


@read_api
def documents(request,organization_id):
    direction=request.GET.get('direction')
    if direction not in ('in','out'): raise ValidationError('Оберіть напрямок платежу.')
    model=Document if direction=='in' else StockDraft
    cp=uuid(request.GET.get('counterparty'))
    query=model.objects.filter(organization_id=organization_id,counterparty_id=cp,status='posted',is_active=True).order_by('-date','-created_at','-id')
    if direction=='out': query=query.filter(kind='receipt',total__isnull=False)
    payment=None
    if request.GET.get('payment'):
        payment=get_object_or_404(Payment,organization_id=organization_id,pk=uuid(request.GET['payment']))
        if payment.counterparty_id!=cp or payment.direction!=direction: payment=None
    page=page_number(request); items=[]
    for doc in query[(page-1)*50:page*50]:
        status=settlement(doc)
        # Existing allocation can be retained when changing a posted distribution.
        current=total(payment.allocations.filter(**{'sale' if direction=='in' else 'receipt':doc})) if payment else Decimal('0.00')
        credit=current if payment and payment.status=='posted' else Decimal('0.00')
        items.append({'id':str(doc.pk),'source':'sale' if direction=='in' else 'receipt','number':doc.display_number,'date':doc.date.isoformat(),**status,'available':str(cents(doc.total)-Decimal(status['paid'])+credit)})
    return JsonResponse({'items':items,'total':query.count(),'page':page})


@read_api
def debts(request,organization_id):
    direction=request.GET.get('direction','in')
    if direction not in ('in','out'): raise ValidationError('Невірний напрямок.')
    model=Document if direction=='in' else StockDraft
    query=model.objects.filter(organization_id=organization_id,status='posted').select_related('counterparty').order_by('-date','-id')
    if direction=='out': query=query.filter(kind='receipt',total__isnull=False)
    if request.GET.get('counterparty'): query=query.filter(counterparty_id=uuid(request.GET['counterparty']))
    page=page_number(request);items=[]
    for doc in query[(page-1)*50:page*50]:
        items.append({'id':str(doc.pk),'source':'sale' if direction=='in' else 'receipt','number':doc.display_number,'date':doc.date.isoformat(),'counterparty':str(doc.counterparty_id),'counterparty_name':doc.counterparty_snapshot.get('name',''),**settlement(doc)})
    return JsonResponse({'items':items,'total':query.count(),'page':page})


@read_api
def journal(request,organization_id):
    query=MoneyMovement.objects.filter(organization_id=organization_id).select_related('payment').order_by('-id')
    if request.GET.get('account'): query=query.filter(account_id=uuid(request.GET['account']))
    page=page_number(request)
    return JsonResponse({'items':[{'id':m.pk,'date':m.date.isoformat(),'account_name':m.account_name,'payment':str(m.payment_id),'number':m.payment.display_number,'amount':str(m.amount),'is_reversal':m.is_reversal} for m in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@read_api
def audit(request,organization_id):
    query=PaymentEvent.objects.filter(organization_id=organization_id).order_by('-id')
    if request.GET.get('payment'): query=query.filter(object_id=uuid(request.GET['payment']))
    page=page_number(request)
    return JsonResponse({'items':[{'action':e.action,'actor':str(e.actor_id),'object':str(e.object_id),'details':e.details,'created_at':e.created_at.isoformat()} for e in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@require_POST
@protected
def save_account(request,organization_id,account_id=None):
    try:
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'payments.manage')
            data=body(request)
            account=get_object_or_404(MoneyAccount,pk=account_id,organization=org) if account_id else MoneyAccount(organization=org)
            if account_id: check_version(account,data)
            name=forms.CharField(max_length=200).clean(data.get('name'))
            kind=data.get('kind')
            if kind not in ('cash','bank'): raise ValidationError('Оберіть тип: каса чи банк.')
            if account_id and kind!=account.kind and Payment.objects.filter(account=account).exists():
                raise Conflict('Тип рахунку з платежами не змінюється. Створіть окремий рахунок.')
            account.name, account.kind=name,kind
            account.details=forms.CharField(max_length=200,required=False).clean(data.get('details',''))
            if type(data.get('is_active',True)) is not bool: raise ValidationError('Невірний стан.')
            account.is_active=data.get('is_active',True)
            if account_id: account.version+=1
            account.updated_at=timezone.now();account.full_clean();account.save()
            event(org,request.user,account,'account.save',{'name':name,'kind':kind,'is_active':account.is_active,'version':account.version})
            return JsonResponse({'item':account_data(account)},status=200 if account_id else 201)
    except PermissionDenied: return JsonResponse({'error':'Недостатньо прав.'},status=403)
    except ValidationError as exc: return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc: return JsonResponse({'error':str(exc)},status=409)


def mutate(request,organization_id,payment_id=None,action='save'):
    try:
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'payments.manage')
            data=body(request)
            transactional=action in ('save-and-post','post','reverse','allocate')
            if transactional:
                key=uuid(data.get('idempotency_key'))
                digest=hashlib.sha256(json.dumps({'action':'payment.'+action,'id':str(payment_id) if payment_id else None,'actor':str(request.user.pk),'body':data},sort_keys=True,separators=(',',':')).encode()).hexdigest()
                old=RequestRecord.objects.filter(organization=org,key=key).first()
                if old:
                    if old.digest!=digest: raise Conflict('Ключ повтору вже використано з іншими даними.')
                    return JsonResponse(old.response)
            payment=get_object_or_404(Payment.objects.select_for_update(),organization=org,pk=payment_id) if payment_id else None
            if action in ('save','save-and-post'):
                payload=data.get('payment') if action=='save-and-post' else data
                payment=write_draft(org,request.user,payload,payment)
                if action=='save-and-post': post_payment(org,request.user,payment)
            elif action=='allocate': replace_allocations(org,request.user,payment,data)
            elif action=='state':
                check_version(payment,data)
                if payment.status!='draft' or type(data.get('is_active')) is not bool: raise ValidationError('Архівувати можна лише чернетку.')
                payment.is_active=data['is_active'];payment.version+=1;payment.updated_at=timezone.now();payment.save(update_fields=['is_active','version','updated_at'])
                event(org,request.user,payment,'state',{'is_active':payment.is_active,'version':payment.version})
            else:
                check_version(payment,data)
                (post_payment if action=='post' else reverse_payment)(org,request.user,payment)
            response={'payment':serialize(payment,True)}
            if transactional: RequestRecord.objects.create(organization=org,key=key,digest=digest,response=response)
            return JsonResponse(response,status=201 if action=='save' and payment_id is None else 200)
    except PermissionDenied: return JsonResponse({'error':'Оплати доступні власнику та бухгалтеру.'},status=403)
    except ValidationError as exc: return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc: return JsonResponse({'error':str(exc)},status=409)


@require_POST
@protected
def add(request,organization_id): return mutate(request,organization_id)


@require_POST
@protected
def update(request,organization_id,payment_id): return mutate(request,organization_id,payment_id)


@require_POST
@protected
def save_post(request,organization_id,payment_id=None): return mutate(request,organization_id,payment_id,'save-and-post')


@require_POST
@protected
def post(request,organization_id,payment_id): return mutate(request,organization_id,payment_id,'post')


@require_POST
@protected
def reverse(request,organization_id,payment_id): return mutate(request,organization_id,payment_id,'reverse')


@require_POST
@protected
def allocate(request,organization_id,payment_id): return mutate(request,organization_id,payment_id,'allocate')


@require_POST
@protected
def state(request,organization_id,payment_id): return mutate(request,organization_id,payment_id,'state')
