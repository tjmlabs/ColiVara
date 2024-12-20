import datetime
import hashlib
import logging

import requests
import sentry_sdk
import stripe
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.crypto import get_random_string

logger = logging.getLogger(__name__)


class Team(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        "CustomUser", related_name="owned_teams", on_delete=models.CASCADE
    )

    def __str__(self):
        return self.name


class CreditUsage(models.Model):
    USAGE_TYPES = (
        ("upsert", "Upsert"),
        ("patch", "Patch"),
        ("search", "Search"),
        ("filter", "Filter"),
        ("embeddings", "Embeddings"),
    )

    user = models.ForeignKey(
        "CustomUser", related_name="credit_usage", on_delete=models.CASCADE
    )
    credits_used = models.IntegerField(default=1)  # default to 1 credit for any usage
    created_at = models.DateTimeField(auto_now_add=True)
    num_pages = models.IntegerField(default=1)  # default to 1 page for any usage
    used_proxy = models.BooleanField(default=False)
    filename = models.CharField(max_length=255, default="N/A")
    request_type = models.CharField(max_length=50, choices=USAGE_TYPES)


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
    svix_application_id = models.CharField(max_length=255, blank=True)
    svix_endpoint_id = models.CharField(max_length=255, blank=True)
    svix_endpoint_url = models.CharField(max_length=255, blank=True)
    svix_endpoint_secret = models.CharField(max_length=255, blank=True)
    token = models.CharField(max_length=255, blank=True)
    available_credits = models.IntegerField(default=100)  # free 100 credits on signup
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

    async def record_credit_usage(
        self, request_type, credits_used, num_pages, used_proxy, filename
    ):
        # create the credit usage object
        await CreditUsage.objects.acreate(
            user=self,
            request_type=request_type,
            credits_used=credits_used,
            num_pages=num_pages,
            used_proxy=used_proxy,
            filename=filename,
        )

    def get_credit_usage(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer_id = self.stripe_customer_id
        if self.tier == "team":
            customer_id = self.team.owner.stripe_customer_id

        if not customer_id:
            return []

        # retrieve subscription "current_period_end": 1682288167, and "current_period_start": 1679624167
        sub = stripe.Subscription.retrieve(self.stripe_subscription_id)
        start_time = sub.current_period_start
        end_time = sub.current_period_end

        # change start time to be midnight before the subscription started
        start_time = datetime.datetime.fromtimestamp(start_time)
        start_time = start_time.replace(hour=0, minute=0, second=0)
        start_time = int(start_time.timestamp())

        # change end time to be midnight after the subscription ends
        end_time = datetime.datetime.fromtimestamp(end_time)
        end_time = end_time.replace(hour=0, minute=0, second=0)
        end_time = int(end_time.timestamp())
        try:
            usage = stripe.billing.Meter.list_event_summaries(
                settings.STRIPE_METER_ID,
                customer=customer_id,
                start_time=start_time,
                end_time=end_time,
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

                # Map price ID to tier
                price_id_to_tier = {
                    settings.STRIPE_INDIVIDUAL_PRICE_ID_RECURRENT: "individual",
                    settings.STRIPE_TEAM_PRICE_ID_RECURRENT: "team",
                    settings.STRIPE_INDIVIDUAL_PRICE_ID_USAGE: "individual",
                    settings.STRIPE_TEAM_PRICE_ID_USAGE: "team",
                }
                new_tier = price_id_to_tier.get(incoming_price_id, None)
                if not new_tier:
                    raise ValueError(f"Invalid price ID provided: {incoming_price_id}")

                if new_tier != current_tier:
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
        # Stripe will email the subscription owner, but for team members, you may want to notify them separately
        if reason == "cancel":
            subject = "Your Team Onwner subscription has been cancelled"
            message = "Your Team Owner has cancelled the subscription. You have been downgraded to the free tier."
        elif reason == "downgrade":
            subject = "Your Team Owner has downgraded the subscription"
            message = "Your Team Owner has downgraded the subscription. You have been downgraded to the free tier."
        else:
            return

        to = [member.email]
        from_email = settings.DEFAULT_FROM_EMAIL
        admin_email = settings.ADMINS[0][1]
        email = EmailMessage(
            subject=subject,
            body=message,
            to=to,
            bcc=[admin_email],
            from_email=from_email,
        )
        email.send()
        pass


@receiver(post_save, sender=CustomUser)
def check_quota_limit(sender, instance, **kwargs):
    if instance.get_available_credits() <= 25 and instance.tier == "free":
        hashed = hashlib.md5(instance.email.lower().encode("utf-8")).hexdigest()

        # get the contact id
        headers = {
            "Authorization": f"Bearer {settings.MARKETING_EMAIL_API_KEY}",
        }

        response = requests.get(
            f"https://api.emailoctopus.com/lists/{settings.MARKETING_EMAIL_LIST_ID}/contacts/{hashed}",
            headers=headers,
        )
        id = response.json().get("id")

        # call the automation endpoint
        payload = {
            "contactId": id,
        }

        headers = {
            "Authorization": f"Bearer {settings.MARKETING_EMAIL_API_KEY}",
            "content-type": "application/json",
        }

        response = requests.post(
            f"https://api.emailoctopus.com/automations/{settings.QUOTA_LIMIT_AUTOMATION_ID}/queue",
            json=payload,
            headers=headers,
        )
