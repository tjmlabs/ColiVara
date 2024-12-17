import asyncio
import base64
import logging
import re
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import aiohttp
from accounts.models import CustomUser
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.core.mail import EmailMessage
from django.db.models import Count, Prefetch
from django.db.models.query import QuerySet
from django.db.utils import IntegrityError
from django.http.request import HttpRequest
from ninja import File, Router, Schema
from ninja.files import UploadedFile
from ninja.security import HttpBearer
from pgvector.utils import HalfVector
from pydantic import Field, model_validator
from svix.api import (ApplicationIn, EndpointIn, EndpointUpdate, MessageIn,
                      SvixAsync)
from typing_extensions import Self

from .models import Collection, Document, MaxSim, Page

router = Router()


logger = logging.getLogger(__name__)


@router.get("/health/", tags=["health"])
async def health(request) -> Dict[str, str]:
    return {"status": "ok"}


"""AUTHENTICATION"""


class Request(HttpRequest):
    auth: CustomUser


class Bearer(HttpBearer):
    """
    Bearer class for handling HTTP Bearer authentication.

    Methods:
        authenticate(request, token):
            Authenticates the user based on the provided token.
            Args:
                request: The HTTP request object.
                token: The token string used for authentication.
            Returns:
                The authenticated user if the token is valid, otherwise None.
    """

    async def authenticate(
        self, request: HttpRequest, token: str
    ) -> Optional[CustomUser]:
        try:
            user = await CustomUser.objects.aget(token=token)
            return user
        except CustomUser.DoesNotExist:
            return None


"""Collections"""


class CollectionIn(Schema):
    name: str
    metadata: Optional[dict] = Field(default_factory=lambda: {})

    @model_validator(mode="after")
    def validate_name(self) -> Self:
        if self.name.lower() == "all":
            raise ValueError("Collection name 'all' is not allowed.")
        return self


class PatchCollectionIn(Schema):
    name: Optional[str] = None
    # metadata can be Not provided = keep the old metadata
    # emtpy dict = override the metadata with an empty dict
    # dict = update the metadata with the provided dict
    metadata: Optional[dict] = None

    @model_validator(mode="after")
    def validate_name(self) -> Self:
        if self.name and self.name.lower() == "all":
            raise ValueError("Collection name 'all' is not allowed.")
        if not any([self.name, self.metadata]):
            raise ValueError("At least one field must be provided to update.")
        return self


class CollectionOut(Schema):
    id: int
    name: str
    metadata: dict
    num_documents: int


class GenericError(Schema):
    detail: str


class GenericMessage(Schema):
    detail: str


@router.post(
    "/collections/",
    tags=["collections"],
    auth=Bearer(),
    response={201: CollectionOut, 409: GenericError},
)
async def create_collection(
    request: Request, payload: CollectionIn
) -> Tuple[int, CollectionOut] | Tuple[int, GenericError]:
    """
    Create a new collection.

    This endpoint allows the user to create a new collection with the specified name and metadata.

    Args:
        request: The HTTP request object, which includes the user information.
        payload (CollectionIn): The input data for creating the collection, which includes the name and metadata.

    Returns:
        dict: A dictionary containing the ID of the newly created collection and a success message.

    Raises:
        HttpError: If the user already has a collection with the same name.
    """
    try:
        collection = await Collection.objects.acreate(
            name=payload.name, owner=request.auth, metadata=payload.metadata
        )
        return 201, CollectionOut(
            id=collection.id,
            name=collection.name,
            metadata=collection.metadata,
            num_documents=0,
        )
    except IntegrityError:
        return 409, GenericError(
            detail="You already have a collection with this name, did you mean to update it? Use PATCH instead."
        )


@router.get(
    "/collections/", response=List[CollectionOut], tags=["collections"], auth=Bearer()
)
async def list_collections(request: Request) -> List[CollectionOut]:
    """
    Endpoint to list collections.

    This endpoint retrieves a list of collections owned by the authenticated user.

    Args:
        request: The request object containing authentication information.

    Returns:
        A list of CollectionOut objects representing the collections owned by the authenticated user.

    Raises:
        HTTPException: If there is an issue with the request or authentication.
    """
    collections = [
        CollectionOut(
            id=c.id,
            name=c.name,
            metadata=c.metadata,
            num_documents=await c.document_count(),
        )
        async for c in Collection.objects.filter(owner=request.auth)
    ]
    return collections


@router.get(
    "/collections/{collection_name}/",
    response={200: CollectionOut, 404: GenericError},
    tags=["collections"],
    auth=Bearer(),
)
async def get_collection(
    request: Request, collection_name: str
) -> Tuple[int, CollectionOut] | Tuple[int, GenericError]:
    """
    Retrieve a collection by its name.

    Args:
        request: The request object containing authentication information.
        collection_name (str): The name of the collection to retrieve.

    Returns:
        CollectionOut: The retrieved collection with its ID, name, and metadata.

    Raises:
        HTTPException: If the collection is not found or the user is not authorized to access it.

    Endpoint:
        GET /collections/{collection_name}

    Tags:
        collections

    Authentication:
        Bearer token required.
    """
    try:
        collection = await Collection.objects.aget(
            name=collection_name, owner=request.auth
        )
        return 200, CollectionOut(
            id=collection.id,
            name=collection.name,
            metadata=collection.metadata,
            num_documents=await collection.document_count(),
        )
    except Collection.DoesNotExist:
        return 404, GenericError(detail=f"Collection: {collection_name} doesn't exist")


