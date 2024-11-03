import stripe
from accounts.models import CustomUser, Team
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def home(request):
    return render(request, "home.html")


def payment_success(request):
    return render(request, "payment_success.html")


def payment_cancel(request):
    return render(request, "payment_cancel.html")


def stripe_checkout(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = request.build_absolute_uri(reverse("home"))
    cancel_url = request.build_absolute_uri(reverse("home"))
    email = request.user.email

    # Get the subscription type from the query parameter
    subscription_type = request.GET.get(
        "tier", "individual"
    )  # Default to 'individual' if not provided

    price_id = settings.STRIPE_INDIVIDUAL_PRICE_ID
    if subscription_type == "team":
        price_id = settings.STRIPE_TEAM_PRICE_ID

    checkout_session = stripe.checkout.Session.create(
        line_items=[
            {
                "price": price_id,
            },
        ],
        client_reference_id=request.user.id,
        mode="subscription",
        customer_email=email,
        success_url=success_url + "payment-success/",
        cancel_url=cancel_url + "payment-cancel/",
        metadata={"subscription_type": subscription_type},
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

    # we don't care about the signature in local development
    if not settings.LOCAL:
        webhook_secret = settings.STRIPE_WH_SECRET
        signature = request.headers.get("stripe-signature")
        try:
            event = stripe.Webhook.construct_event(
                payload=request.body, sig_header=signature, secret=webhook_secret
            )

        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except stripe.error.SignatureVerificationError as e:
            return JsonResponse({"error": str(e)}, status=400)

    data_object = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        user_id = int(data_object["client_reference_id"])
        user = CustomUser.objects.get(id=user_id)
        user.stripe_customer_id = data_object["customer"]
        user.stripe_subscription_id = data_object["subscription"]

        # Check the subscription type from metadata
        subscription_type = data_object["metadata"].get("subscription_type")
        user.tier = subscription_type
        if subscription_type == "team":
            team = Team.objects.create(name=f"{user.first_name}'s Team", owner=user)
            user.team = team

        user.save()
    elif event["type"] == "customer.subscription.deleted":
        customer_id = data_object["customer"]
        user = CustomUser.objects.get(stripe_customer_id=customer_id)
        user.tier = "free"
        user.save()

    return JsonResponse({"status": "success"}, status=200)
