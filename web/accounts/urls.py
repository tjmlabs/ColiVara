from django.urls import path

from . import views

urlpatterns = [
    path("edit-account/", views.edit_account, name="edit_account"),
    path("post-signup/", views.post_signup, name="post_signup"),
    path("invite-team-member/", views.invite_team_member, name="invite_team_member"),
]
