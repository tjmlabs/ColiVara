from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _


class Team(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        "CustomUser", related_name="owned_teams", on_delete=models.CASCADE
    )

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    TIER = (
        ("individual", "Individual"),
        ("team", "Team"),
    )
    subscribe_to_emails = models.BooleanField(default=True)
    tier = models.CharField(max_length=50, choices=TIER, default="individual")
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    token = models.CharField(max_length=255, blank=True)
    consumed_credits = models.IntegerField(default=0)
    available_credits = models.IntegerField(default=1000)  # free 1000 credits on signup
    team = models.ForeignKey(
        Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="members"
    )

    def __str__(self) -> str:
        return self.email

    # create token on user creation
    def generate_token(self, email=None) -> str:
        if not self.token:
            self.token = get_random_string(length=32)
            self.save()
        return self.token

    def save(self, *args, **kwargs) -> None:
        if not self.token:
            self.generate_token()
        super().save(*args, **kwargs)

    def get_available_credits(self):
        if self.team:
            return self.team.owner.available_credits
        return self.available_credits

    def get_consumed_credits(self):
        if self.team:
            return self.team.owner.consumed_credits
        return self.consumed_credits

    def set_available_credits(self, credits):
        if self.team:
            self.team.owner.available_credits = credits
            self.team.owner.save()
        else:
            self.available_credits = credits
            self.save()

    def set_consumed_credits(self, credits):
        if self.team:
            self.team.owner.consumed_credits = credits
            self.team.owner.save()
        else:
            self.consumed_credits = credits
            self.save()