@router.patch(
    "/collections/{collection_name}/",
    tags=["collections"],
    auth=Bearer(),
    response={200: CollectionOut, 404: GenericError},
)
async def partial_update_collection(
    request: Request, collection_name: str, payload: PatchCollectionIn
) -> Tuple[int, CollectionOut] | Tuple[int, GenericError]:
    """
    Partially update a collection.

    This endpoint allows for partial updates to a collection's details. Only the fields provided in the payload will be updated.

    Args:
        request: The request object containing authentication details.
        collection_name (str): The name of the collection to be updated.
        payload (PatchCollectionIn): The payload containing the fields to be updated.

    Returns:
        dict: A message indicating the collection was updated successfully.

    Raises:
        HTTPException: If the collection is not found or the user is not authorized to update it.
    """
    try:
        collection = await Collection.objects.aget(
            name=collection_name, owner=request.auth
        )
    except Collection.DoesNotExist:
        return 404, GenericError(detail=f"Collection: {collection_name} doesn't exist")

    collection.name = payload.name or collection.name

    if payload.metadata is not None:
        collection.metadata = payload.metadata

    await collection.asave()
    return 200, CollectionOut(
        id=collection.id,
        name=collection.name,
        metadata=collection.metadata,
        num_documents=await collection.document_count(),
    )


@router.delete(
    "/collections/{collection_name}/",
    tags=["collections"],
    auth=Bearer(),
    response={204: None, 404: GenericError},
)
async def delete_collection(
    request: Request, collection_name: str
) -> Tuple[int, None] | Tuple[int, GenericError]:
    """
    Delete a collection by its name.

    This endpoint deletes a collection specified by the `collection_name` parameter.
    The collection must belong to the authenticated user.

    Args:
        request: The HTTP request object, which includes authentication information.
        collection_id (int): The ID of the collection to be deleted.

    Returns:
        dict: A message indicating that the collection was deleted successfully.

    Raises:
        HTTPException: If the collection does not exist or does not belong to the authenticated user.
    """
    try:
        collection = await Collection.objects.aget(
            name=collection_name, owner=request.auth
        )
    except Collection.DoesNotExist:
        return 404, GenericError(detail=f"Collection: {collection_name} doesn't exist")

    await collection.adelete()
    return 204, None


"""Documents"""


class DocumentIn(Schema):
    name: str
    metadata: dict = Field(default_factory=dict)
    collection_name: str = Field(
        "default_collection",
        description="""The name of the collection to which the document belongs. If not provided, the document will be added to the default_collection. Use 'all' to access all collections belonging to the user.""",
    )
    url: Optional[str] = None
    base64: Optional[str] = None
    wait: Optional[bool] = False
    use_proxy: Optional[bool] = False

    @model_validator(mode="after")
    def base64_or_url(self) -> Self:
        if not self.url and not self.base64:
            raise ValueError("Either 'url' or 'base64' must be provided.")
        if self.url and self.base64:
            raise ValueError("Only one of 'url' or 'base64' should be provided.")

        # Validate base64
        if self.base64:
            base64_pattern = r"^[A-Za-z0-9+/]+={0,2}$"
            is_base64 = (
                re.match(base64_pattern, self.base64) and len(self.base64) % 4 == 0
            )

            if not is_base64:
                raise ValueError(
                    "Provided 'base64' is not valid. Please provide a valid base64 string."
                )

        # Validate URL
        if self.url:
            parsed = urlparse(self.url)
            is_url = all([parsed.scheme, parsed.netloc])

            if not is_url:
                raise ValueError(
                    "Provided 'url' is not valid. Please provide a valid URL."
                )
        return self


class PageOut(Schema):
    document_name: Optional[str] = None
    img_base64: str
    page_number: int


class DocumentOut(Schema):
    id: int
    name: str
    metadata: dict = Field(default_factory=dict)
    url: Optional[str] = None
    num_pages: int
    collection_name: str
    pages: Optional[List[PageOut]] = None


class DocumentInPatch(Schema):
    name: Optional[str] = None
    metadata: Optional[dict] = Field(default_factory=lambda: {})
    collection_name: Optional[str] = Field(
        "default_collection",
        description="""The name of the collection to which the document belongs. If not provided, the document will be added to the default_collection. Use 'all' to access all collections belonging to the user.""",
    )
    url: Optional[str] = None
    base64: Optional[str] = None
    use_proxy: Optional[bool] = False

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not any([self.name, self.metadata, self.url, self.base64]):
            raise ValueError("At least one field must be provided to update.")
        if self.url and self.base64:
            raise ValueError("Only one of 'url' or 'base64' should be provided.")
        return self


