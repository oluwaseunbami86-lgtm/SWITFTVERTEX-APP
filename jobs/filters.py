import django_filters
from django.db.models import Q

from clients.models import Client

from .models import Job


class JobFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search", label="Search")
    plan_number = django_filters.CharFilter(
        field_name="plan_number", lookup_expr="icontains", label="Plan number"
    )
    client = django_filters.ModelChoiceFilter(
        queryset=Client.objects.order_by("name"), label="Client"
    )

    class Meta:
        model = Job
        fields = ["status", "survey_type", "job_type", "client", "assigned_to"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(title__icontains=value)
            | Q(plan_number__icontains=value)
            | Q(job_address__icontains=value)
            | Q(client__name__icontains=value)
        )
