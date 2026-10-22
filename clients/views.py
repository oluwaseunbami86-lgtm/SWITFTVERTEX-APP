from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render

from accounts import permissions as perm
from audit.models import AuditEvent
from audit.services import log_event
from .forms import ClientForm
from .models import Client


@login_required
def client_list(request):
    perm.require(perm.can_view_client_list(request.user))
    query = request.GET.get("q", "")
    clients = Client.objects.all()
    if query:
        clients = clients.filter(name__icontains=query)
    return render(request, "clients/client_list.html", {"clients": clients, "query": query})


@login_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    perm.require(perm.can_view_client(request.user, client))
    jobs = client.jobs.all().order_by("-created_at")
    jobs = [job for job in jobs if perm.can_view_job(request.user, job)]
    return render(request, "clients/client_detail.html", {"client": client, "jobs": jobs})


@login_required
def client_create(request):
    perm.require(perm.can_create(request.user))
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.created_by = request.user
            client.save()
            messages.success(request, f"Client '{client.name}' added.")
            return redirect("clients:detail", pk=client.pk)
    else:
        form = ClientForm()
    return render(request, "clients/client_form.html", {"form": form, "title": "New client"})


@login_required
def client_update(request, pk):
    client = get_object_or_404(Client, pk=pk)
    perm.require(perm.can_edit_client(request.user, client))
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, "Client updated.")
            return redirect("clients:detail", pk=client.pk)
    else:
        form = ClientForm(instance=client)
    return render(request, "clients/client_form.html", {"form": form, "title": f"Edit {client.name}"})


@login_required
def client_delete(request, pk):
    client = get_object_or_404(Client, pk=pk)
    perm.require(perm.can_delete(request.user) and perm.can_edit_client(request.user, client))
    if request.method == "POST":
        name = client.name
        try:
            with transaction.atomic():
                client.delete()
                log_event(
                    user=request.user,
                    action=AuditEvent.Action.CLIENT_DELETED,
                    entity_type="client",
                    entity_id=client.pk,
                    entity_label=name,
                    description=f"Deleted client {name}",
                    request=request,
                )
        except ProtectedError:
            messages.error(request, "CLIENT CANNOT BE DELETED WHILE JOBS ARE LINKED TO IT.")
            return redirect("clients:list")
        messages.success(request, f"Client '{name}' deleted.")
        return redirect("clients:list")
    return render(request, "clients/client_confirm_delete.html", {"client": client})