async def process_upsert_document(
    request: Request, payload: DocumentIn
) -> Tuple[int, DocumentOut] | Tuple[int, GenericError]:
    collection, _ = await Collection.objects.aget_or_create(
        name=payload.collection_name, owner=request.auth
    )
    try:
        available_credits = await sync_to_async(request.auth.get_available_credits)()
        logger.info(f"Available credits: {available_credits}")

        if available_credits < 1 and request.auth.tier == "free":
            # raise an exception so it's caught by the error handler
            error_message = (
                "You have no available credits. Please upgrade your subscription."
            )
            if payload.wait:
                return 402, GenericError(detail=error_message)
            else:
                raise ValueError(error_message)

        # we look up the document by name and collection
        # if it exists, we update its metadate and embeddings (by calling embed_document)
        exists = await Document.objects.filter(
            name=payload.name, collection=collection
        ).aexists()
        if exists:
            logger.info(
                f"Document {payload.name} already exists, updating metadata and embeddings."
            )
            # we update the metadata and embeddings
            document = await Document.objects.aget(
                name=payload.name, collection=collection
            )
            document.metadata = payload.metadata
            document.url = payload.url or ""
            # we delete the old pages, since we will re-embed the document
            await document.pages.all().adelete()
        else:
            logger.info(f"Document {payload.name} does not exist, creating it.")
            # we create a new document
            document = Document(
                name=payload.name,
                metadata=payload.metadata,
                collection=collection,
                url=payload.url or "",
            )
        if payload.base64:
            await document.save_base64_to_s3(payload.base64)

        # this method will embed the document and save it to the database
        await document.embed_document(payload.use_proxy)
        document = (
            await Document.objects.select_related("collection")
            .annotate(num_pages=Count("pages"))
            .aget(
                id=document.id,
            )
        )
        logger.info(f"Document {document.name} processed successfully.")

        if payload.use_proxy:
            await consume_credits(
                request,
                available_credits,
                document.num_pages + 10,
                document.num_pages,
                True,
                document.name,
                "upsert",
            )  # 10 extra credits for proxy usage
        else:
            await consume_credits(
                request,
                available_credits,
                document.num_pages,
                document.num_pages,
                False,
                document.name,
                "upsert",
            )

        if (
            not payload.wait
            and request.auth.svix_application_id
            and settings.SVIX_TOKEN != ""
        ):
            # send an event to the webhook
            svix = SvixAsync(settings.SVIX_TOKEN)
            await svix.message.create(
                request.auth.svix_application_id,
                MessageIn(
                    event_type="upsert.success",
                    payload={
                        "type": "upsert.success",
                        "message": "Document upserted successfully",
                        "id": document.id,
                        "name": document.name,
                        "metadata": document.metadata,
                        "url": await document.get_url(),
                        "num_pages": document.num_pages,
                        "collection_name": document.collection.name,
                    },
                ),
            )

        return 201, DocumentOut(
            id=document.id,
            name=document.name,
            metadata=document.metadata,
            url=await document.get_url(),
            num_pages=document.num_pages,
            collection_name=document.collection.name,
        )

    except Exception as e:
        logger.error(f"Error processing document: {str(e)}")

        if (
            not payload.wait
            and request.auth.svix_application_id
            and settings.SVIX_TOKEN != ""
        ):
            # send an event to the webhook
            svix = SvixAsync(settings.SVIX_TOKEN)
            await svix.message.create(
                request.auth.svix_application_id,
                MessageIn(
                    event_type="upsert.fail",
                    payload={
                        "type": "upsert.fail",
                        "message": "There was an error processing your document",
                        "name": payload.name,
                        "metadata": payload.metadata,
                        "collection_name": payload.collection_name,
                        "error": str(e),
                    },
                ),
            )
        elif (
            not payload.wait
        ):  # only send an email in the async case and if there's no webhook
            user_email = request.auth.email
            admin_email = settings.ADMINS[0][1]
            from_email = settings.DEFAULT_FROM_EMAIL

            to = [user_email]
            email = EmailMessage(
                subject="Document Upsertion Failed",
                body=f"There was an error processing your document: {str(e)}",
                to=to,
                bcc=[admin_email],
                from_email=from_email,
            )
            email.content_subtype = "html"
            email.send()

        return 400, GenericError(detail=str(e))


@router.post(
    "/documents/upsert-document/",
    tags=["documents"],
    auth=Bearer(),
    response={
        201: DocumentOut,
        202: GenericMessage,
        400: GenericError,
        402: GenericError,
    },
)
async def upsert_document(
    request: Request, payload: DocumentIn
) -> Tuple[int, DocumentOut] | Tuple[int, GenericError] | Tuple[int, GenericMessage]:
    """
    Create or update a document in a collection. Average latency is 7 seconds per page.

    This endpoint allows the user to create or update a document in a collection.
    The document can be provided as a URL or a base64-encoded string.
    if the collection is not provided, a collection named "default_collection" will be used.
    if the collection is provided, it will be created if it does not exist.

    Args:
        request: The HTTP request object, which includes the user information.
        payload (DocumentIn): The input data for creating or updating the document.

    Returns:
        str: A message indicating that the document is being processed.

    Raises:
        HttpError: If the document cannot be created or updated.

    Example:
        POST /documents/upsert-document/
        {
            "name": "my_document",
            "metadata": {"author": "John Doe"},
            "collection": "my_collection",
            "url": "https://example.com/my_document.pdf,
            "wait": true # optional, if true, the response will be sent after waiting for the document to be processed. Otherwise, it will be done asynchronously.
        }
    """
    # if the user didn't give a collection, payload.collection will be "default_collection"
    if payload.collection_name == "all":
        return 400, GenericError(
            detail="The collection name 'all' is not allowed when inserting or updating a document - only when retrieving. Please provide a valid collection name."
        )

    if payload.wait:
        return await process_upsert_document(request, payload)
    else:
        # Schedule the background task
        asyncio.create_task(process_upsert_document(request, payload))
        return 202, GenericMessage(
            detail="Document is being processed in the background."
        )


