# forms.py
from allauth.account.forms import SignupForm
from django import forms

from .models import Team


class CustomSignupForm(SignupForm):
    full_name = forms.CharField(max_length=255, required=True)

    # tier with "team" and "individual" options
    tier = forms.ChoiceField(
        choices=[("team", "Team"), ("individual", "Individual")],
        required=True,
    )

    def save(self, request):
        user = super().save(request)
        name_split = self.cleaned_data["full_name"].split(" ")
        if len(name_split) == 2:
            user.first_name = name_split[0]
            user.last_name = name_split[1]
        elif len(name_split) > 2:
            user.first_name = name_split[0]
            user.last_name = " ".join(name_split[1:])
        else:
            user.first_name = name_split[0]
        user.tier = self.cleaned_data["tier"]
        user.subscribe_to_emails = self.cleaned_data["subscribe_to_emails"]

        if user.tier == "team":
            # Create a new team and set the user as the owner
            team = Team.objects.create(name=f"{user.first_name}'s Team", owner=user)
            user.team = team
            user.available_credits = 25000  # 25,000 credits for teams

        user.save()
        return user
