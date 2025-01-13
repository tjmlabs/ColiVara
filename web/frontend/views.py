import base64
import json
import logging
from threading import Thread
from time import sleep

import stripe
from accounts.models import CustomUser, Team
from api.models import Collection, Document
from api.views import (DocumentIn, DocumentInPatch,
                       partial_update_document_sync,
                       process_upsert_document_sync)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
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


def terms_privacy(request):
    return render(request, "terms_privacy.html")


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


@login_required
def documents_portal(request):
    if request.method == "POST":
        # Get the list of selected document IDs
        document_ids = request.POST.getlist("documents")

        if not document_ids:
            messages.error(request, "No documents selected.")
            return redirect("documents_portal")

        # Delete the documents
        try:
            documents = Document.objects.filter(id__in=document_ids)
            documents.delete()
        except Document.DoesNotExist:
            messages.error(request, "Some documents do not exist.")

        messages.success(request, "Documents deleted successfully.")
        # Redirect back to the document portal
        return redirect("documents_portal")
    else:
        user_documents_list = (
            Document.objects.all()
            .filter(collection__owner=request.user)
            .order_by("-created_at")
        )

        paginator = Paginator(user_documents_list, 10)  # Show 10 documents per page

        page_number = request.GET.get("page")
        user_documents = paginator.get_page(page_number)
        return render(
            request, "documents_portal.html", {"user_documents": user_documents}
        )


@login_required
def create_document(request):
    if request.method == "POST":
        # Retrieve form data
        collection_name = request.POST.get("collection", "default_collection")
        wait = request.POST.get("wait") == "on"
        use_proxy = request.POST.get("use_proxy") == "on"
        document_name = request.POST.get("name")
        metadata_keys = request.POST.getlist("metadata_key[]")
        metadata_values = request.POST.getlist("metadata_value[]")
        file = request.FILES.get("file")
        url = request.POST.get("file_url")

        # Combine keys and values into a dictionary
        metadata = {
            key: value
            for key, value in zip(metadata_keys, metadata_values)
            if key and value
        }

        # Handle file upload
        if file:
            # convert file to base64
            file_content = file.read()
            base64_file = base64.b64encode(file_content).decode()
        else:
            base64_file = None

        try:
            payload = DocumentIn(
                name=document_name,
                collection_name=collection_name,
                metadata=metadata,
                url=url,
                base64=base64_file,
                wait=wait,
                use_proxy=use_proxy,
            )

            if wait:
                process_upsert_document_sync(request.user, payload)
                messages.success(request, "Document created successfully.")
            else:
                # Create and start a new thread for the background task
                thread = Thread(
                    target=process_upsert_document_sync, args=(request.user, payload)
                )
                thread.start()
                messages.success(
                    request, "Document is being processed in the background."
                )
            return redirect("documents_portal")
        except Exception as e:
            messages.error(request, f"Failed to create document: {e}")
            return redirect("create_document")
    else:
        user_collections = Collection.objects.all().filter(owner=request.user)
        return render(
            request,
            "view_document.html",
            {"user_collections": user_collections, "is_editing": False},
        )