@router.get(
    "documents/{document_name}/",
    tags=["documents"],
    auth=Bearer(),
    response={200: DocumentOut, 404: GenericError, 409: GenericError},
)
async def get_document(
    request: Request,
    document_name: str,
    collection_name: Optional[str] = "default_collection",
    expand: Optional[str] = None,
) -> Tuple[int, DocumentOut] | Tuple[int, GenericError]:
    """
    Retrieve a specific document from the user documents.
    Default collection is "default_collection".
    To get all documents, use collection_name="all".

    Args:
        request: The HTTP request object.
        document_name (str): The ID of the document to retrieve.
        expand (Optional[str]): A comma-separated list of fields to expand in the response.
                                If "pages" is included, the document's pages will be included.

    Returns:
        DocumentOut: The retrieved document with its details.

    Raises:
        HTTPException: If the document or collection is not found.

    Example:
        GET /documents/{document_name}/?collection_name={collection_name}&expand=pages
    """
    try:
        query = Document.objects.select_related("collection").annotate(
            num_pages=Count("pages")
        )
        if collection_name == "all":
            document = await query.aget(
                name=document_name, collection__owner=request.auth
            )
        else:
            document = await query.aget(
                name=document_name,
                collection__name=collection_name,
                collection__owner=request.auth,
            )
    except Document.DoesNotExist:
        return 404, GenericError(
            detail=f"Document: {document_name} doesn't exist in your collections."
        )
    except Document.MultipleObjectsReturned:
        return 409, GenericError(
            detail=f"Multiple documents with the name: {document_name} exist in your collections. Please specify a collection."
        )

    document_out = DocumentOut(
        id=document.id,
        name=document.name,
        metadata=document.metadata,
        url=await document.get_url(),
        num_pages=document.num_pages,
        collection_name=document.collection.name,
    )

    if expand and "pages" in expand.split(","):
        document_out.pages = [
            PageOut(
                document_name=document.name,
                img_base64=page.img_base64,
                page_number=page.page_number,
            )
            async for page in document.pages.all()
        ]

    return 200, document_out


@router.get(
    "/documents/",
    tags=["documents"],
    auth=Bearer(),
    response=List[DocumentOut],
)
async def list_documents(
    request: Request,
    collection_name: Optional[str] = "default_collection",
    expand: Optional[str] = None,
) -> List[DocumentOut]:
    """
    Fetch a list of documents for a given collection.

    This endpoint retrieves documents associated with a specified collection name.
    Optionally, it can expand the response to include pages of each document.

    Args:
        request (Request): The request object.
        collection_name (Optional[str]): The name of the collection to fetch documents from. Defaults to "default_collection". Use "all" to fetch documents from all collections.
        expand (Optional[str]): A comma-separated string specifying additional fields to include in the response.
                                If "pages" is included, the pages of each document will be included.

    Returns:
        List[DocumentOut]: A list of documents with their details. If expanded, includes pages of each document.

    Raises:
        HTTPException: If the collection or documents are not found.

    Example:
        GET /documents/?collection_name=default_collection&expand=pages
    """
    expand_fields = expand.split(",") if expand else []

    query = Document.objects.select_related("collection").annotate(
        num_pages=Count("pages")
    )

    if "pages" in expand_fields:
        query = query.prefetch_related(
            Prefetch("pages", queryset=Page.objects.order_by("page_number"))
        )

    if collection_name == "all":
        query = query.filter(collection__owner=request.auth)
    else:
        query = query.filter(
            collection__name=collection_name, collection__owner=request.auth
        )

    documents = []
    async for doc in query:
        document = DocumentOut(
            id=doc.id,
            name=doc.name,
            metadata=doc.metadata,
            url=await doc.get_url(),
            num_pages=doc.num_pages,
            collection_name=doc.collection.name,
        )

        if "pages" in expand_fields:
            document.pages = [
                PageOut(
                    document_name=doc.name,
                    img_base64=page.img_base64,
                    page_number=page.page_number,
                )
                for page in doc.pages.all()
            ]

        documents.append(document)

    return documents


@router.patch(
    "documents/{document_name}/",
    tags=["documents"],
    auth=Bearer(),
    response={
        200: DocumentOut,
        404: GenericError,
        409: GenericError,
        402: GenericError,
    },
)
async def partial_update_document(
    request: Request, document_name: str, payload: DocumentInPatch
) -> Tuple[int, DocumentOut] | Tuple[int, GenericError]:
    """
    Partially update a document.

    This endpoint allows for partial updates to a document's details. Only the fields provided in the payload will be updated.
    If the URL is changed or base64 is provided, the document will be re-embedded. Otherwise, only the metadata and name will be updated.

    Args:
        request: The request object containing authentication details.
        document_name (str): The name of the document to be updated.
        payload (DocumentInPatch): The payload containing the fields to be updated.

    Returns:
        Tuple[int, DocumentOut] | Tuple[int, GenericError]: A tuple containing the status code and the updated document or an error message.

    Raises:
        HTTPException: If the document is not found or the user is not authorized to update it.
    """
    collection_name = payload.collection_name
    try:
        query = Document.objects.select_related("collection")
        if collection_name == "all":
            document = await query.aget(
                name=document_name, collection__owner=request.auth
            )
        else:
            document = await query.aget(
                name=document_name,
                collection__name=collection_name,
                collection__owner=request.auth,
            )
    except Document.DoesNotExist:
        return 404, GenericError(
            detail=f"Document: {document_name} doesn't exist in your collections."
        )
    except Document.MultipleObjectsReturned:
        return 409, GenericError(
            detail=f"Multiple documents with the name: {document_name} exist in your collections. Please specify a collection."
        )
    available_credits = await sync_to_async(request.auth.get_available_credits)()

    if available_credits < 1 and request.auth.tier == "free":
        return 402, GenericError(
            detail="You have no available credits. Please upgrade your subscription."
        )

    if payload.url and payload.url != document.url:
        document.url = payload.url
        document.metadata = payload.metadata or document.metadata
        document.name = payload.name or document.name
        # we want to delete the old pages, since we will re-embed the document
        await document.pages.all().adelete()
        await document.embed_document(payload.use_proxy)
        record_usage = True

    elif payload.base64:
        document.metadata = payload.metadata or document.metadata
        document.name = payload.name or document.name
        await document.save_base64_to_s3(payload.base64)
        await document.pages.all().adelete()
        await document.embed_document(payload.use_proxy)
        record_usage = True

    else:
        document.name = payload.name or document.name
        document.metadata = payload.metadata or document.metadata
        await document.asave()
        record_usage = False

    new_document = (
        await Document.objects.select_related("collection")
        .annotate(num_pages=Count("pages"))
        .aget(id=document.id)
    )
    logger.info(f"Document {new_document.name} updated successfully.")
    if record_usage:
        if payload.use_proxy:
            await consume_credits(
                request,
                available_credits,
                new_document.num_pages + 10,
                document.num_pages,
                True,
                document.name,
                "patch",
            )  # 10 extra credits for proxy usage
        else:
            await consume_credits(
                request,
                available_credits,
                new_document.num_pages,
                document.num_pages,
                False,
                document.name,
                "patch",
            )
    return 200, DocumentOut(
        id=new_document.id,
        name=new_document.name,
        metadata=new_document.metadata,
        url=await new_document.get_url(),
        num_pages=new_document.num_pages,
        collection_name=new_document.collection.name,
    )


