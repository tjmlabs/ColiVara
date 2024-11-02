import json
import logging

import stripe
from accounts.models import CustomUser, Team
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def home(request):
    return render(request, "home.html")


def payment_success(request):
    messages.success(request, "Payment successful!")
    return redirect("edit_account")


def payment_cancel(request):
    messages.success(request, "Payment cancelled.")
    return redirect("home")


def stripe_checkout(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = request.build_absolute_uri(reverse("payment_success"))
    cancel_url = request.build_absolute_uri(reverse("payment_cancel"))
    email = request.user.email

    # Get the subscription type from the query parameter
    subscription_type = request.GET.get(
        "tier", "individual"
    )  # Default to 'individual' if not provided

    price_id_recurrent = settings.STRIPE_INDIVIDUAL_PRICE_ID_RECURRENT
    price_id_usage = settings.STRIPE_INDIVIDUAL_PRICE_ID_USAGE
    if subscription_type == "team":
        price_id_recurrent = settings.STRIPE_TEAM_PRICE_ID_RECURRENT
        price_id_usage = settings.STRIPE_TEAM_PRICE_ID_USAGE

    checkout_session = stripe.checkout.Session.create(
        line_items=[
            {
                "price": price_id_recurrent,
                "quantity": 1,
            },
            {
                "price": price_id_usage,
            },
        ],
        mode="subscription",
        customer_email=email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"subscription_type": subscription_type, "user_id": request.user.id},
    )
    return redirect(checkout_session.url, code=303)


def stripe_portal(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.billing_portal.Session.create(
        customer=request.user.stripe_customer_id,
        return_url=request.build_absolute_uri(reverse("home")),
    )
    return redirect(session.url, code=303)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    event = None
    # we don't care about the signature in local development
    if settings.LOCAL:
        event = stripe.Event.construct_from(json.loads(request.body), stripe.api_key)

    else:
        webhook_secret = settings.STRIPE_WH_SECRET
        try:
            sig_header = request.META["HTTP_STRIPE_SIGNATURE"]
            event = stripe.Webhook.construct_event(
                payload=request.body, sig_header=sig_header, secret=webhook_secret
            )

        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except stripe.error.SignatureVerificationError as e:
            return JsonResponse({"error": str(e)}, status=400)

    data_object = event["data"]["object"]
    logger.info(f"Received event: {event['type']}")
    if event["type"] == "checkout.session.completed":
        logger.info("Received event: Subscription created")
        user_id = data_object["metadata"].get("user_id")
        logger.info(f"User ID: {user_id}")
        user = CustomUser.objects.get(id=user_id)
        logger.info(f"User Email: {user.email}")
        user.stripe_customer_id = data_object["customer"]
        user.stripe_subscription_id = data_object["subscription"]

        # Check the subscription type from metadata
        subscription_type = data_object["metadata"].get("subscription_type")
        logger.info(f"Subscription Type: {subscription_type}")
        user.tier = subscription_type
        if subscription_type == "team":
            team = Team.objects.create(name=f"{user.email}'s Team", owner=user)
            user.team = team

        user.save()
        logger.info("User saved")
    elif event["type"] == "customer.subscription.deleted":
        logger.info("Received event: Subscription deleted")
        customer_id = data_object["customer"]
        user = CustomUser.objects.get(stripe_customer_id=customer_id)
        logger.info(f"User Email: {user.email}")
        user.cancel_sub()
        logger.info("Subscription cancelled")

    elif event["type"] == "customer.subscription.updated":
        logger.info("Received event: Subscription updated")
        customer_id = data_object["customer"]
        user = CustomUser.objects.get(stripe_customer_id=customer_id)
        logger.info(f"User Email: {user.email}")
        incoming_price_id = data_object["items"]["data"][0]["plan"]["id"]
        logger.info(f"Incoming Price ID: {incoming_price_id}")
        user.update_sub(incoming_price_id)
        logger.info("Subscription updated")

    return JsonResponse({"status": "success"}, status=200)
