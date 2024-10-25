from django.urls import path

from . import views

urlpatterns = [
    path("edit-account/", views.edit_account, name="edit_account"),
    path("signup/", views.SignupView.as_view(), name="account_signup"),
    path("post-signup/", views.post_signup, name="post_signup"),
]
