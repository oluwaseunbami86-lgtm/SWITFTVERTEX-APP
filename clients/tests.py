from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile

from .models import Client


class ClientTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="office1", password="test")
        self.user.profile.role = Profile.Role.OFFICE_STAFF
        self.user.profile.status = Profile.Status.APPROVED
        self.user.profile.save()
        self.client.login(username="office1", password="test")

    def test_create_client(self):
        response = self.client.post(reverse("clients:create"), {
            "name": "Acme Estates",
            "client_type": "company",
            "email": "info@acme.com",
            "phone_number": "08000000000",
            "address": "",
            "notes": "",
        })
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_client_list_view(self):
        Client.objects.create(name="Test Client", created_by=self.user)
        response = self.client.get(reverse("clients:list"))
        self.assertContains(response, "Test Client")
