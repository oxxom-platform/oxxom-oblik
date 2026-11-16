from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from documents.services import Conflict
from .models import IssuerProfile,ProfileEvent

FIELDS=('legal_name','kind','code','address','bank_details','email','phone','signatory')


class ProfileForm(forms.ModelForm):
    class Meta:
        model=IssuerProfile
        fields=FIELDS
        labels={"legal_name":"Повна назва", "kind":"Тип", "code":"Код", "address":"Адреса", "bank_details":"Банківські реквізити", "email":"Email", "phone":"Телефон", "signatory":"Підписант"}


def snapshot(organization):
    profile=IssuerProfile.objects.filter(organization=organization).first()
    return {**{field:getattr(profile,field) for field in FIELDS},'profile_version':profile.version} if profile else {}


def get_profile(organization):
    data=snapshot(organization)
    return {'profile':{field:data.get(field,'company' if field=='kind' else organization.name if field=='legal_name' else '') for field in FIELDS},'version':data.get('profile_version',0),'configured':bool(data)}


def save_profile(organization,user,data):
    if set(data)-set(FIELDS)-{'version'}:raise ValidationError('Невідомі поля реквізитів.')
    existing=IssuerProfile.objects.filter(organization=organization).first()
    version=existing.version if existing else 0
    if type(data.get('version')) is not int or data['version']!=version:raise Conflict('Реквізити змінено. Оновіть сторінку перед збереженням.')
    before=snapshot(organization)
    form=ProfileForm(data,instance=existing or IssuerProfile(organization=organization))
    if not form.is_valid():raise ValidationError([f'{form.fields[field].label}: {message}' for field,messages in form.errors.items() for message in messages])
    profile=form.save(commit=False);profile.version=version+1;profile.updated_at=timezone.now();profile.save()
    ProfileEvent.objects.create(organization=organization,actor=user,version=profile.version,before=before,after=snapshot(organization))
    return get_profile(organization)
