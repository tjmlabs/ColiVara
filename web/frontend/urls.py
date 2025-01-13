from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("terms-privacy/", views.terms_privacy, name="terms_privacy"),
    path("stripe-checkout/", views.stripe_checkout, name="stripe_checkout"),
    path("stripe-portal/", views.stripe_portal, name="stripe_portal"),
    path("upgrade/", views.upgrade, name="upgrade"),
    path("downgrade/", views.downgrade, name="downgrade"),
    path("stripe-webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("payment-success/", views.payment_success, name="payment_success"),
    path("payment-cancel/", views.payment_cancel, name="payment_cancel"),
    path("documents/", views.documents_portal, name="documents_portal"),
    path("documents/create/", views.create_document, name="create_document"),
    path("documents/<int:document_id>/", views.edit_document, name="edit_document"),
]
