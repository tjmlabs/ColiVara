import asyncio
import base64
import logging
import mimetypes
import os
import re
import urllib.parse
from io import BytesIO
from typing import Any, Dict, List, Optional

import aiohttp
import magic
from accounts.models import CustomUser
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import FloatField, Func, JSONField, Q
from django_stubs_ext.db.models import TypedModelMeta
from pdf2image import convert_from_bytes
from pgvector.django import HalfVectorField
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_fixed)

logger = logging.getLogger(__name__)


def get_upload_path(instance, filename):
    """
    Generate the upload path for document files.
    Path format: documents/<owner_email>/<filename>
    """
    owner_email = instance.collection.owner.email
    # Sanitize email to be safe for use in paths
    safe_email = owner_email.replace("@", "_at_")
    if settings.DEBUG:
        upload_path = f"dev-documents/{safe_email}/{filename}"  # pragma: no cover
    else:
        upload_path = f"documents/{safe_email}/{filename}"

    MAX_UPLOAD_PATH_LENGTH = 116
    if len(upload_path) <= MAX_UPLOAD_PATH_LENGTH:
        return upload_path

    extension = os.path.splitext(upload_path)[1]
    trimmed_upload_path = (
        upload_path[: MAX_UPLOAD_PATH_LENGTH - len(extension)] + extension
    )

    logger.info(f"Trimmed upload path to {trimmed_upload_path}")
    return trimmed_upload_path


def get_extension_from_mime(mime_type):
    """Get file extension from MIME type."""
    # Hard-code some common MIME types that mimetypes doesn't handle well
    hardcode_mimetypes = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/msword": ".doc",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.ms-excel": ".xls",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
        "application/csv": ".csv",
    }

    if mime_type in hardcode_mimetypes:
        return hardcode_mimetypes[mime_type]

    # Try to guess extension from MIME type
    extension = mimetypes.guess_extension(mime_type)
    if extension:
        return extension

    # Default to .bin if we can't determine the type
    return ".bin"


class Collection(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    owner = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="collections"
    )
    metadata = JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    async def document_count(self) -> int:
        return await self.documents.acount()

    class Meta(TypedModelMeta):
        constraints = [
            models.UniqueConstraint(
                fields=["name", "owner"], name="unique_collection_per_user"
            ),
            # disallow the name "all" for collections
            models.CheckConstraint(
                condition=~Q(name="all"), name="collection_name_not_all"
            ),
        ]

        indexes = [
            models.Index(fields=["name", "owner"], name="collection_name_owner_idx")
        ]


