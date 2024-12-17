from allauth.account.forms import SignupForm
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST
from svix.api import ApplicationIn, EndpointIn, EndpointUpdate, Svix

from .models import CreditUsage, CustomUser


@login_required
def edit_account(request):
    initial_grant = request.user.get_available_credits()
    if initial_grant == 0:
        usage = request.user.get_credit_usage()
        credits_used = int(sum(event["aggregated_value"] for event in usage))
        starting_credits = 2500
        if request.user.tier == "team":
            starting_credits = 25000
    else:
        credits_used = 100 - initial_grant
        starting_credits = 100

    credit_usages_list = CreditUsage.objects.filter(user=request.user).order_by(
        "-created_at"
    )
    paginator = Paginator(credit_usages_list, 10)  # Show 10 usages per page

    page_number = request.GET.get("page")
    credit_usages = paginator.get_page(page_number)

    return render(
        request,
        "account/edit_account.html",
        {
            "user": request.user,
            "credits_used": credits_used,
            "starting_credits": starting_credits,
            "credit_usages": credit_usages,
        },
    )


@login_required
@require_POST
def invite_team_member(request):
    if not request.user.tier == "team" or request.user != request.user.team.owner:
        messages.error(request, "You are not authorized to add team members.")
        return redirect("home")

    member_email = request.POST.get("email")

    # Check if a user with this email already exists
    if CustomUser.objects.filter(email=member_email).exists():
        messages.error(request, "A user with this email already exists.")
        return redirect("edit_account")

    # TODO: force the user to reset the temp password
    # create a new user with the email
    password = get_random_string(length=8)
    data = {
        "email": member_email,
        "password1": password,
    }

    form = SignupForm(data)
    if form.is_valid():
        form.save(request)

    # get the created user instance
    user = CustomUser.objects.filter(email=member_email).first()
    user.available_credits = 0
    user.team = request.user.team
    user.tier = "team"
    user.save()

    # send an email to the user with the password
    from_email = settings.DEFAULT_FROM_EMAIL
    owner_name = request.user.email
    to = [member_email]
    email = EmailMessage(
        subject="Colivara Team Invitation",
        body=f"{str(owner_name)} invited to join their team on Colivara! Please use this temporary password to sign in: {str(password)}",
        to=to,
        from_email=from_email,
    )
    email.content_subtype = "html"
    email.send()

    messages.success(request, "Account invited successfully")
    return redirect("edit_account")


@login_required
@require_POST
def add_webhook(request):
    webhook_url = request.POST.get("url")

    # Return an error if SVIX_TOKEN is not set
    if settings.SVIX_TOKEN == "":
        messages.error(request, "SVIX_TOKEN is not set.")

    svix = Svix(settings.SVIX_TOKEN)

    if not request.user.svix_application_id:
        app = svix.application.create(ApplicationIn(name=request.user.email))
        app_id = app.id
    else:
        app_id = request.user.svix_application_id

    if request.user.svix_endpoint_id:
        # update the webhook
        endpoint_out = svix.endpoint.update(
            app_id,
            request.user.svix_endpoint_id,
            EndpointUpdate(
                url=webhook_url,
            ),
        )
    else:
        # create the webhook
        endpoint_out = svix.endpoint.create(
            app_id,
            EndpointIn(
                url=webhook_url,
                version=1,
                description="User webhook",
            ),
        )

    endpoint_secret_out = svix.endpoint.get_secret(app_id, endpoint_out.id)

    # save the app_id, endpoint id, url, and secret to the user
    request.user.svix_application_id = app_id
    request.user.svix_endpoint_id = endpoint_out.id
    request.user.svix_endpoint_url = webhook_url
    request.user.svix_endpoint_secret = endpoint_secret_out.key
    request.user.save()

    messages.success(request, "Webhook added successfully.")
    return redirect("edit_account")
