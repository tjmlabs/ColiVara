import asyncio
import base64
import mimetypes
import os
import re
import urllib.parse
from binascii import Error as BinasciiError
from io import BytesIO
from typing import Any, Dict, List

import aiohttp
import magic
from accounts.models import CustomUser
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import FloatField, Func, JSONField
from django_stubs_ext.db.models import TypedModelMeta
from pdf2image import convert_from_bytes
from pgvector.django import HalfVectorField


class Collection(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="collections"
    )
    metadata = JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    class Meta(TypedModelMeta):
        constraints = [
            models.UniqueConstraint(
                fields=["name", "owner"], name="unique_collection_per_user"
            )
        ]


class Document(models.Model):
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="documents"
    )
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    base64 = models.TextField(blank=True)
    metadata = JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    class Meta(TypedModelMeta):
        constraints = [
            models.UniqueConstraint(
                fields=["name", "collection"], name="unique_document_per_collection"
            )
        ]

    async def embed_document(self) -> None:
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
        EMBEDDINGS_BATCH_SIZE = 5

        # Helper function to send a batch of images to the embeddings service
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
                    raise ValidationError(
                        "Failed to get embeddings from the embeddings service."
                    )
                out = await response.json()
            return out["output"]["data"]

        base64_images = await self._prep_document()
        # Split the images into batches
        batches = [
            base64_images[i : i + EMBEDDINGS_BATCH_SIZE]
            for i in range(0, len(base64_images), EMBEDDINGS_BATCH_SIZE)
        ]

        try:
            # we save the document first, then save the pages
            await self.asave()

            async with aiohttp.ClientSession() as session:
                # Use gather to send all batches concurrently
                embedding_results = await asyncio.gather(
                    *[send_batch(session, batch) for batch in batches]
                )

            # Flatten the results
            all_embeddings = [
                embedding
                for batch_result in embedding_results
                for embedding in batch_result
            ]

            for i, embedding_obj in enumerate(all_embeddings):
                # we want to assert that the embeddings is a list of a list of 128 floats
                # each page = 1 embedding, an
                # exampple all_embeddings = [
                #     {
                #         "embedding": [[0.1, 0.2, ..., 0.128], [0.1, 0.2, ...]],  # List of 1030 members, each a list of 128 floats
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

        except Exception as e:
            # If there's an error, delete the document and pages
            await self.adelete()  # will cascade delete the pages
            raise ValidationError(f"Failed to save pages: {str(e)}")

        return

    async def _prep_document(self, document_data=None) -> List[str]:
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

        # Helper function to get file extension
        def get_extension(filename):
            _, ext = os.path.splitext(filename)
            return ext[1:].lower() if ext else ""

        def get_mime_type(data):
            mime = magic.Magic(mime=True)
            return mime.from_buffer(data)

        async def fetch_document(url):
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise ValidationError("Failed to fetch document from URL")
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > MAX_SIZE_BYTES:
                        raise ValidationError("Document exceeds maximum size of 50MB.")
                    content_disposition = response.headers.get(
                        "Content-Disposition", ""
                    )
                    filename_match = re.findall('filename="(.+)"', content_disposition)
                    filename = (
                        filename_match[0]
                        if filename_match
                        else os.path.basename(urllib.parse.urlparse(url).path)
                    )
                    return await response.read(), filename

        # Step 1: Validate the document
        filename = None

        # every block should give back a document_data and filename
        if self.url and not document_data:
            parsed_url = urllib.parse.urlparse(self.url)
            url_extension = get_extension(parsed_url.path)

            if url_extension == "" or url_extension not in ALLOWED_EXTENSIONS:
                # Likely a webpage URL, convert to PDF via Gotenberg
                try:
                    pdf_data = await self._convert_url_to_pdf(self.url)
                    document_data = pdf_data
                    filename = "webpage.pdf"
                except Exception as e:
                    raise ValidationError(f"Failed to convert URL to PDF: {str(e)}")

            else:
                document_data, filename = await fetch_document(self.url)

        elif self.base64 and not document_data:
            # Decode base64 content
            try:
                document_data = base64.b64decode(self.base64)
                filename = "document"
            except BinasciiError:
                raise ValidationError("Invalid base64 content.")

        # here we should have a document_data and filename

        # Validate the size
        assert document_data is not None, "document_data should not be None"
        if len(document_data) > MAX_SIZE_BYTES:
            raise ValidationError("Document exceeds maximum size of 50MB.")

        # Determine file type
        mime_type = get_mime_type(document_data)
        extension = mimetypes.guess_extension(mime_type)
        # hard-code some mimetype that guess_extesion can't handle
        hardcode_mimetypes = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/msword": ".doc",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.ms-excel": ".xls",
        }
        if extension is None:
            extension = hardcode_mimetypes.get(mime_type, None)
        if extension:
            extension = extension[1:].lower()
        else:
            extension = get_extension(filename)

        # Validate file extension
        if extension not in ALLOWED_EXTENSIONS:
            raise ValidationError(f"File extension .{extension} is not allowed.")

        # Determine if the document is an image or PDF
        is_image = extension in IMAGE_EXTENSIONS
        is_pdf = extension == "pdf"
        # Step 2: Convert to PDF if necessary
        if not is_image and not is_pdf:
            # Use Gotenberg to convert to PDF
            filename = f"{filename}.{extension}"
            pdf_data = await self._convert_to_pdf(document_data, filename)
        elif is_pdf:
            pdf_data = document_data
        else:
            # if it is an image, convert it to base64 and return
            img_base64 = base64.b64encode(document_data).decode("utf-8")
            return [img_base64]

        # here all documents are converted to pdf
        # Step 3: Turn the PDF into images via pdf2image
        try:
            images = convert_from_bytes(pdf_data)
        except Exception as e:
            raise ValidationError(f"Failed to convert PDF to images: {str(e)}")

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

    async def _convert_to_pdf(self, document_data: bytes, filename: str) -> bytes:
        """
        Helper method to convert documents to PDF using Gotenberg.
        """
        Gotenberg_URL = "http://gotenberg:3000"
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

    async def _convert_url_to_pdf(self, url: str) -> bytes:
        """
        Helper method to convert a webpage URL to PDF using Gotenberg.
        """
        Gotenberg_URL = "http://gotenberg:3000"
        endpoint = "/forms/chromium/convert/url"
        gotenberg_url = Gotenberg_URL + endpoint

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