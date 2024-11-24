from time import sleep
from typing import Any, Dict, List

import requests
from api.models import Page, PageEmbedding
from django.conf import settings
from django.core.management.base import BaseCommand
from tenacity import retry, stop_after_attempt, wait_fixed

# Constants
EMBEDDINGS_URL = settings.EMBEDDINGS_URL
DELAY_BETWEEN_BATCHES = 1  # seconds


class Command(BaseCommand):
    help = "Update embeddings for all documents, we run this whenever we upgrade the base model"

    def handle(self, *args: Any, **options: Any) -> None:
        pages = Page.objects.all()

        for page in pages:
            image: List[str] = [page.img_base64]
            embeddings_obj: List[Dict[str, Any]] = send_batch(image)
            embeddings: List[float] = embeddings_obj[0]["embedding"]
            page.embeddings.all().delete()
            bulk_create_embeddings = [
                PageEmbedding(page=page, embedding=embedding)
                for embedding in embeddings
            ]
            PageEmbedding.objects.bulk_create(bulk_create_embeddings)
            self.stdout.write(self.style.SUCCESS(f"Updated embedding for {page.id}"))
            sleep(DELAY_BETWEEN_BATCHES)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def send_batch(images: List[str]) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"input": {"task": "image", "input_data": images}}
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {settings.EMBEDDINGS_URL_TOKEN}"
    }
    response = requests.post(settings.EMBEDDINGS_URL, json=payload, headers=headers)
    response.raise_for_status()
    data: List[Dict[str, Any]] = response.json()["output"]["data"]
    return data