@router.delete(
    "/documents/delete-document/{document_name}/",
    tags=["documents"],
    auth=Bearer(),
    response={204: None, 404: GenericError, 409: GenericError},
)
async def delete_document(
    request: Request,
    document_name: str,
    collection_name: Optional[str] = "default_collection",
) -> Tuple[int, None] | Tuple[int, GenericError]:
    """
    Delete a document by its Name.

    This endpoint deletes a document specified by the `document_name` parameter.
    The document must belong to the authenticated user.

    Args:
        request: The HTTP request object, which includes authentication information.
        collection_name (name): The name of the collection containing the document. Defaults to "default_collection". Use "all" to access all collections belonging to the user.
        document_name (int): The name of the document to be deleted.

    Returns:
        dict: A message indicating that the document was deleted successfully.

    Raises:
        HTTPException: If the document does not exist or does not belong to the authenticated user.

    Example:
        DELETE /documents/delete-document/{document_name}/?collection_name={collection_name}
    """
    try:
        query = Document.objects.select_related("collection")
        if collection_name == "all":
            document = await query.aget(
                name=document_name, collection__owner=request.auth
            )
        else:
            document = await query.aget(
                name=document_name,
                collection__name=collection_name,
                collection__owner=request.auth,
            )
    except Document.DoesNotExist:
        return 404, GenericError(
            detail=f"Document: {document_name} doesn't exist in your collections."
        )
    except Document.MultipleObjectsReturned:
        return 409, GenericError(
            detail=f"Multiple documents with the name: {document_name} exist in your collections. Please specify a collection"
        )
    await document.adelete()
    return 204, None


""" Search """


class QueryFilter(Schema):
    class onEnum(str, Enum):
        document = "document"
        collection = "collection"

    class lookupEnum(str, Enum):
        key_lookup = "key_lookup"
        contains = "contains"
        contained_by = "contained_by"
        has_key = "has_key"
        has_keys = "has_keys"
        has_any_keys = "has_any_keys"

    on: onEnum = onEnum.document
    # key is a str or a list of str
    key: Union[str, List[str]]
    # value can be any - we can accept int, float, str, bool
    value: Optional[Union[str, int, float, bool]] = None
    lookup: lookupEnum = lookupEnum.key_lookup

    # validation rules:
    # 1. if looks up is contains or contained_by, value must be a string, and key must be a string
    # 2. if lookup is has_keys, or has_any_keys, key must be a list of strings - we can transform automatically - value must be None
    # 3. if lookup is has_key, key must be a string, value must be None
    @model_validator(mode="after")
    def validate_filter(self) -> Self:
        if self.lookup in ["contains", "contained_by", "key_lookup"]:
            if not isinstance(self.key, str):
                raise ValueError("Key must be a string.")
            if self.value is None:
                raise ValueError("Value must be provided.")
        if self.lookup in ["has_key"]:
            if not isinstance(self.key, str):
                raise ValueError("Key must be a string.")
            if self.value is not None:
                raise ValueError("Value must be None.")
        if self.lookup in ["has_keys", "has_any_keys"]:
            if not isinstance(self.key, list):
                raise ValueError("Key must be a list of strings.")
            if self.value is not None:
                raise ValueError("Value must be None.")
        return self


class QueryIn(Schema):
    query: str
    collection_name: Optional[str] = "all"
    top_k: Optional[int] = 3
    query_filter: Optional[QueryFilter] = None


class PageOutQuery(Schema):
    collection_name: str
    collection_id: int
    collection_metadata: Optional[dict] = {}
    document_name: str
    document_id: int
    document_metadata: Optional[dict] = {}
    page_number: int
    raw_score: float
    normalized_score: float
    img_base64: str


class QueryOut(Schema):
    query: str
    results: List[PageOutQuery]


