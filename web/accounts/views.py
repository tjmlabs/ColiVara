import allauth.account.views as auth_views
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator

from .decorators import persist_session_vars
from .models import CustomUser


@login_required
def edit_account(request):
    if request.method == "GET":
        return render(request, "account/edit_account.html", {"user": request.user})

    error = None

    first_name = request.POST.get("first_name", None)
    last_name = request.POST.get("last_name", None)
    email = request.POST.get("email", None)

    try:
        user = CustomUser.objects.get(pk=request.user.pk)
        user.first_name = first_name
        user.last_name = last_name
        user.email = email
        user.save()
        messages.success(request, "Account updated successfully")
    except CustomUser.DoesNotExist:
        error = "User does not exist"

    if error:
        messages.error(request, error)

    return redirect("home")


@method_decorator(persist_session_vars(["plan"]), name="dispatch")
class SignupView(auth_views.SignupView):
    pass


def post_signup(request):
    # check the session for a plan. If pro, go to stripe checkout. If free, go to home.
    plan = request.session.get("plan", None)
    if plan == "pro":
        return redirect(reverse("stripe_checkout"))
    response = redirect(reverse("pricing"))
    # add query param to show a success message
    response["Location"] += "?home=true"
    return response
