from playwright.sync_api import Page, expect
from time import sleep
import pytest
from accounts.models import CustomUser
from django.conf import settings

@pytest.fixture(scope='module', autouse=True)
def django_db_setup():
    # This is so that we are accessing the same database (non-test) as the playwright browser
    settings.DATABASES['default']['TEST'] = {
        'MIRROR': 'default',
    }


def test_homepage_loads(page: Page):
    _navigate_to_homepage(page)


def test_signup(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_testing@example.com")

    # go to the edit account view to check credits
    page.goto("http://host.docker.internal:8001/accounts/edit-account/")
    expect(page.locator("text=You have 1000 remaining credits before you need to upgrade to a paid plan")).to_be_visible()


def test_upgrade_to_individual(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_individual@example.com")

    _navigate_to_homepage(page)

    _upgrade_subscription(page, "individual")
    expect(page.locator("text=0 credits used out of 2500 credits in the past 30 days")).to_be_visible()


def test_upgrade_to_team(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_team@example.com")

    _navigate_to_homepage(page)

    _upgrade_subscription(page, "team")
    expect(page.locator("text=0 credits used out of 25000 credits in the past 30 days")).to_be_visible()


def test_upgrade_individual_to_team(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_upgrade@example.com")

    _navigate_to_homepage(page)

    _upgrade_subscription(page, "individual")

    _navigate_to_homepage(page)

    # click on the upgrade to team button
    page.locator("button:has-text('Upgrade to team')").click()

    # click on the Confirm button
    page.locator("button:has-text('Confirm')").click()

    # wait for the upgrade to take effect
    sleep(5)

    # go to the edit account view
    page.goto("http://host.docker.internal:8001/accounts/edit-account/")

    # check we are on the correct plan
    expect(page.locator("text=You are on team plan")).to_be_visible()


@pytest.mark.django_db
def test_invite_team_member(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_team-owner@example.com")

    _navigate_to_homepage(page)

    _upgrade_subscription(page, "team")

    # Invite the team member
    page.fill("#invite_email", "pw_team-member@example.com")
    page.get_by_text("Invite Team Member").last.click()

    # check that the team member account was created
    team_member = CustomUser.objects.get(email="pw_team-member@example.com")
    assert team_member.tier == "team"


@pytest.mark.django_db
def test_downgrade_team_to_individual(page: Page):
    _navigate_to_homepage(page)
    _create_account(page, "pw_downgrade@example.com")

    _navigate_to_homepage(page)

    _upgrade_subscription(page, "team")

    # Invite the team member
    page.fill("#invite_email", "pw_team-member2@example.com")
    page.get_by_text("Invite Team Member").last.click()

    # check that the team member account was created
    team_member = CustomUser.objects.get(email="pw_team-member2@example.com")
    assert team_member.tier == "team"

    _navigate_to_homepage(page)

    # click the downgrade button
    page.locator("button:has-text('Downgrade')").click()

    # click the confirm button
    page.locator("button:has-text('Confirm')").click()

    # wait for the changes to take effect
    sleep(5)

    # go to the edit account view
    page.goto("http://host.docker.internal:8001/accounts/edit-account/")

    # check we are on the correct plan
    expect(page.locator("text=You are on individual plan")).to_be_visible()

    # check that the team member is on the free tier
    team_member = CustomUser.objects.get(email="pw_team-member2@example.com")
    assert team_member.tier == "free"


def _navigate_to_homepage(page: Page):
    # Navigate to the homepage
    page.goto("http://host.docker.internal:8001/")

    # Verify that the URL is correct
    expect(page).to_have_url("http://host.docker.internal:8001/")

    # You can also check for other elements specific to your homepage
    expect(page.locator("text=The RAG solution you're looking for")).to_be_visible()


def _create_account(page: Page, email: str):
    # Click the get started link.
    page.get_by_role("link", name="Start Free Trial").first.click()

    # Verify that the URL is correct
    expect(page).to_have_url("http://host.docker.internal:8001/accounts/signup/")

    # You can also check for other elements specific to your homepage
    expect(page.locator("text=Create your account")).to_be_visible()

    # Enter email
    page.fill("input[name='email']", email)

    # Enter password
    page.fill("input[name='password1']", "securepassword123")

    # Locate and click the Register button by its text content
    page.get_by_text("Register").click()

    # wait for the account to be created
    sleep(5)


def _upgrade_subscription(page: Page, tier: str):
    # click the upgrade button
    #upgrade_text = f"Upgrade to {tier.capitalize()} Subscription"
    page.get_by_text(f"Upgrade to {tier.capitalize()} Subscription").click()

    # wait for the stripe checkout to load
    sleep(5)

    # enter credit card details
    page.fill("input[name='cardNumber']", "4242424242424242")
    page.fill("input[name='cardExpiry']", "1228")
    page.fill("input[name='cardCvc']", "123")

    # enter name
    page.fill("input[name='billingName']", "Test User")

    # click the pay button
    page.get_by_text("Subscribe").last.click()

    # Should be redirected to the edit account view
    expect(page).to_have_url("http://host.docker.internal:8001/accounts/edit-account/", timeout=30000)

    # check we are on the correct plan
    expect(page.locator(f"text=You are on {tier} plan")).to_be_visible()
