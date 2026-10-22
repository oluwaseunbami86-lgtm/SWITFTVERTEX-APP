from django.contrib import admin

from .models import Location, State


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "created_at")
    list_filter = ("state",)
    search_fields = ("name", "city", "address")
