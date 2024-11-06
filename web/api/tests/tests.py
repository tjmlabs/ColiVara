import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from accounts.models import CustomUser
from api.middleware import add_slash
from api.models import Collection, Document, Page, PageEmbedding
from api.views import Bearer, QueryFilter, QueryIn, filter_query, router
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from ninja.testing import TestAsyncClient
from pydantic import ValidationError

pytestmark = [pytest.mark.django_db(transaction=True, reset_sequences=True)]

""" Authentication tests """


@pytest.fixture
async def user(db):
    """
    Fixture to create a test user with a token.
    """
    # get or create a user
    # delete any existing users
    await CustomUser.objects.all().adelete()
    user, _ = await CustomUser.objects.aget_or_create(
        username="test_user", token="valid_token"
    )
    return user


@pytest.fixture
def bearer():
    """
    Fixture to create an instance of the Bearer class.
    """
    return Bearer()


@pytest.fixture
def async_client():
    """
    Fixture to create an instance of the TestAsyncClient class.
    """
    return TestAsyncClient(router)


@pytest.fixture
async def collection(user):
    """
    Fixture to create a test collection.
    """
    # delete any existing collections
    await Collection.objects.all().adelete()
    collection, _ = await Collection.objects.aget_or_create(
        name="Test Collection Fixture", metadata={"key": "value"}, owner=user
    )
    return collection


@pytest.fixture
async def document(user, collection):
    """
    Fixture to create a test document.
    """
    # delete any existing documents
    await Document.objects.all().adelete()
    document, _ = await Document.objects.aget_or_create(
        name="Test Document Fixture",
        collection=collection,
        url="https://www.example.com",
    )
    # create a page for the document
    page = await Page.objects.acreate(
        document=document,
        page_number=1,
        img_base64="base64_string",
    )
    await PageEmbedding.objects.acreate(
        page=page,
        embedding=[0.1 for _ in range(128)],
    )
    return document


async def test_valid_token(bearer, user):
    """
    Test that a valid token authenticates the user successfully.
    """
    request = None  # We don't need the request object for this test
    token = "valid_token"

    authenticated_user = await bearer.authenticate(request, token)

    assert authenticated_user is not None
    assert authenticated_user.username == user.username


async def test_invalid_token(bearer, user):
    """
    Test that an invalid token returns None (authentication fails).
    """
    request = None
    token = "invalid_token"

    authenticated_user = await bearer.authenticate(request, token)

    assert authenticated_user is None


async def test_missing_token(bearer):
    """
    Test that a missing token returns None.
    """
    request = None
    token = None  # No token provided

    authenticated_user = await bearer.authenticate(request, token)

    assert authenticated_user is None


# health
async def test_health(async_client):
    response = await async_client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


""" Collection tests """


async def test_create_collection(async_client, user):
    response = await async_client.post(
        "/collections/",
        json={"name": "Test Collection Fixture", "metadata": {"key": "value"}},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    # we return collectionOut in the response
    assert response.json() == {
        "id": 1,
        "name": "Test Collection Fixture",
        "metadata": {"key": "value"},
        "num_documents": 0,
    }


async def test_create_collection_unique(async_client, user, collection):
    response = await async_client.post(
        "/collections/",
        json={"name": "Test Collection Fixture", "metadata": {"key": "value"}},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 409


async def test_create_collection_with_all(async_client, user):
    response = await async_client.post(
        "/collections/",
        json={"name": "all"},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "value_error",
                "loc": ["body", "payload"],
                "msg": "Value error, Collection name 'all' is not allowed.",
                "ctx": {"error": "Collection name 'all' is not allowed."},
            }
        ]
    }


async def test_get_collection_by_name(async_client, user, collection):
    collection_name = "Test Collection Fixture"
    response = await async_client.get(
        f"/collections/{collection_name}/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Collection Fixture",
        "metadata": {"key": "value"},
        "num_documents": 0,
    }


async def test_list_collection(async_client, user, collection):
    response = await async_client.get(
        "/collections/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "name": "Test Collection Fixture", "metadata": {"key": "value"}, "num_documents": 0}
    ]


async def test_patch_collection(async_client, user, collection):
    collection_name = "Test Collection Fixture"
    response = await async_client.patch(
        f"/collections/{collection_name}/",
        json={"name": "Test Collection Update", "metadata": {"key": "value"}},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Collection Update",
        "metadata": {"key": "value"},
        "num_documents": 0,
    }

    # now check if the collection was actually updated
    new_collection_name = "Test Collection Update"
    response = await async_client.get(
        f"/collections/{new_collection_name}/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Collection Update",
        "metadata": {"key": "value"},
        "num_documents": 0,
    }


