from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Profile


@receiver(post_save, sender=get_user_model())
def create_profile_for_user(sender, instance, created, **kwargs):
    if created:
        status = Profile.Status.APPROVED if instance.is_superuser else Profile.Status.PENDING
        Profile.objects.create(
            user=instance,
            role=Profile.Role.CLIENT_VIEWER,
            status=status,
            approved_by=instance if instance.is_superuser else None,
            approved_at=timezone.now() if instance.is_superuser else None,
        )