class Document(models.Model):
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="documents"
    )
    name = models.CharField(max_length=255, db_index=True)
    url = models.URLField(blank=True)
    s3_file = models.FileField(upload_to=get_upload_path, blank=True)
    metadata = JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    async def page_count(self) -> int:
        return await self.pages.acount()

    class Meta(TypedModelMeta):
        constraints = [
            models.UniqueConstraint(
                fields=["name", "collection"], name="unique_document_per_collection"
            )
        ]
        indexes = [
            models.Index(
                fields=["name", "collection"], name="document_name_collection_idx"
            )
        ]

    async def save_base64_to_s3(self, base64_content: str) -> None:
        """Convert base64 to file and save to S3"""
        try:
            await self.delete_s3_file()
            # Decode base64 content
            file_content = base64.b64decode(base64_content)

            # Detect MIME type
            mime = magic.Magic(mime=True)
            mime_type = mime.from_buffer(file_content)

            # Get appropriate file extension
            extension = get_extension_from_mime(mime_type)
            # Generate filename
            safe_name = self.name.replace(" ", "_")

            # Remove any existing extension (if present)
            name_without_extension, _ = os.path.splitext(safe_name)

            # Append the correct extension
            filename = f"{name_without_extension}{extension}"

            # Save to S3
            await sync_to_async(self.s3_file.save)(
                filename, ContentFile(file_content), save=True
            )

        except Exception as e:
            raise ValidationError(f"Failed to save file to S3: {str(e)}")

    async def delete_s3_file(self) -> None:
        """Delete the S3 file"""
        if self.s3_file:
            await sync_to_async(self.s3_file.delete)(save=False)

    async def get_url(self) -> str:
        """Get the URL of the document"""
        if self.s3_file:
            return self.s3_file.url
        return self.url

    async def embed_document(self, use_proxy: Optional[bool] = False) -> None:
        """
        Process a document by embedding its pages and storing the results.

        This method takes a list of base64 images (each image representing a page in a document),
        sends them to an embeddings service, then stores the results in the Page and PageEmbedding models.

        The method performs the following steps:
        1. Prepares the document by converting pages to base64 images.
        2. Splits the images into batches.
        3. Sends batches to the embeddings service concurrently.
        4. Saves the document and its pages with their corresponding embeddings.

        Raises:
            ValidationError: If there's an error in processing or saving the document and its pages.

        Note:
            - The method uses the EMBEDDINGS_URL and EMBEDDINGS_URL_TOKEN from settings.
            - If an error occurs during processing, the document and all its pages are deleted.
        """
        # Constants
        EMBEDDINGS_URL = settings.EMBEDDINGS_URL
        EMBEDDINGS_BATCH_SIZE = 3
        DELAY_BETWEEN_BATCHES = 1  # seconds

        # Helper function to send a batch of images to the embeddings service
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_fixed(5),
            reraise=True,
            retry=retry_if_exception_type(aiohttp.ClientError),
        )
        async def send_batch(
            session: aiohttp.ClientSession, images: List[str]
        ) -> List[Dict[str, Any]]:
            """
            Send a batch of images to the embeddings service and return the embeddings.

            Args:
                session (aiohttp.ClientSession): The aiohttp session to use for the request.
                images (List[str]): A list of base64-encoded images.

            Returns:
                List[Dict[str, Any]]: A list of embedding objects, each containing 'embedding', 'index', and 'object' keys.

            Raises:
                ValidationError: If the embeddings service returns a non-200 status code.

            Example of returned data:
                [
                    {
                        "embedding": [[0.1, 0.2, ..., 0.128]],  # List of 128 floats
                        "index": 0,
                        "object": "embedding"
                    },
                    ...
                ]
            """
            payload = {"input": {"task": "image", "input_data": images}}
            headers = {"Authorization": f"Bearer {settings.EMBEDDINGS_URL_TOKEN}"}
            async with session.post(
                EMBEDDINGS_URL, json=payload, headers=headers
            ) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to get embeddings from the embeddings service. Status code: {response.status}"
                    )
                    raise ValidationError(
                        "Failed to get embeddings from the embeddings service."
                    )
                out = await response.json()
                if "output" not in out or "data" not in out["output"]:
                    raise ValidationError(
                        f"Failed to get embeddings from the embeddings service. Repsonse: {out}"
                    )
                logger.info(
                    f"Got embeddings for batch of {len(images)} images. delayTime: {out['delayTime']}, executionTime: {out['executionTime']}"
                )
            return out["output"]["data"]

        base64_images = await self._prep_document(use_proxy=use_proxy)
        logger.info(f"Successfully prepped document {self.name}")
        # Split the images into batches
        batches = [
            base64_images[i : i + EMBEDDINGS_BATCH_SIZE]
            for i in range(0, len(base64_images), EMBEDDINGS_BATCH_SIZE)
        ]
        logger.info(
            f"Split document {self.name} into {len(batches)} batches for embedding"
        )

        try:
            # we save the document first, then save the pages
            await self.asave()
            logger.info(f"Starting the process to save document {self.name}")

            async with aiohttp.ClientSession() as session:
                embedding_results = []
                for i, batch in enumerate(batches):
                    # Process batches sequentially
                    batch_result = await send_batch(session, batch)
                    embedding_results.extend(batch_result)

                    logger.info(
                        f"Processed batch {i+1}/{len(batches)} for document {self.name}"
                    )

                    if i < len(batches) - 1:  # Don't delay after the last batch
                        logger.info(
                            f"Waiting {DELAY_BETWEEN_BATCHES} seconds before processing next batch"
                        )
                        # we don't want to overload the embeddings service
                        await asyncio.sleep(DELAY_BETWEEN_BATCHES)

            logger.info(
                f"Successfully got embeddings for all pages in document {self.name}"
            )

            for i, embedding_obj in enumerate(embedding_results):
                # we want to assert that the embeddings is a list of a list of 128 floats
                # each page = 1 embedding, an
                # exampple all_embeddings = [
                #     {
                #         "embedding": [[0.1, 0.2, ..., 0.128], [0.1, 0.2, ...]],  # List of n members, each a list of 128 floats
                #         "index": 0,
                #         "object": "embedding"
                #     },
                #     ...
                # ]
                assert (
                    isinstance(embedding_obj["embedding"], list)
                    and isinstance(embedding_obj["embedding"][0], list)
                    and len(embedding_obj["embedding"][0]) == 128
                ), "Embedding is not a list of a list of 128 floats"

                # can we create a page and pageembedding in one go?
                page = Page(
                    document=self,
                    page_number=i + 1,
                    img_base64=base64_images[i],
                )
                await page.asave()
                bulk_create_embeddings = [
                    PageEmbedding(page=page, embedding=embedding)
                    for embedding in embedding_obj["embedding"]
                ]
                await PageEmbedding.objects.abulk_create(bulk_create_embeddings)
                logger.info(
                    f"Successfully saved page {page.page_number} in document {self.name}"
                )
            logger.info(f"Successfully saved all pages in document {self.name}")
        except Exception as e:
            # If there's an error, delete the document and pages
            if self.pk:
                await self.adelete()  # will cascade delete the pages
            raise ValidationError(f"Failed to save pages: {str(e)}")

        return

    async def _prep_document(
        self, document_data=None, use_proxy: Optional[bool] = False
    ) -> List[str]:
        """
        The goal of this method is to take a document and convert it into a series of base64 images.
        Steps:
        1. Validate the document (size, type)
        2. Convert to PDF if not an image or a PDF via Gotenberg
        3. Turn the PDF into images via pdf2image
        4. Turn the images into base64 strings
        """
        # Constants
        IMAGE_EXTENSIONS = [
            "png",
            "jpg",
            "jpeg",
            "tiff",
            "bmp",
            "gif",
        ]  # Don't need to convert these
        ALLOWED_EXTENSIONS = [
            "123",
            "602",
            "abw",
            "bib",
            "cdr",
            "cgm",
            "cmx",
            "csv",
            "cwk",
            "dbf",
            "dif",
            "doc",
            "docm",
            "docx",
            "dot",
            "dotm",
            "dotx",
            "dxf",
            "emf",
            "eps",
            "epub",
            "fodg",
            "fodp",
            "fods",
            "fodt",
            "fopd",
            "htm",
            "html",
            "hwp",
            "key",
            "ltx",
            "lwp",
            "mcw",
            "met",
            "mml",
            "mw",
            "numbers",
            "odd",
            "odg",
            "odm",
            "odp",
            "ods",
            "odt",
            "otg",
            "oth",
            "otp",
            "ots",
            "ott",
            "pages",
            "pbm",
            "pcd",
            "pct",
            "pcx",
            "pdb",
            "pdf",
            "pgm",
            "pot",
            "potm",
            "potx",
            "ppm",
            "pps",
            "ppt",
            "pptm",
            "pptx",
            "psd",
            "psw",
            "pub",
            "pwp",
            "pxl",
            "ras",
            "rtf",
            "sda",
            "sdc",
            "sdd",
            "sdp",
            "sdw",
            "sgl",
            "slk",
            "smf",
            "stc",
            "std",
            "sti",
            "stw",
            "svg",
            "svm",
            "swf",
            "sxc",
            "sxd",
            "sxg",
            "sxi",
            "sxm",
            "sxw",
            "tga",
            "txt",
            "uof",
            "uop",
            "uos",
            "uot",
            "vdx",
            "vor",
            "vsd",
            "vsdm",
            "vsdx",
            "wb2",
            "wk1",
            "wks",
            "wmf",
            "wpd",
            "wpg",
            "wps",
            "xbm",
            "xhtml",
            "xls",
            "xlsb",
            "xlsm",
            "xlsx",
            "xlt",
            "xltm",
            "xltx",
            "xlw",
            "xml",
            "xpm",
            "zabw",
        ]
        ALLOWED_EXTENSIONS += IMAGE_EXTENSIONS  # Include images
        MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
        # Step 1: Get the document data
        filename = None  # document.pdf or document.docx
        extension = None
        # here we should have a document_data and filename
        if document_data:
            logger.info("Document data provided.")
            # Get MIME type from magic
            mime = magic.Magic(mime=True)
            mime_type = mime.from_buffer(document_data)
            extension = get_extension_from_mime(mime_type).lstrip(".")
            filename = f"document.{extension}"

        # every block should give back a document_data, and filename w/ extension
        elif self.s3_file:
            logger.info(f"Fetching document from S3: {self.s3_file.name}")
            with self.s3_file.open("rb") as f:
                document_data = f.read()
            filename = os.path.basename(self.s3_file.name)
            logger.info(f"Document filename: {filename}")

        elif self.url:
            content_type, filename, document_data = await self._fetch_document(
                use_proxy
            )
            if "text/html" in content_type:
                logger.info("Document is a webpage.")
                # It's a webpage, convert to PDF
                document_data = await self._convert_url_to_pdf(self.url)
                logger.info("Successfully converted URL to PDF.")
                filename = f"{filename}.pdf"
            else:
                # It's a regular file
                logger.info(f"Fetching document from URL: {self.url}")
                if "application/pdf" in content_type:
                    extension = "pdf"
                else:
                    extension = get_extension_from_mime(content_type).lstrip(".")
                assert filename, "Filename should be set"
                name = os.path.splitext(filename)[0]
                filename = f"{name}.{extension}"
                logger.info(f"Document filename: {filename}")
        else:
            raise ValidationError(
                "Document data is missing. Please provide a document or a URL."
            )

        # make sure we have the document data and filename
        assert document_data, "Document data should be set"
        assert filename, "Filename should be set"

        if not extension:
            extension = os.path.splitext(filename)[1].lstrip(".")

        if len(document_data) > MAX_SIZE_BYTES:
            raise ValidationError("Document exceeds maximum size of 50MB.")

        if extension not in ALLOWED_EXTENSIONS:
            raise ValidationError(f"File extension .{extension} is not allowed.")

        # Determine if the document is an image or PDF
        is_image = extension in IMAGE_EXTENSIONS
        is_pdf = extension == "pdf"
        # Step 2: Convert to PDF if necessary
        if not is_image and not is_pdf:
            logger.info(f"Converting document to PDF. Extension: {extension}")
            # Use Gotenberg to convert to PDF
            pdf_data = await self._convert_to_pdf(document_data, filename)
        elif is_pdf:
            logger.info("Document is already a PDF.")
            pdf_data = document_data
        else:
            # if it is an image, convert it to base64 and return
            logger.info("Document is an image. Converting to base64.")
            img_base64 = base64.b64encode(document_data).decode("utf-8")
            return [img_base64]

        # here all documents are converted to pdf
        # Step 3: Turn the PDF into images via pdf2image
        try:
            images = convert_from_bytes(pdf_data)
        except Exception:
            raise ValidationError(
                "Failed to convert PDF to images. The PDF may be corrupted, which sometimes happens with URLs. Try downloading the document and sending us the base64."
            )
        logger.info(f"Successfully converted PDF to {len(images)} images.")

        # here all documents are converted to images
        # Step 4: Turn the images into base64 strings
        base64_images = []
        for image in images:
            img_io = BytesIO()
            image.save(img_io, "PNG")
            img_data = img_io.getvalue()
            img_base64 = base64.b64encode(img_data).decode("utf-8")
            base64_images.append(img_base64)

        # Step 5: returning the base64 images
        return base64_images

    async def _fetch_document(self, use_proxy: Optional[bool] = False):
        proxy = None
        if use_proxy:
            proxy = settings.PROXY_URL
            # replace https with http for the proxy
            self.url = self.url.replace("https://", "http://")
            logger.info("Using proxy to fetch document.")

        MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url, proxy=proxy) as response:
                # handle when the response is not 200
                if response.status != 200:
                    logger.info(f"response status: {response.status}")
                    raise ValidationError(
                        "Failed to fetch document info from URL. Some documents are protected by anti-scrapping measures. We recommend you download them and send us base64."
                    )
                content_type = response.headers.get("Content-Type", "").lower()
                content_disposition = response.headers.get("Content-Disposition", "")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_SIZE_BYTES:
                    raise ValidationError("Document exceeds maximum size of 50MB.")
                filename_match = re.findall('filename="(.+)"', content_disposition)
                filename = (
                    filename_match[0]
                    if filename_match
                    else os.path.basename(urllib.parse.urlparse(self.url).path)
                )
                if not filename:
                    filename = "downloaded_file"
                return content_type, filename, await response.read()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        reraise=True,
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    async def _convert_to_pdf(self, document_data: bytes, filename: str) -> bytes:
        """
        Helper method to convert documents to PDF using Gotenberg.
        """
        Gotenberg_URL = settings.GOTENBERG_URL
        endpoint = "/forms/libreoffice/convert"
        url = Gotenberg_URL + endpoint

        # Prepare the form data for Gotenberg
        form = aiohttp.FormData()
        form.add_field(
            "files",
            document_data,
            filename=filename,
        )

        # Set Gotenberg's specific headers if needed (adjust according to your Gotenberg setup)
        headers = {
            "Accept": "application/pdf",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form, headers=headers) as response:
                if response.status != 200:
                    error_message = await response.text()
                    raise ValidationError(
                        f"Failed to convert document to PDF via Gotenberg: {error_message}"
                    )
                pdf_data = await response.read()
        return pdf_data

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        reraise=True,
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    async def _convert_url_to_pdf(self, url: str) -> bytes:
        """
        Helper method to convert a webpage URL to PDF using Gotenberg.
        """
        Gotenberg_URL = settings.GOTENBERG_URL
        endpoint = "/forms/chromium/convert/url"
        gotenberg_url = Gotenberg_URL + endpoint
        logger.info(f"Converting URL to PDF as a Webpage: {url}")
        # Prepare the form data for Gotenberg
        form = aiohttp.FormData()
        form.add_field("url", url, content_type="text/plain")

        async with aiohttp.ClientSession() as session:
            async with session.post(gotenberg_url, data=form) as response:
                if response.status != 200:
                    error_message = await response.text()
                    raise ValidationError(
                        f"Failed to convert URL to PDF via Gotenberg: {error_message}"
                    )
                pdf_data = await response.read()
        return pdf_data


class Page(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="pages"
    )
    page_number = models.IntegerField()
    content = models.TextField(blank=True)
    img_base64 = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.document.name} - Page {self.page_number}"


class PageEmbedding(models.Model):
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="embeddings")
    embedding = HalfVectorField(dimensions=128)


# TO DO: Post save signal on page to get the content via OCR


class MaxSim(Func):
    function = "max_sim"
    output_field = FloatField()