@router.post(
    "/search/",
    tags=["search"],
    auth=Bearer(),
    response={200: QueryOut, 503: GenericError, 402: GenericError},
)
async def search(
    request: Request, payload: QueryIn
) -> Tuple[int, QueryOut] | Tuple[int, GenericError]:
    """
    Search for pages similar to a given query.

    This endpoint allows the user to search for pages similar to a given query.
    The search is performed across all documents in the specified collection.

    Args:
        request: The HTTP request object, which includes the user information.
        payload (QueryIn): The input data for the search, which includes the query string and collection ID.

    Returns:
        QueryOut: The search results, including the query and a list of similar pages.

    Raises:
        HttpError: If the collection does not exist or the query is invalid.

    Example:
        POST /search/
        {
            "query": "dog",
            "collection_name": "my_collection",
            "top_k": 3,
            "query_filter": {
                "on": "document",
                "key": "breed",
                "value": "collie",
                "lookup": "contains"
            }
        }
    """
    available_credits = await sync_to_async(request.auth.get_available_credits)()
    if available_credits < 1 and request.auth.tier == "free":
        return 402, GenericError(
            detail="You have no available credits. Please upgrade your subscription."
        )

    query_embeddings = await get_query_embeddings(payload.query)
    if not query_embeddings:
        return 503, GenericError(
            detail="Failed to get embeddings from the embeddings service"
        )
    query_length = len(query_embeddings)  # we need this for normalization

    # we want to cast the embeddings to halfvec
    casted_query_embeddings = [
        HalfVector(embedding).to_text() for embedding in query_embeddings
    ]

    # building the query:

    # 1. filter the pages based on the collection_id and the query_filter
    base_query = await filter_query(payload, request.auth)

    # 2. annotate the query with the max sim score
    # maxsim needs 2 arrays of embeddings, one for the pages and one for the query
    pages_query = (
        base_query.annotate(page_embeddings=ArrayAgg("embeddings__embedding"))
        .annotate(max_sim=MaxSim("page_embeddings", casted_query_embeddings))
        .order_by("-max_sim")[: payload.top_k or 3]
    )
    # 3. execute the query
    results = pages_query.values(
        "id",
        "page_number",
        "img_base64",
        "document__id",
        "document__name",
        "document__metadata",
        "document__collection__id",
        "document__collection__name",
        "document__collection__metadata",
        "max_sim",
    )
    # Normalization
    normalization_factor = query_length

    # Format the results
    formatted_results = [
        PageOutQuery(
            collection_name=row["document__collection__name"],
            collection_id=row["document__collection__id"],
            collection_metadata=(
                row["document__collection__metadata"]
                if row["document__collection__metadata"]
                else {}
            ),
            document_name=row["document__name"],
            document_id=row["document__id"],
            document_metadata=(
                row["document__metadata"] if row["document__metadata"] else {}
            ),
            page_number=row["page_number"],
            raw_score=row["max_sim"],
            normalized_score=row["max_sim"] / normalization_factor,
            img_base64=row["img_base64"],
        )
        async for row in results
    ]

    await consume_credits(request, available_credits, 1, 1, False, "N/A", "search")

    return 200, QueryOut(query=payload.query, results=formatted_results)


@router.post(
    "/filter/",
    tags=["filter"],
    auth=Bearer(),
    response={200: Union[List[DocumentOut], List[CollectionOut]], 503: GenericError},
)
async def filter(
    request: Request,
    payload: QueryFilter,
    expand: Optional[str] = None,
) -> Union[
    Tuple[int, List[DocumentOut]],
    Tuple[int, List[CollectionOut]],
    Tuple[int, GenericError],
]:
    """
    Filter for documents and collections that meet the criteria of the filter.

    Args:
        request: The HTTP request object, which includes the user information.
        payload (QueryFilter): The input data for the filter, which includes the filter criteria.
        expand (Optional[str]): A comma-separated list of fields to expand in the response. If "pages" is included, the document's pages will be included.

    Returns:
        DocumentOut: The retrieved documents with their details.
        CollectionOut: The retrieved collections with their details.

    Raises:
        HttpError: If the collection does not exist or the query is invalid.

    Example:
        POST /filter/?expand=pages
        {
            "on": "document",
            "key": "breed",
            "value": "collie",
            "lookup": "contains"
        }
    """
    available_credits = await sync_to_async(request.auth.get_available_credits)()
    if available_credits < 1 and request.auth.tier == "free":
        return 402, GenericError(
            detail="You have no available credits. Please upgrade your subscription."
        )

    # filter the documents or collections based on the payload
    base_query: QuerySet[Union[Document, Collection]]

    if payload.on == "document":
        base_query = await filter_documents(payload, request.auth)
        documents = []

        async for doc in base_query:
            document_out = DocumentOut(
                id=doc.id,
                name=doc.name,
                metadata=doc.metadata,
                url=await doc.get_url(),
                num_pages=await doc.page_count(),
                collection_name=doc.collection.name,
            )

            if expand and "pages" in expand.split(","):
                document_out.pages = [
                    PageOut(
                        document_name=doc.name,
                        img_base64=page.img_base64,
                        page_number=page.page_number,
                    )
                    async for page in doc.pages.all()
                ]

            documents.append(document_out)

        await consume_credits(request, available_credits, 1, 1, False, "N/A", "filter")
        return 200, documents
    else:
        base_query = await filter_collections(payload, request.auth)
        collections = [
            CollectionOut(
                id=col.id,
                name=col.name,
                metadata=col.metadata,
                num_documents=await col.document_count(),
            )
            async for col in base_query
        ]

        await consume_credits(request, available_credits, 1, 1, False, "N/A", "filter")
        return 200, collections


