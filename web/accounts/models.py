import datetime

import stripe
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.crypto import get_random_string


class Team(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        "CustomUser", related_name="owned_teams", on_delete=models.CASCADE
    )

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    TIER = (
        ("free", "Free"),
        ("individual", "Individual"),
        ("team", "Team"),
    )
    subscribe_to_emails = models.BooleanField(default=True)
    tier = models.CharField(max_length=50, choices=TIER, default="free")
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    token = models.CharField(max_length=255, blank=True)
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

    def set_available_credits(self, credits):
        if self.team:
            self.team.owner.available_credits = credits
            self.team.owner.save()
        else:
            self.available_credits = credits
            self.save()

    def record_consumed_credits(self, credits):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer_id = self.stripe_customer_id
        # customer_id = "cus_R8JkMI5v4oBQiB"  # for testing

        if self.tier == "team":
            customer_id = self.team.owner.stripe_customer_id

        if not customer_id:
            return

        stripe.billing.MeterEvent.create(
            event_name=settings.STRIPE_METER_EVENT,
            payload={"value": credits, "stripe_customer_id": customer_id},
        )

    def get_credit_usage(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer_id = self.stripe_customer_id
        if self.tier == "team":
            customer_id = self.team.owner.stripe_customer_id

        if not customer_id:
            return []

        now = datetime.datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_month = now - datetime.timedelta(days=30)

        usage = stripe.billing.Meter.list_event_summaries(
            settings.STRIPE_METER_ID,
            customer=customer_id,
            start_time=int(last_month.timestamp()),
            end_time=int(now.timestamp()),
            value_grouping_window="day",
        )
        results = usage.data
        return results
