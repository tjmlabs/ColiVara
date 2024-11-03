import base64

import magic
from api.models import get_extension_from_mime
from django.core.files.base import ContentFile
from django.db import migrations


def migrate_base64_to_s3(apps, schema_editor):
    Document = apps.get_model("api", "Document")
    mime = magic.Magic(mime=True)

    # Process documents in batches to avoid memory issues
    batch_size = 10
    total_documents = Document.objects.filter(base64__gt="").count()

    for offset in range(0, total_documents, batch_size):
        documents = Document.objects.filter(base64__gt="")[offset : offset + batch_size]

        for document in documents:
            if document.base64 and not document.s3_file:
                try:
                    # Decode base64 content
                    file_content = base64.b64decode(document.base64)

                    # Detect MIME type
                    mime_type = mime.from_buffer(file_content)

                    # Get appropriate file extension
                    extension = get_extension_from_mime(mime_type)

                    # Generate a filename based on the document name
                    safe_name = document.name.replace(" ", "_")
                    filename = f"{safe_name}{extension}"

                    # Save the file to S3
                    document.s3_file.save(
                        filename, ContentFile(file_content), save=True
                    )

                    print(
                        f"Successfully migrated document {document.id} as {mime_type}"
                    )

                    # Optionally, clear base64 field immediately
                    document.base64 = ""
                    document.save()

                except Exception as e:
                    print(f"Error migrating document {document.id}: {str(e)}")


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0021_document_s3_file"),
    ]

    operations = [
        migrations.RunPython(
            migrate_base64_to_s3,
        ),
    ]