async def get_query_embeddings(query: str) -> List:
    EMBEDDINGS_URL = settings.ALWAYS_ON_EMBEDDINGS_URL
    embed_token = settings.EMBEDDINGS_URL_TOKEN
    headers = {"Authorization": f"Bearer {embed_token}"}
    payload = {
        "input": {
            "task": "query",
            "input_data": [query],
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            EMBEDDINGS_URL, json=payload, headers=headers
        ) as response:
            if response.status != 200:
                logger.error(
                    f"Failed to get embeddings from the embeddings service: {response.status}"
                )
                return []
            out = await response.json()
            # returning  a dynamic array of embeddings, each of which is a list of 128 floats
            # example: [[0.1, 0.2, 0.3, ...], [0.4, 0.5, 0.6, ...]]
            return out["output"]["data"][0]["embedding"]


async def filter_query(payload: QueryIn, user: CustomUser) -> QuerySet[Page]:
    base_query = Page.objects.select_related("document__collection")
    if payload.collection_name == "all":
        base_query = base_query.filter(document__collection__owner=user)
    else:
        base_query = base_query.filter(
            document__collection__owner=user,
            document__collection__name=payload.collection_name,
        )

    if payload.query_filter:
        on = payload.query_filter.on
        key = payload.query_filter.key
        value = payload.query_filter.value
        lookup = payload.query_filter.lookup
        field_prefix = (
            "document__collection__metadata"
            if on == "collection"
            else "document__metadata"
        )
        lookup_operations = {
            "key_lookup": lambda k, v: {f"{field_prefix}__{k}": v},
            "contains": lambda k, v: {f"{field_prefix}__contains": {k: v}},
            "contained_by": lambda k, v: {f"{field_prefix}__contained_by": {k: v}},
            "has_key": lambda k, _: {f"{field_prefix}__has_key": k},
            "has_keys": lambda k, _: {f"{field_prefix}__has_keys": k},
            "has_any_keys": lambda k, _: {f"{field_prefix}__has_any_keys": k},
        }
        filter_params = lookup_operations[lookup](key, value)
        base_query = base_query.filter(**filter_params)
    return base_query


async def filter_documents(
    query_filter: QueryFilter, user: CustomUser
) -> QuerySet[Document]:
    base_query = Document.objects.select_related("collection")
    base_query = base_query.filter(collection__owner=user)

    field_prefix = "metadata"

    lookup_operations = {
        "key_lookup": lambda k, v: {f"{field_prefix}__{k}": v},
        "contains": lambda k, v: {f"{field_prefix}__contains": {k: v}},
        "contained_by": lambda k, v: {f"{field_prefix}__contained_by": {k: v}},
        "has_key": lambda k, _: {f"{field_prefix}__has_key": k},
        "has_keys": lambda k, _: {f"{field_prefix}__has_keys": k},
        "has_any_keys": lambda k, _: {f"{field_prefix}__has_any_keys": k},
    }

    key = query_filter.key
    value = query_filter.value
    lookup = query_filter.lookup

    filter_params = lookup_operations[lookup](key, value)
    base_query = base_query.filter(**filter_params)

    return base_query


async def filter_collections(
    query_filter: QueryFilter, user: CustomUser
) -> QuerySet[Collection]:
    base_query = Collection.objects.all().filter(owner=user)

    field_prefix = "metadata"

    lookup_operations = {
        "key_lookup": lambda k, v: {f"{field_prefix}__{k}": v},
        "contains": lambda k, v: {f"{field_prefix}__contains": {k: v}},
        "contained_by": lambda k, v: {f"{field_prefix}__contained_by": {k: v}},
        "has_key": lambda k, _: {f"{field_prefix}__has_key": k},
        "has_keys": lambda k, _: {f"{field_prefix}__has_keys": k},
        "has_any_keys": lambda k, _: {f"{field_prefix}__has_any_keys": k},
    }

    key = query_filter.key
    value = query_filter.value
    lookup = query_filter.lookup

    filter_params = lookup_operations[lookup](key, value)
    base_query = base_query.filter(**filter_params)

    return base_query


""" helpers """


class FileOut(Schema):
    img_base64: str
    page_number: int


@router.post(
    "helpers/file-to-imgbase64/",
    tags=["helpers"],
    response=List[FileOut],
    auth=Bearer(),
)
async def file_to_imgbase64(request, file: UploadedFile = File(...)) -> List[FileOut]:
    """
    Upload one file, converts to images and return their base64 encoded strings with 1-indexed page numberss.

    Args:
        request: The HTTP request object.
        file UploadedFile): One uploaded file

    Returns:
        List[FileOut]: A list of FileOut objects containing the base64 encoded strings of the images.
    """
    document_data = file.read()
    document = Document()
    img_base64 = await document._prep_document(document_data)
    results = []
    for i, img in enumerate(img_base64):
        results.append(FileOut(img_base64=img, page_number=i + 1))
    return results


@router.post("helpers/file-to-base64/", tags=["helpers"], auth=Bearer())
async def file_to_base64(request, file: UploadedFile = File(...)) -> Dict[str, str]:
    """
    Upload one file, converts to base64 encoded strings.

    Args:
        request: The HTTP request object.
        file UploadedFile): One uploaded file

    Returns:
    str: base64 encoded string of the file.
    """
    document_data = file.read()
    return {"data": base64.b64encode(document_data).decode()}


""" Embeddings """


class TaskEnum(str, Enum):
    image = "image"
    query = "query"


class EmbeddingsIn(Schema):
    input_data: List[str]
    task: TaskEnum

    @model_validator(mode="after")
    def validate_input_data(self) -> Self:
        if self.task == TaskEnum.image:
            for value in self.input_data:
                # Validate base64
                base64_pattern = r"^[A-Za-z0-9+/]+={0,2}$"
                is_base64 = re.match(base64_pattern, value) and len(value) % 4 == 0

                # Validate URL
                parsed = urlparse(value)
                is_url = all([parsed.scheme, parsed.netloc])

                if not (is_base64 or is_url):
                    raise ValueError(
                        "Each input must be a valid base64 string or a URL. Please use our Python SDK if you want to provide a file path."
                    )
        return self


class EmbeddingsOut(Schema):
    _object: str
    data: List[dict]
    model: str
    usage: dict


@router.post(
    "/embeddings/",
    tags=["embeddings"],
    auth=Bearer(),
    response={200: EmbeddingsOut, 503: GenericError, 402: GenericError},
)
async def embeddings(
    request: Request, payload: EmbeddingsIn
) -> Tuple[int, EmbeddingsOut] | Tuple[int, GenericError]:
    """
    Embed a list of documents.

    This endpoint allows the user to embed a list of documents.

    Args:
        request: The HTTP request object, which includes the user information.
        payload (EmbeddingsIn): The input data for embedding the documents.

    Returns:
        EmbeddingsOut: The embeddings of the documents and metadata.

    Raises:
        HttpError: If the documents cannot be embedded.
    """
    available_credits = await sync_to_async(request.auth.get_available_credits)()
    num_pages = len(payload.input_data)
    # case: free user with not enough first time grant credits
    if available_credits < num_pages and request.auth.tier == "free":
        return 402, GenericError(
            detail="You have no available credits. Please upgrade your subscription."
        )
    EMBEDDINGS_URL = settings.ALWAYS_ON_EMBEDDINGS_URL
    embed_token = settings.EMBEDDINGS_URL_TOKEN
    headers = {"Authorization": f"Bearer {embed_token}"}
    task = payload.task
    input_data = payload.input_data
    embed_payload = {
        "input": {
            "task": task,
            "input_data": input_data,
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            EMBEDDINGS_URL, json=embed_payload, headers=headers
        ) as response:
            if response.status != 200:
                return 503, GenericError(
                    detail="Failed to get embeddings from the embeddings service"
                )
            response_data = await response.json()
            output_data = response_data["output"]
            # change object to _object
            output_data["_object"] = output_data.pop("object")

        await consume_credits(
            request, available_credits, num_pages, num_pages, False, "N/A", "embeddings"
        )

        return 200, EmbeddingsOut(**output_data)


async def consume_credits(
    request,
    available_credits,
    consumed_credits,
    num_pages,
    used_proxy,
    filename,
    request_type,
):
    logger.info(f"Current available credits: {available_credits}")

    # available credit is a 100 one time grant, so, we keep using this until it runs out, without hitting stripe
    if available_credits >= consumed_credits:
        await sync_to_async(request.auth.set_available_credits)(
            available_credits - consumed_credits
        )
        logger.info(f"Decreased available credits by {consumed_credits}")
    else:
        await sync_to_async(request.auth.record_consumed_credits)(
            consumed_credits - available_credits
        )

        if available_credits != 0:
            await sync_to_async(request.auth.set_available_credits)(0)
        logger.info(
            f"Set available credits to 0 and recorded {consumed_credits - available_credits} consumed credits"
        )

    # track credit usage
    await sync_to_async(request.auth.record_credit_usage)(
        request_type, consumed_credits, num_pages, used_proxy, filename
    )


""" Webhooks """


class WebhookIn(Schema):
    url: str


class WebhookOut(Schema):
    app_id: str
    endpoint_id: str
    webhook_secret: str


@router.post(
    "/webhook/",
    auth=Bearer(),
    tags=["webhook"],
    response={200: WebhookOut, 400: GenericError},
)
async def add_webhook(
    request: Request, payload: WebhookIn
) -> Tuple[int, GenericError] | Tuple[int, WebhookOut]:
    """
    Add a webhook to the service.

    This endpoint allows the user to add a webhook to the service. The webhook will be called when a document is upserted
    with the upsertion status.

    Events are document upsert successful, document upsert failed.

    Args:
        request: The HTTP request object, which includes the user information.
        url (str): The URL of the webhook.

    Returns:
        A message indicating that the webhook was added successfully.

    Raises:
        HttpError: If the webhook is invalid.
    """
    try:
        # Throw an error if SVIX_TOKEN is not set
        if settings.SVIX_TOKEN == "":
            raise ValueError(
                "SVIX_TOKEN is not set in the environment variables. Please set it before adding a webhook."
            )

        svix = SvixAsync(settings.SVIX_TOKEN)

        if not request.auth.svix_application_id:
            app = await svix.application.create(ApplicationIn(name=request.auth.email))
            app_id = app.id
        else:
            app_id = request.auth.svix_application_id

        if request.auth.svix_endpoint_id:
            # update the webhook
            endpoint_out = await svix.endpoint.update(
                app_id,
                request.auth.svix_endpoint_id,
                EndpointUpdate(
                    url=payload.url,
                ),
            )
        else:
            # create the webhook
            endpoint_out = await svix.endpoint.create(
                app_id,
                EndpointIn(
                    url=payload.url,
                    version=1,
                    description="User webhook",
                ),
            )

        # save the app_id and endpoint_id to the user
        request.auth.svix_application_id = app_id
        request.auth.svix_endpoint_id = endpoint_out.id
        await request.auth.asave()

        endpoint_secret_out = await svix.endpoint.get_secret(app_id, endpoint_out.id)

        return 200, WebhookOut(
            app_id=app_id,
            endpoint_id=endpoint_out.id,
            webhook_secret=endpoint_secret_out.key,
        )
    except Exception as e:
        return 400, GenericError(detail="Error adding webhook: " + str(e))
