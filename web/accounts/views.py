from allauth.account.forms import SignupForm
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from .models import CustomUser


@login_required
def edit_account(request):
    usage = request.user.get_credit_usage()
    credits_used = sum(event["aggregated_value"] for event in usage)
    starting_credits = 2500
    if request.user.tier == "team":
        starting_credits = 25000
    return render(
        request,
        "account/edit_account.html",
        {
            "user": request.user,
            "credits_used": credits_used,
            "starting_credits": starting_credits,
        },
    )


def post_signup(request):
    # check the session for a plan. If pro, go to stripe checkout. If free, go to home.
    plan = request.session.get("plan", None)
    if plan == "pro":
        return redirect(reverse("stripe_checkout"))
    response = redirect(reverse("pricing"))
    # add query param to show a success message
    response["Location"] += "?home=true"
    return response


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
    password = get_random_string(length=32)
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
    admin_email = settings.ADMINS[0][1]
    owner_name = request.user.email
    to = [member_email]
    email = EmailMessage(
        subject="Colivara Team Invitation",
        body=f"{str(owner_name)} invited to join their team on Colivara! Please use this temporary password to sign in: {str(password)}",
        to=to,
        from_email=admin_email,
    )
    email.content_subtype = "html"
    email.send()

    messages.success(request, "Account invited successfully")
    return redirect("home")
