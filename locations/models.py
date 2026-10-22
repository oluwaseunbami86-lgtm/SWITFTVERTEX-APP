from django.db import models


class State(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(models.Model):
    """A site/area a job takes place at - town, address, and coordinates."""

    name = models.CharField(max_length=255, help_text="e.g. Lekki Phase 2, Plot 14")
    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="locations")
    city = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name}, {self.city or self.state}"
