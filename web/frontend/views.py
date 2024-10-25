import stripe
from accounts.models import CustomUser
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def home(request):
    return render(request, "home.html")


def pricing(request):
    # get dashboard request GET parameter
    dashboard = request.GET.get("dashboard", None)
    return render(request, "pricing.html", {"dashboard": dashboard})


def payment_success(request):
    return render(request, "payment_success.html")


def payment_cancel(request):
    return render(request, "payment_cancel.html")


def stripe_checkout(request):
    if request.user.is_anonymous:
        request.session["plan"] = "pro"
        return redirect(reverse("account_signup"))
    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = request.build_absolute_uri(reverse("home"))
    cancel_url = request.build_absolute_uri(reverse("home"))
    email = request.user.email
    checkout_session = stripe.checkout.Session.create(
        line_items=[
            {
                "price": f"{settings.STRIPE_PRICE_ID}",
                "quantity": 1,
            },
        ],
        client_reference_id=request.user.id,
        mode="subscription",
        customer_email=email,
        success_url=success_url + "/payment-success/",
        cancel_url=cancel_url + "/payment-cancel/",
    )
    return redirect(checkout_session.url, code=303)


def customer_portal(request):
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
    webhook_secret = settings.STRIPE_WH_SECRET
    if settings.LOCAL:
        webhook_secret = (
            "whsec_ba621ab4001800ffb9f7dca15c7d2f1e749f6ae563342edce7b0e7b01ba64d70"
        )
    else:
        webhook_secret = settings.STRIPE_WH_SECRET
    signature = request.headers.get("stripe-signature")
    stripe.api_key = settings.STRIPE_SECRET_KEY
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
        user.tier = "pro"
        user.save()
    if event["type"] == "customer.subscription.deleted":
        customer_id = data_object["customer"]
        user = CustomUser.objects.get(stripe_customer_id=customer_id)
        user.tier = "free"
        user.save()
    return JsonResponse({"status": "success"}, status=200)
