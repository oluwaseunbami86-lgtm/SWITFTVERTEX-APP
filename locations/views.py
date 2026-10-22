from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.models import Profile
from accounts.permissions import SUPER_ADMIN, role_required
from .models import Location


@role_required(SUPER_ADMIN, Profile.Role.ADMIN, Profile.Role.OFFICE_STAFF, Profile.Role.SURVEYOR)
def location_list(request):
    locations = Location.objects.select_related("state").all()
    return render(request, "locations/location_list.html", {"locations": locations})