async def test_patch_collection_not_found(async_client, user, collection):
    response = await async_client.patch(
        "/collections/Nonexistent/",
        json={"name": "Test Collection Update", "metadata": {"key": "value"}},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


# test collection patch with no fields to update
async def test_patch_collection_no_data_to_update(async_client, user, collection):
    response = await async_client.patch(
        "/collections/Test Collection Fixture/",
        json={},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_patch_collection_no_metadata(async_client, user, collection):
    response = await async_client.patch(
        "/collections/Test Collection Fixture/",
        json={"name": "Test Collection Update"},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Collection Update",
        "metadata": {"key": "value"},
        "num_documents": 0,
    }


async def test_patch_collection_all(async_client, user, collection):
    response = await async_client.patch(
        "/collections/Test Collection Fixture/",
        json={"name": "all"},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_delete_collection(async_client, user, collection):
    collection_name = "Test Collection Fixture"
    response = await async_client.delete(
        f"/collections/{collection_name}/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 204

    # now check if the collection was actually deleted
    response = await async_client.get(
        f"/collections/{collection_name}/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


async def test_delete_collection_not_found(async_client, user, collection):
    response = await async_client.delete(
        "/collections/Nonexistent/",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


""" Document tests """


async def test_create_document_pdf_url_await(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "Test Document Fixture",
        "metadata": {},
        "url": "https://pdfobject.com/pdf/sample.pdf",
        "num_pages": 1,
        "collection_name": "default collection",
        "pages": None,
    }


async def test_create_document_pdf_url_async(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


# the update in upsert
async def test_create_document_pdf_url_update_await(
    async_client, user, document, collection
):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": document.name,
            # we changed from base64 to url
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "collection_name": collection.name,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "Test Document Fixture",
        "metadata": {},
        "url": "https://pdfobject.com/pdf/sample.pdf",
        "num_pages": 1,
        "collection_name": "Test Collection Fixture",
        "pages": None,
    }


async def test_create_document_pdf_url_update_async(
    async_client, user, document, collection
):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": document.name,
            # we changed from base64 to url
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "collection_name": collection.name,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_create_document_pdf_url_all(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "collection_name": "all",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 400


async def test_create_document_pdf_url_base64(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "base64": "base64_string",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_create_document_no_url_no_base64(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_create_document_pdf_url_collection_await(async_client, user, collection):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "collection_name": collection.name,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "Test Document Fixture",
        "metadata": {},
        "url": "https://pdfobject.com/pdf/sample.pdf",
        "num_pages": 1,
        "collection_name": collection.name,
        "pages": None,
    }


async def test_create_document_pdf_url_collection_async(async_client, user, collection):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://pdfobject.com/pdf/sample.pdf",
            "collection_name": collection.name,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_create_document_pdf_base64_await(async_client, user, collection):
    # test_docs/ is a directory in the same level as the test.py file - we will use a sample PDF file from there

    with open("api/tests/test_docs/sample.pdf", "rb") as f:
        # convert the file to base64
        base64_string = base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    response_data = response.json()
    assert response.status_code == 201
    # The URL should now be a pre-signed S3 URL
    assert "s3.amazonaws.com" in response_data["url"]
    assert response_data["id"] == 1
    assert response_data["name"] == "Test Document Fixture"
    assert response_data["metadata"] == {}
    assert response_data["num_pages"] == 1
    assert response_data["collection_name"] == "default collection"
    assert response_data["pages"] is None
    await Document.objects.all().adelete()


async def test_create_document_pdf_base64_async(async_client, user, collection):
    # test_docs/ is a directory in the same level as the test.py file - we will use a sample PDF file from there

    with open("api/tests/test_docs/sample.pdf", "rb") as f:
        # convert the file to base64
        base64_string = base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202
    await Document.objects.all().adelete()


async def test_create_document_docx_url_await(async_client, user, collection):
    url = "https://www.cte.iup.edu/cte/Resources/DOCX_TestPage.docx"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201

    await Document.objects.all().adelete()


async def test_create_document_docx_url_async(async_client, user, collection):
    url = "https://www.cte.iup.edu/cte/Resources/DOCX_TestPage.docx"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202

    await Document.objects.all().adelete()


async def test_create_document_docx_base64_await(async_client, user, collection):
    with open("api/tests/test_docs/sample.docx", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201

    await Document.objects.all().adelete()


async def test_create_document_docx_base64_async(async_client, user, collection):
    with open("api/tests/test_docs/sample.docx", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_create_document_webpage_await(async_client, user, collection):
    url = "https://gotenberg.dev/docs/getting-started/introduction"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201


async def test_create_document_webpage_async(async_client, user, collection):
    url = "https://gotenberg.dev/docs/getting-started/introduction"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_create_document_image_url_await(async_client, user, collection):
    url = "https://www.w3schools.com/w3css/img_lights.jpg"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201


async def test_create_document_image_url_async(async_client, user, collection):
    url = "https://www.w3schools.com/w3css/img_lights.jpg"
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": url,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_create_document_image_base64_await(async_client, user, collection):
    with open("api/tests/test_docs/sample.png", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201

    await Document.objects.all().adelete()


async def test_create_document_image_base64_async(async_client, user, collection):
    with open("api/tests/test_docs/sample.png", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "base64": base64_string,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_get_document_by_name(async_client, user, collection, document):
    document_name = document.name
    response = await async_client.get(
        f"documents/{document_name}/?collection_name=all&expand=pages",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Document Fixture",
        "metadata": {},
        "url": "https://www.example.com",
        "num_pages": 1,
        "collection_name": "Test Collection Fixture",
        "pages": [
            {
                "document_name": "Test Document Fixture",
                "img_base64": "base64_string",
                "page_number": 1,
            }
        ],
    }


# get document by name with multiple documents with the same name
async def test_get_document_by_name_multiple_documents(
    async_client, user, collection, document
):
    # first we create a new document with the same name under default collection
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://www.w3schools.com/w3css/img_lights.jpg",
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    # now we get the document by name with "all" as the collection name
    document_name = document.name
    response = await async_client.get(
        f"documents/{document_name}/?collection_name=all",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 409


async def test_get_documents(async_client, user, collection, document):
    response = await async_client.get(
        f"/documents/?collection_name={collection.name}",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "name": "Test Document Fixture",
            "metadata": {},
            "url": "https://www.example.com",
            "num_pages": 1,
            "collection_name": "Test Collection Fixture",
            "pages": None,
        }
    ]


async def test_get_documents_all(async_client, user, collection, document):
    response = await async_client.get(
        "/documents/?collection_name=all&expand=pages",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() != []
    assert isinstance(response.json(), list)


async def test_patch_document_no_embed(async_client, user, collection, document):
    # we are changing the name
    response = await async_client.patch(
        f"/documents/{document.name}/",
        json={"name": "Test Document Update", "collection_name": collection.name},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Document Update",
        "metadata": {},
        "url": "https://www.example.com",
        "num_pages": 1,
        "collection_name": "Test Collection Fixture",
        "pages": None,
    }
    new_document_name = "Test Document Update"
    # now check if the document was actually updated
    response = await async_client.get(
        f"/documents/{new_document_name}/?collection_name={collection.name}",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Test Document Update",
        "url": "https://www.example.com",
        "metadata": {},
        "num_pages": 1,
        "collection_name": "Test Collection Fixture",
        "pages": None,
    }


async def test_patch_document_not_found(async_client, user, collection, document):
    response = await async_client.patch(
        "/documents/Nonexistent/",
        json={"name": "Test Document Update", "collection_name": "all"},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


async def test_patch_document_multiple_documents(
    async_client, user, collection, document
):
    # first we create a new document with the same name under default collection
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://www.w3schools.com/w3css/img_lights.jpg",
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    # now we patch the document by name with "all" as the collection name
    response = await async_client.patch(
        "/documents/Test Document Fixture/",
        json={"name": "Test Document Update", "collection_name": "all"},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 409


async def test_patch_document_embed(async_client, user, collection, document):
    # we will change the base64 string of the page
    with open("api/tests/test_docs/sample.png", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    # we updated the base64 string of the page
    response = await async_client.patch(
        f"/documents/{document.name}/",
        json={
            "name": "Test Document Update",
            "base64": base64_string,
            "metadata": {"key": "value"},
            "collection_name": collection.name,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["id"] == 1
    assert response_data["name"] == "Test Document Update"
    assert response_data["metadata"] == {"key": "value"}
    assert "s3.amazonaws.com" in response_data["url"]
    assert response_data["num_pages"] == 1
    assert response_data["collection_name"] == "Test Collection Fixture"
    assert response_data["pages"] is None

    await Document.objects.all().adelete()


async def test_patch_document_url(async_client, user, collection, document):
    # we update the URL of the document
    response = await async_client.patch(
        f"/documents/{document.name}/",
        json={
            "name": "Test Document Update",
            "url": "https://www.w3schools.com/w3css/img_lights.jpg",
            "collection_name": collection.name,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["id"] == 1
    assert response_data["name"] == "Test Document Update"
    assert response_data["metadata"] == {}
    assert response_data["url"] == "https://www.w3schools.com/w3css/img_lights.jpg"
    assert response_data["num_pages"] == 1
    assert response_data["collection_name"] == "Test Collection Fixture"
    assert response_data["pages"] is None

    # now check if the document was actually updated
    await Document.objects.all().adelete()


async def test_patch_document_url_and_base64(async_client, user, collection, document):
    # we updated the base64 string of the page
    response = await async_client.patch(
        f"/documents/{document.name}/",
        json={
            "name": "Test Document Update",
            "base64": "base64_string",
            "url": "https://www.example.com",
            "metadata": {"key": "value"},
            "collection_name": collection.name,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_patch_document_no_data_to_update(
    async_client, user, collection, document
):
    response = await async_client.patch(
        f"/documents/{document.name}/",
        json={},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 422


async def test_delete_document(async_client, user, collection, document):
    response = await async_client.delete(
        f"/documents/delete-document/{document.name}/?collection_name={collection.name}",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 204

    # now check if the document was actually deleted
    response = await async_client.get(
        f"/documents/{document.name}/?collection_name=all",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


async def test_delete_document_not_found(async_client, user, collection, document):
    response = await async_client.delete(
        "/documents/delete-document/Nonexistent/?collection_name=all",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 404


async def test_delete_document_multiple_documents(
    async_client, user, collection, document
):
    # first we create a new document with the same name under default collection
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://www.w3schools.com/w3css/img_lights.jpg",
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201
    # now we delete the document by name with "all" as the collection name
    response = await async_client.delete(
        "/documents/delete-document/Test Document Fixture/?collection_name=all",
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 409


async def test_search_documents(async_client, user, collection, document):
    response = await async_client.post(
        "/search/",
        json={"query": "What is 1 + 1", "top_k": 1},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() != []


""" Search Filtering tests """


# we will create a fixture just for these tests
@pytest.fixture
async def search_filter_fixture(user):
    collection, _ = await Collection.objects.aget_or_create(
        name="Test Collection Filtering Fixture",
        metadata={"type": "AI papers"},
        owner=user,
    )
    document_1, _ = await Document.objects.aget_or_create(
        name="Attention is All You Need",
        collection=collection,
        url="https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf",
        metadata={"important": True},
    )
    document_2, _ = await Document.objects.aget_or_create(
        name="BMX : Entropy-weighted Similarity and Semantic-enhanced Lexical Search",
        collection=collection,
        url="https://arxiv.org/pdf/2408.06643v2",
        metadata={"important": False},
    )
    # create or get a page for the document
    page_1, _ = await Page.objects.aget_or_create(
        document=document_1,
        page_number=1,
        defaults={"img_base64": "base64_string"},
    )

    await PageEmbedding.objects.aget_or_create(
        page=page_1,
        defaults={"embedding": [0.1 for _ in range(128)]},
    )
    # create or get a page for the document
    page_2, _ = await Page.objects.aget_or_create(
        document=document_2,
        page_number=1,
        defaults={"img_base64": "base64_string"},
    )

    await PageEmbedding.objects.aget_or_create(
        page=page_2,
        defaults={"embedding": [0.1 for _ in range(128)]},
    )
    return collection, document_1, document_2


async def test_search_filter_collection_name(async_client, user, collection, document):
    response = await async_client.post(
        "/search/",
        json={"query": "What is 1 + 1", "top_k": 1, "collection_name": collection.name},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json() != []


async def test_search_filter_key_equals(async_client, user, search_filter_fixture):
    collection, document_1, document_2 = search_filter_fixture

    # Create a QueryIn object with a collection_name and a query_filter
    query_in = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(
            on="document", key="important", value=True, lookup="key_lookup"
        ),
    )

    # Call the filter_query function
    result = await filter_query(query_in, user)

    # Check if the result is a QuerySet
    assert isinstance(result, Page.objects.all().__class__)

    # Check if only one document is returned (the one with important=True)
    count = await result.acount()
    assert count == 1
    # get the page from the queryset
    page = await result.afirst()
    # check if the page belongs to the correct document
    assert page.document == document_1


async def test_filter_query_document_contains(search_filter_fixture, user):
    collection, document_1, _ = search_filter_fixture
    query_in = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(
            on="document", key="important", value=True, lookup="contains"
        ),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 1
    page = await result.afirst()
    assert page.document == document_1


async def test_filter_query_collection_metadata(search_filter_fixture, user):
    collection, _, _ = search_filter_fixture
    query_in = QueryIn(
        query="test query",
        collection_name="all",
        query_filter=QueryFilter(
            on="collection", key="type", value="AI papers", lookup="key_lookup"
        ),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 2

    # now check when count should be 0
    query_in = QueryIn(
        query="test query",
        query_filter=QueryFilter(
            on="collection", key="type", value="AI papers 2", lookup="key_lookup"
        ),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 0


async def test_filter_query_has_key(search_filter_fixture, user):
    collection, _, _ = search_filter_fixture
    query_in = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(on="document", key="important", lookup="has_key"),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 2

    # test if key is not there
    query_in_2 = QueryIn(
        query="test query",
        collection_id=collection.id,
        query_filter=QueryFilter(on="document", key="not_there", lookup="has_key"),
    )
    result = await filter_query(query_in_2, user)
    count = await result.acount()
    assert count == 0


async def test_filter_query_has_keys(search_filter_fixture, user):
    collection, _, _ = search_filter_fixture
    query_in = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(on="document", key=["important"], lookup="has_keys"),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 2

    # test if key is not there
    query_in_2 = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(on="document", key=["not_there"], lookup="has_keys"),
    )
    result = await filter_query(query_in_2, user)
    count = await result.acount()
    assert count == 0


async def test_filter_query_document_contained_by(search_filter_fixture, user):
    collection, document_1, _ = search_filter_fixture
    query_in = QueryIn(
        query="test query",
        collection_name=collection.name,
        query_filter=QueryFilter(
            on="document", key="important", value=True, lookup="contained_by"
        ),
    )
    result = await filter_query(query_in, user)
    count = await result.acount()
    assert count == 1
    page = await result.afirst()
    assert page.document == document_1


@pytest.mark.parametrize(
    "on, key, value, lookup, should_raise",
    [
        # Test cases for "contains" and "contained_by"
        ("document", "key1", "value1", "contains", False),
        ("document", "key1", None, "contains", True),
        ("document", ["key1"], "value1", "contains", True),
        ("document", "key1", "value1", "contained_by", False),
        ("document", "key1", None, "contained_by", True),
        ("document", ["key1"], "value1", "contained_by", True),
        # Test cases for "key_lookup"
        ("document", "key1", "value1", "key_lookup", False),
        ("document", "key1", None, "key_lookup", True),
        ("document", ["key1"], "value1", "key_lookup", True),
        # Test cases for "has_key"
        ("document", "key1", None, "has_key", False),
        ("document", "key1", "value1", "has_key", True),
        ("document", ["key1"], None, "has_key", True),
        # Test cases for "has_keys"
        ("document", ["key1", "key2"], None, "has_keys", False),
        ("document", "key1", None, "has_keys", True),
        ("document", ["key1", "key2"], "value1", "has_keys", True),
        # Test cases for "has_any_keys"
        ("document", ["key1", "key2"], None, "has_any_keys", False),
        ("document", ["key1", "key2"], "value1", "has_any_keys", True),
    ],
)
def test_query_filter_validation(on, key, value, lookup, should_raise):
    if should_raise:
        with pytest.raises(ValidationError):
            QueryFilter(on=on, key=key, value=value, lookup=lookup)
    else:
        filter_instance = QueryFilter(on=on, key=key, value=value, lookup=lookup)
        assert filter_instance.key == key if isinstance(key, list) else [key]
        assert filter_instance.value == value
        assert filter_instance.lookup == lookup


""" Embedding tests """


async def test_create_embedding(async_client, user):
    task = "query"
    input_data = ["What is 1 + 1"]
    response = await async_client.post(
        "/embeddings/",
        json={"task": task, "input_data": input_data},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 200
    assert response.json()["data"] != []


async def test_create_embedding_service_down(async_client, user):
    task = "query"
    input_data = ["What is 1 + 1"]
    EMBEDDINGS_POST_PATH = "api.models.aiohttp.ClientSession.post"
    # Create a mock response object with status 500
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.json.return_value = AsyncMock(return_value={"error": "Service Down"})
    # Mock the context manager __aenter__ to return the mock_response
    mock_response.__aenter__.return_value = mock_response
    # Patch the aiohttp.ClientSession.post method to return the mock_response
    with patch(EMBEDDINGS_POST_PATH, return_value=mock_response):
        response = await async_client.post(
            "/embeddings/",
            json={"task": task, "input_data": input_data},
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert response.status_code == 503


""" Helper tests """


async def test_file_to_imgbase64(async_client, user):
    headers = {
        "Authorization": f"Bearer {user.token}",
    }

    # Read the file content
    with open("api/tests/test_docs/sample.docx", "rb") as f:
        file_content = f.read()

    # Create a SimpleUploadedFile object
    file = SimpleUploadedFile(
        "sample.docx",
        file_content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # Send the request with the file in the FILES parameter
    response = await async_client.post(
        "helpers/file-to-imgbase64/",
        FILES={"file": file},
        headers=headers,
    )

    assert response.status_code == 200


async def test_file_to_base64(async_client, user):
    headers = {
        "Authorization": f"Bearer {user.token}",
    }

    # Read the file content
    with open("api/tests/test_docs/sample.docx", "rb") as f:
        file_content = f.read()

    # Create a SimpleUploadedFile object
    file = SimpleUploadedFile(
        "sample.docx",
        file_content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # Send the request with the file in the FILES parameter
    response = await async_client.post(
        "helpers/file-to-base64/",
        FILES={"file": file},
        headers=headers,
    )

    assert response.status_code == 200


""" Model tests """


async def test_collection_str_method(collection):
    assert str(collection) == "Test Collection Fixture"


async def test_document_str_method(document):
    assert str(document) == "Test Document Fixture"


async def test_page_str_method(document):
    page = await Page.objects.acreate(
        document=document,
        page_number=1,
        img_base64="base64_string",
    )
    assert str(page) == "Test Document Fixture - Page 1"


""" Test Middleware """


@pytest.fixture
def mock_get_response_sync():
    return Mock()


@pytest.fixture
def mock_get_response_async():
    """Mock for asynchronous get_response."""
    return AsyncMock()


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.path = "/test"
    request.path_info = "/test"
    request.META = {}
    request.session = {}
    return request


@pytest.mark.parametrize("get_response_type", ["sync", "async"])
@pytest.mark.parametrize(
    "path, should_append",
    [
        ("/test", True),
        ("/swagger/docs", False),
        ("/already/slashed/", False),
        ("/another/test", True),
        ("/redoc/path", False),
        ("/openapi", False),
        ("/some/path", True),
    ],
)
async def test_add_slash_middleware(
    get_response_type,
    path,
    should_append,
    mock_get_response_sync,
    mock_get_response_async,
    mock_request,
):
    """Parametrized test covering both sync and async get_response."""
    # Modify the mock_request based on the parameter
    mock_request.path = path
    mock_request.path_info = path

    if get_response_type == "sync":
        get_response = mock_get_response_sync
        get_response.reset_mock()
        # Apply the middleware with synchronous get_response
        middleware = add_slash(get_response)
        # Call the middleware with the mock request
        middleware(mock_request)
        if should_append:
            assert mock_request.path == f"{path}/"
            assert mock_request.path_info == f"{path}/"
        else:
            assert mock_request.path == path
            assert mock_request.path_info == path
        get_response.assert_called_once_with(mock_request)
    else:  # async
        get_response = mock_get_response_async
        get_response.reset_mock()
        # Apply the middleware with asynchronous get_response
        middleware = add_slash(get_response)
        # Call the middleware with the mock request
        await middleware(mock_request)
        if should_append:
            assert mock_request.path == f"{path}/"
            assert mock_request.path_info == f"{path}/"
        else:
            assert mock_request.path == path
            assert mock_request.path_info == path
        get_response.assert_awaited_once_with(mock_request)


""" Test Misc """


async def test_embeddings_service_down(async_client, user):
    EMBEDDINGS_POST_PATH = "api.models.aiohttp.ClientSession.post"
    # Create a mock response object with status 500
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.json.return_value = AsyncMock(return_value={"error": "Service Down"})

    # Mock the context manager __aenter__ to return the mock_response
    mock_response.__aenter__.return_value = mock_response

    # Patch the aiohttp.ClientSession.post method to return the mock_response
    with patch(EMBEDDINGS_POST_PATH, return_value=mock_response) as mock_post:
        # Perform the POST request to trigger embed_document
        response = await async_client.post(
            "/documents/upsert-document/",
            json={
                "name": "Test Document Fixture",
                "url": "https://pdfobject.com/pdf/sample.pdf",
                "wait": True,
            },
            headers={"Authorization": f"Bearer {user.token}"},
        )

        args, kwargs = mock_post.call_args
        assert kwargs["json"]["input"]["task"] == "image"
        assert "Authorization" in kwargs["headers"]

        # Assert that the response status code reflects the failure
        assert (
            response.status_code == 400
        )  # Assuming your view returns 400 on ValidationError

        # Optionally, check the response content for the error message

        assert response.json() == {
            "detail": "[\"Failed to save pages: ['Failed to get embeddings from the embeddings service.']\"]"
        }


async def test_embedding_service_down_query(async_client, user):
    EMBEDDINGS_POST_PATH = "api.views.aiohttp.ClientSession.post"
    # Create a mock response object with status 500
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.json.return_value = AsyncMock(return_value={"error": "Service Down"})

    # Mock the context manager __aenter__ to return the mock_response
    mock_response.__aenter__.return_value = mock_response

    # Patch the aiohttp.ClientSession.post method to return the mock_response
    with patch(EMBEDDINGS_POST_PATH, return_value=mock_response):
        # Perform the POST request to trigger embed_document
        response = await async_client.post(
            "/search/",
            json={"query": "hello", "top_k": 1},
            headers={"Authorization": f"Bearer {user.token}"},
        )

        assert response.status_code == 503


async def test_embed_document_arxiv_await(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://arxiv.org/pdf/2408.06643v2",
            "wait": True,
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 201


async def test_embed_document_arxiv_async(async_client, user):
    response = await async_client.post(
        "/documents/upsert-document/",
        json={
            "name": "Test Document Fixture",
            "url": "https://arxiv.org/pdf/2408.06643v2",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    assert response.status_code == 202


async def test_document_fetch_failure_await(async_client, user):
    AIOHTTP_GET_PATH = "api.models.aiohttp.ClientSession.get"
    AIOHTTP_HEAD_PATH = "api.models.aiohttp.ClientSession.head"

    # Mock for HEAD request
    mock_head_response = AsyncMock()
    mock_head_response.status = 200
    mock_head_response.headers = {
        "Content-Type": "application/pdf",
        "Content-Length": "1000",
    }
    mock_head_response.__aenter__.return_value = mock_head_response

    # Mock for GET request
    mock_get_response = AsyncMock()
    mock_get_response.status = 500
    mock_get_response.headers = {}
    mock_get_response.read = AsyncMock(return_value=b"")
    mock_get_response.__aenter__.return_value = mock_get_response

    # Patch both HEAD and GET methods
    with patch(AIOHTTP_HEAD_PATH, return_value=mock_head_response) as mock_head, patch(
        AIOHTTP_GET_PATH, return_value=mock_get_response
    ) as mock_get:
        response = await async_client.post(
            "/documents/upsert-document/",
            json={
                "name": "Test Document Fetch Failure",
                "url": "https://example.com/nonexistent.pdf",
                "wait": True,
            },
            headers={"Authorization": f"Bearer {user.token}"},
        )

        # Assert both HEAD and GET were called
        mock_head.assert_called_once_with(
            "https://example.com/nonexistent.pdf", allow_redirects=True
        )
        mock_get.assert_called_once_with("https://example.com/nonexistent.pdf")

        # Assert that the response status code reflects the failure
        assert response.status_code == 400


async def test_document_fetch_failure_async(async_client, user):
    AIOHTTP_GET_PATH = "api.models.aiohttp.ClientSession.get"
    AIOHTTP_HEAD_PATH = "api.models.aiohttp.ClientSession.head"

    # Mock for HEAD request
    mock_head_response = AsyncMock()
    mock_head_response.status = 200
    mock_head_response.headers = {
        "Content-Type": "application/pdf",
        "Content-Length": "1000",
    }
    mock_head_response.__aenter__.return_value = mock_head_response

    # Mock for GET request (failing response)
    mock_get_response = AsyncMock()
    mock_get_response.status = 500
    mock_get_response.headers = {}
    mock_get_response.read = AsyncMock(return_value=b"")
    mock_get_response.__aenter__.return_value = mock_get_response

    # Patch both HEAD and GET methods
    with patch(AIOHTTP_HEAD_PATH, return_value=mock_head_response) as mock_head, patch(
        AIOHTTP_GET_PATH, return_value=mock_get_response
    ) as mock_get:
        # Mock EmailMessage
        with patch("api.views.EmailMessage") as MockEmailMessage:
            mock_email_instance = MockEmailMessage.return_value
            mock_email_instance.send = AsyncMock()

            # Perform the POST request
            response = await async_client.post(
                "/documents/upsert-document/",
                json={
                    "name": "Test Document Fetch Failure",
                    "url": "https://example.com/nonexistent.pdf",
                },
                headers={"Authorization": f"Bearer {user.token}"},
            )

            # Assert that the response status code reflects the async processing
            assert response.status_code == 202

            # Wait for all pending tasks to complete
            pending_tasks = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            await asyncio.gather(*pending_tasks)

            # Assert that both HEAD and GET were called
            mock_head.assert_called_once_with(
                "https://example.com/nonexistent.pdf", allow_redirects=True
            )
            mock_get.assert_called_once_with("https://example.com/nonexistent.pdf")

            # Assert that the email was sent
            MockEmailMessage.assert_called_once_with(
                subject="Document Upsertion Failed",
                body="There was an error processing your document: ['Failed to fetch document from URL']",
                to=[user.email, "dummy@example.com"],
                from_email="dummy@example.com",
            )
            mock_email_instance.send.assert_called_once()


async def test_document_file_too_big(async_client, user):
    AIOHTTP_GET_PATH = "api.models.aiohttp.ClientSession.get"
    AIOHTTP_HEAD_PATH = "api.models.aiohttp.ClientSession.head"
    MAX_SIZE_BYTES = 50 * 1024 * 1024

    # Mock for HEAD request
    mock_head_response = AsyncMock()
    mock_head_response.status = 200
    mock_head_response.headers = {
        "Content-Type": "application/pdf",
        "Content-Length": str(MAX_SIZE_BYTES + 1),  # 50MB + 1 byte
    }
    mock_head_response.__aenter__.return_value = mock_head_response

    # Mock for GET request (shouldn't be called due to size check in HEAD)
    mock_get_response = AsyncMock()
    mock_get_response.status = 200
    mock_get_response.headers = {
        "Content-Length": str(MAX_SIZE_BYTES + 1),
    }
    mock_get_response.read = AsyncMock(return_value=b"x" * (MAX_SIZE_BYTES + 1))
    mock_get_response.__aenter__.return_value = mock_get_response

    # Patch both HEAD and GET methods
    with patch(AIOHTTP_HEAD_PATH, return_value=mock_head_response) as mock_head, patch(
        AIOHTTP_GET_PATH, return_value=mock_get_response
    ) as mock_get:
        response = await async_client.post(
            "/documents/upsert-document/",
            json={
                "name": "Test Document File Too Large",
                "url": "https://example.com/largefile.pdf",
                "wait": True,
            },
            headers={"Authorization": f"Bearer {user.token}"},
        )

        # Assert that HEAD was called
        mock_head.assert_called_once_with(
            "https://example.com/largefile.pdf", allow_redirects=True
        )

        # Assert that GET was never called (should fail at HEAD check)
        mock_get.assert_not_called()

        # Assert that the response status code reflects the failure
        assert response.status_code == 400


async def test_gotenberg_service_down(async_client, user):
    GOTENBERG_POST_PATH = "api.models.aiohttp.ClientSession.post"
    # Create a mock response object with status 500
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.json.return_value = AsyncMock(return_value={"error": "Service Down"})
    # Mock the context manager __aenter__ to return the mock_response
    mock_response.__aenter__.return_value = mock_response
    # Patch the aiohttp.ClientSession.post method to return the mock_response

    # we will use a sample docx to force the gotenberg service to fail
    with open("api/tests/test_docs/sample.docx", "rb") as f:
        # convert the file to base64
        base64_string = base64.b64encode(f.read()).decode("utf-8")

    with patch(GOTENBERG_POST_PATH, return_value=mock_response):
        response = await async_client.post(
            "/documents/upsert-document/",
            json={
                "name": "Test Document Fixture",
                "base64": base64_string,
                "wait": True,
            },
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert response.status_code == 400

    # now we will use a url
    with patch(GOTENBERG_POST_PATH, return_value=mock_response):
        response = await async_client.post(
            "/documents/upsert-document/",
            json={
                "name": "Test Document Fixture",
                "url": "https://gotenberg.dev/docs/getting-started/introduction",
                "wait": True,
            },
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert response.status_code == 400


async def test_prep_document_document_data_too_large():
    # Initialize Document without a URL or base64 (assuming document_data is handled internally)
    doc = Document()

    document_data = b"x" * (51 * 1024 * 1024)  # 51 MB

    # Attempt to prepare the document and expect a ValidationError
    with pytest.raises(DjangoValidationError):
        await doc._prep_document(document_data=document_data)


async def test_prep_document_with_disallowed_extension(collection):
    content = "bad base64 string"
    content_bytes = content.encode("utf-8")
    base64_bytes = base64.b64encode(content_bytes)
    base64_string = base64_bytes.decode("utf-8")
    # give it an .exe extension
    extension = "exe"
    bad_base64 = f"data:application/{extension};base64,{base64_string}"
    document = Document(collection=collection)
    await document.save_base64_to_s3(bad_base64)
    with pytest.raises(DjangoValidationError):
        await document._prep_document()

    await document.delete_s3_file()


async def test_save_base64_to_s3_failure(collection):
    # Create a document instance
    document = Document(name="test document", collection=collection)

    # Create a valid base64 string
    content = "test content"
    content_bytes = content.encode("utf-8")
    base64_bytes = base64.b64encode(content_bytes)
    base64_string = base64_bytes.decode("utf-8")

    # Test S3 save failure
    S3_SAVE_PATH = "django.db.models.fields.files.FieldFile.save"
    with patch(S3_SAVE_PATH) as mock_save:
        mock_save.side_effect = Exception("S3 Storage Error")

        with pytest.raises(DjangoValidationError) as exc_info:
            await document.save_base64_to_s3(base64_string)

        assert str(exc_info.value) == "['Failed to save file to S3: S3 Storage Error']"
        mock_save.assert_called_once()


async def test_unknown_mime_type(collection):
    # Create a document with content that will produce an unknown mime type
    content = bytes([0xFF, 0xFE, 0xFD])  # Some arbitrary bytes
    base64_string = base64.b64encode(content).decode("utf-8")

    document = Document(name="test_unknown_mime", collection=collection)

    # Mock magic to return an unknown mime type
    MAGIC_PATH = "magic.Magic.from_buffer"
    with patch(MAGIC_PATH) as mock_magic:
        mock_magic.return_value = "application/x-unknown-type"

        # First, verify that save_base64_to_s3 saves with .bin extension
        await document.save_base64_to_s3(base64_string)

        # Assert that the filename ends with .bin
        assert document.s3_file.name.endswith(".bin")

        # Then verify that _prep_document raises ValidationError because .bin is not allowed
        with pytest.raises(DjangoValidationError) as exc_info:
            await document._prep_document()

        assert "File extension .bin is not allowed" in str(exc_info.value)

    # Cleanup
    await document.delete_s3_file()
