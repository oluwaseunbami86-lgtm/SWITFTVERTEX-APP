from django import template

from accounts import permissions as p

register = template.Library()

_CHECKS = {
    "create": lambda u, o: p.can_create(u),
    "edit_job": p.can_edit_job,
    "edit_content": p.can_edit_content,
    "edit_client": p.can_edit_client,
    "archive": lambda u, o: p.can_archive(u),
    "restore": lambda u, o: p.can_restore(u),
    "delete": lambda u, o: p.can_delete(u),
}


@register.simple_tag
def can(user, action, obj=None):
    """{% can request.user 'edit_job' job as ok %}"""
    check = _CHECKS.get(action)
    return bool(check and check(user, obj))


@register.filter
def json_script_data(value):
    """Dict -> JSON that is safe to embed inside an inline <script> block."""
    import json
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