@login_required
def edit_document(request, document_id):
    # Fetch the document by its ID
    document = Document.objects.get(id=document_id)

    if request.method == "POST":
        # Handle form submission for editing the document
        document_name = request.POST.get("name")
        wait = request.POST.get("wait") == "on"
        use_proxy = request.POST.get("use_proxy") == "on"
        metadata_keys = request.POST.getlist("metadata_key[]")
        metadata_values = request.POST.getlist("metadata_value[]")
        file = request.FILES.get("file")
        url = request.POST.get("file_url")

        # Combine keys and values into a dictionary
        metadata = {
            key: value
            for key, value in zip(metadata_keys, metadata_values)
            if key and value
        }

        # Handle file upload
        if file:
            # convert file to base64
            file_content = file.read()
            base64_file = base64.b64encode(file_content).decode()
        else:
            base64_file = None

        try:
            payload = DocumentInPatch(
                name=document_name,
                metadata=metadata,
                url=url,
                base64=base64_file,
                use_proxy=use_proxy,
            )

            if wait or (not url and not file):
                partial_update_document_sync(request.user, document, payload)
                messages.success(request, "Document updated successfully.")
            else:
                # Create and start a new thread for the background task
                thread = Thread(
                    target=partial_update_document_sync,
                    args=(request.user, document, payload),
                )
                thread.start()
                messages.success(
                    request, "Document is being updated in the background."
                )
            return redirect("documents_portal")
        except Exception as e:
            messages.error(request, f"Failed to updated document: {e}")
            return redirect("edit_document", document_id=document.id)

    else:
        # For GET request, render the template with existing document data
        user_collections = Collection.objects.filter(owner=request.user)
        return render(
            request,
            "view_document.html",
            {
                "user_collections": user_collections,
                "document": document,
                "is_editing": True,  # Flag to indicate that this is an edit
            },
        )


@login_required
def stripe_portal(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.billing_portal.Session.create(
        customer=request.user.stripe_customer_id,
        return_url=request.build_absolute_uri(reverse("edit_account")),
    )
    return redirect(session.url, code=303)


@require_POST
@login_required
def upgrade(request):
    if request.user.tier == "team":
        messages.error(request, "You are already on the team plan.")
        return redirect("edit_account")
    if request.user.tier == "free":
        messages.error(request, "You are on the free plan. Please subscribe first.")
        return redirect("edit_account")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    subscription_id = request.user.stripe_subscription_id
    new_price_id_recurrent = settings.STRIPE_TEAM_PRICE_ID_RECURRENT
    new_price_id_usage = settings.STRIPE_TEAM_PRICE_ID_USAGE

    stripe_sub = stripe.Subscription.list(customer=request.user.stripe_customer_id)

    item_ids = [item["id"] for item in stripe_sub["data"][0]["items"]["data"]]
    stripe.Subscription.modify(
        subscription_id,
        items=[
            {"id": item_ids[0], "price": new_price_id_recurrent},
            {"id": item_ids[1], "price": new_price_id_usage},
        ],
    )
    messages.success(request, "Subscription upgraded to team plan.")
    return redirect("edit_account")


@require_POST
@login_required
def downgrade(request):
    if request.user.tier == "individual":
        messages.error(request, "You are already on the individual plan.")
        return redirect("edit_account")
    if request.user.tier == "free":
        messages.error(request, "You are on the free plan. Please subscribe first.")
        return redirect("edit_account")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    subscription_id = request.user.stripe_subscription_id
    new_price_id_recurrent = settings.STRIPE_INDIVIDUAL_PRICE_ID_RECURRENT
    new_price_id_usage = settings.STRIPE_INDIVIDUAL_PRICE_ID_USAGE

    stripe_sub = stripe.Subscription.list(customer=request.user.stripe_customer_id)

    item_ids = [item["id"] for item in stripe_sub["data"][0]["items"]["data"]]
    stripe.Subscription.modify(
        subscription_id,
        items=[
            {"id": item_ids[0], "price": new_price_id_recurrent},
            {"id": item_ids[1], "price": new_price_id_usage},
        ],
    )
    messages.success(request, "Subscription downgraded to individual plan.")
    return redirect("edit_account")


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
        # we want to sleep here as we get update events with race conditions with checkout.session.completed
        sleep(2)
        try:
            user = CustomUser.objects.get(stripe_customer_id=customer_id)
        except CustomUser.DoesNotExist:
            logger.error(f"User with customer ID {customer_id} not found")
            return JsonResponse({"status": "success"}, status=200)
        logger.info(f"User Email: {user.email}")
        incoming_price_id = data_object["items"]["data"][0]["plan"]["id"]
        logger.info(f"Incoming Price ID: {incoming_price_id}")
        user.update_sub(incoming_price_id)
        logger.info("Subscription updated")

    return JsonResponse({"status": "success"}, status=200)
