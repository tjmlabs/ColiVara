from time import sleep

import requests
from accounts.models import CustomUser
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Updates our email list over the marketing email platform"

    def fetch_all_unsubscribed_contacts(self, url, headers):
        all_contacts = []

        params = {"status": "UNSUBSCRIBED"}

        while True:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                self.stdout.write(
                    self.style.ERROR(
                        f"API request failed with status {response.status_code}"
                    )
                )
                break

            data = response.json()
            all_contacts.extend(data["data"])

            # Check if there's a next page
            if (
                "paging" in data
                and data["paging"]["next"]["starting_after"] is not None
            ):
                # Update params with the starting_after parameter for the next page
                params["starting_after"] = data["paging"]["next"]["starting_after"]
            else:
                break  # No more pages

        return all_contacts

    def upsert_contact(self, url, headers, user):
        status = "subscribed" if user.subscribe_to_emails else "unsubscribed"
        payload = {
            "email_address": user.email,
            "status": status,
        }

        try:
            response = requests.put(url, headers=headers, json=payload)

            if response.status_code not in [200, 201]:
                self.stdout.write(
                    self.style.WARNING(
                        f"Failed to upsert user {user.email}: {response.status_code} - {response.text}"
                    )
                )
                return False
            return True
        except requests.exceptions.RequestException as e:
            self.stdout.write(
                self.style.ERROR(f"Error upserting user {user.email}: {str(e)}")
            )
            return False

    def handle(self, *args, **options):
        # first step - updated unsubscribers
        list_id = settings.MARKETING_EMAIL_LIST_ID
        api_key = settings.MARKETING_EMAIL_API_KEY
        url = f"https://api.emailoctopus.com/lists/{list_id}/contacts"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        # Get all unsubscribed contacts
        unsubscribed_contacts = self.fetch_all_unsubscribed_contacts(url, headers)

        updated_count = 0
        for contact in unsubscribed_contacts:
            email_address = contact["email_address"]
            try:
                user = CustomUser.objects.get(email=email_address)
                user.subscribe_to_emails = False
                user.save()
                updated_count += 1
            except CustomUser.DoesNotExist:
                continue  # not a user

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated {updated_count} users as unsubscribed"
            )
        )

        # Upsert all users
        users = CustomUser.objects.all()
        successful_upserts = 0
        failed_upserts = 0

        # Add batch processing to avoid overwhelming the API
        batch_size = 100
        for i in range(0, len(users), batch_size):
            batch = users[i : i + batch_size]

            for user in batch:
                if self.upsert_contact(url, headers, user):
                    successful_upserts += 1
                else:
                    failed_upserts += 1
                sleep(0.3)

            # Add a status update for each batch
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed batch {i//batch_size + 1}: "
                    f"{successful_upserts} successful, {failed_upserts} failed"
                )
            )

        # Final summary
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed email list sync:\n"
                f"- {successful_upserts} contacts successfully upserted\n"
                f"- {failed_upserts} contacts failed to upsert"
            )
        )
