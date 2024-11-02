import datetime
import logging

import sentry_sdk
import stripe
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.utils.crypto import get_random_string

logger = logging.getLogger(__name__)


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

        try:
            stripe.billing.MeterEvent.create(
                event_name=settings.STRIPE_METER_EVENT,
                payload={"value": credits, "stripe_customer_id": customer_id},
            )
        except Exception as e:
            if settings.SENTRY_DSN:
                sentry_sdk.capture_exception(e)
                sentry_sdk.set_tag("stripe_error", "meter_event")
                sentry_sdk.set_extra(
                    "user",
                    {
                        "email": self.email,
                        "tier": self.tier,
                        "stripe_customer_id": customer_id,
                    },
                )
                sentry_sdk.capture_message(
                    f"Error recording consumed credits for {self.email}", "fatal"
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
        try:
            usage = stripe.billing.Meter.list_event_summaries(
                settings.STRIPE_METER_ID,
                customer=customer_id,
                start_time=int(last_month.timestamp()),
                end_time=int(now.timestamp()),
                value_grouping_window="day",
            )
            results = usage.data
        except Exception as e:
            results = []
            if settings.SENTRY_DSN:
                sentry_sdk.capture_exception(e)
                sentry_sdk.set_tag("stripe_error", "meter_list")
                sentry_sdk.set_extra(
                    "user",
                    {
                        "email": self.email,
                        "tier": self.tier,
                        "stripe_customer_id": customer_id,
                    },
                )
                sentry_sdk.capture_message(
                    f"Error listing credit usage for {self.email}", "fatal"
                )
        return results

    def cancel_sub(self):
        try:
            with transaction.atomic():
                if self.tier == "team" and self.owned_teams.exists():
                    team = self.owned_teams.first()
                    team_members = team.members.all()

                    # Convert all team members to free tier and notify them
                    for member in team_members:
                        member.tier = "free"
                        member.team = None
                        member.save()

                        # Send notification to member
                        self.notify_member(member, "cancel")

                    # Delete the team
                    team.delete()

                self.stripe_subscription_id = ""
                self.stripe_customer_id = ""
                self.tier = "free"
                self.save()

        except Exception as e:
            if settings.SENTRY_DSN:
                sentry_sdk.capture_exception(e)
                sentry_sdk.set_tag("stripe_error", "cancel_sub")
                sentry_sdk.set_extra(
                    "user",
                    {
                        "email": self.email,
                        "tier": self.tier,
                        "stripe_customer_id": self.stripe_customer_id,
                    },
                )
                sentry_sdk.capture_message(
                    f"Error cancelling subscription for {self.email}", "fatal"
                )

    def update_sub(self, incoming_price_id):
        try:
            with transaction.atomic():
                current_tier = self.tier
                new_tier = ""

                # Map price ID to tier
                if incoming_price_id == settings.STRIPE_INDIVIDUAL_PRICE_ID:
                    new_tier = "individual"
                elif incoming_price_id == settings.STRIPE_TEAM_PRICE_ID:
                    new_tier = "team"
                else:
                    raise ValueError(f"Invalid price ID: {incoming_price_id}")

                if new_tier and new_tier != current_tier:
                    old_tier = self.tier
                    self.tier = new_tier

                    # Upgrading to team tier
                    if new_tier == "team":
                        if not self.owned_teams.exists():
                            team = Team.objects.create(
                                name=f"{self.email}'s Team", owner=self
                            )
                            self.team = team

                    # Downgrading from team tier
                    elif old_tier == "team" and new_tier != "team":
                        if self.owned_teams.exists():
                            team = self.owned_teams.first()
                            # Convert team members to free tier
                            for member in team.members.all():
                                if member != self:  # Don't process the owner here
                                    member.tier = "free"
                                    member.team = None
                                    member.save()
                                    self.notify_member(member, "downgrade")
                            team.delete()
                        self.team = None

                    self.save()

                    # Log successful update
                    logger.info(
                        f"Subscription updated for user {self.email}: {old_tier} -> {new_tier}"
                    )
                else:
                    logger.info(
                        f"Subscription already up to date for user {self.email}"
                    )
        except Exception as e:
            if settings.SENTRY_DSN:
                sentry_sdk.capture_exception(e)
                sentry_sdk.set_tag("stripe_error", "update_sub")
                sentry_sdk.set_extra(
                    "user",
                    {
                        "email": self.email,
                        "tier": self.tier,
                        "stripe_customer_id": self.stripe_customer_id,
                    },
                )
                sentry_sdk.capture_message(
                    f"Error updating subscription for {self.email}", "fatal"
                )

    def notify_member(self, member, reason):
        # Implement your notification logic here
        # TDOD: Send an email to the member
        pass
