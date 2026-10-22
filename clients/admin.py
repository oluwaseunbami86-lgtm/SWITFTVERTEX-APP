from django.contrib import admin

from .models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "client_type", "phone_number", "email", "created_at")
    list_filter = ("client_type",)
    search_fields = ("name", "email", "phone_number")
