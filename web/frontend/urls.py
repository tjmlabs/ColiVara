from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("pricing/", views.pricing, name="pricing"),
    path("stripe-checkout/", views.stripe_checkout, name="stripe_checkout"),
    path("customer-portal/", views.customer_portal, name="customer_portal"),
    path("stripe-webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("payment-success/", views.payment_success, name="payment_success"),
    path("payment-cancel/", views.payment_cancel, name="payment_cancel"),
]
