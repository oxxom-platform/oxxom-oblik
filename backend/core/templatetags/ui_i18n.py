from django import template
from core.i18n import translate

register = template.Library()


@register.simple_tag(takes_context=True)
def ui_text(context, source):
    return translate(source, context.get('ui_language', 'uk'))
