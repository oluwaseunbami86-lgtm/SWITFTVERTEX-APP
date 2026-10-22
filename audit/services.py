def record_action(user, method, path):
    """Best-effort audit log write - never raises, so it can't break a request."""
    from .models import AuditLog

    try:
        AuditLog.objects.create(user=user, method=method, path=path)
    except Exception:
        pass


def _client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def log_event(*, user, action, entity_type, entity_id="", entity_label="", old=None, new=None,
              description="", request=None):
    """Write an AuditEvent. Unlike record_action this is NOT best-effort: call it
    inside the same transaction as the change it describes, so if the audit row
    cannot be written the change is rolled back rather than going unrecorded."""
    from .models import AuditEvent

    return AuditEvent.objects.create(
        user_id_snapshot=getattr(user, "pk", None),
        username=getattr(user, "username", "") or "",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        entity_label=entity_label[:255],
        old_value=old,
        new_value=new,
        description=description,
        ip_address=_client_ip(request),
    )
