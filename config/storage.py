"""Chooses where uploaded files are stored (STORAGES["default"])."""

from django.core.exceptions import ImproperlyConfigured

SUPABASE_S3_VARS = (
    "SUPABASE_S3_ENDPOINT",
    "SUPABASE_S3_REGION",
    "SUPABASE_S3_BUCKET",
    "SUPABASE_S3_ACCESS_KEY_ID",
    "SUPABASE_S3_SECRET_ACCESS_KEY",
)


def default_storage_config(env_values, on_render):
    """Return the STORAGES["default"] entry.

    - All Supabase S3 variables set: Supabase Storage (private bucket).
    - Some set: configuration error naming the missing ones (never the values).
    - None set on Render: configuration error, because Render's disk is wiped on deploy.
    - None set locally: files go to MEDIA_ROOT on the local disk.
    """
    present = [name for name in SUPABASE_S3_VARS if env_values.get(name)]
    missing = [name for name in SUPABASE_S3_VARS if not env_values.get(name)]

    if present and missing:
        raise ImproperlyConfigured(
            f"Supabase Storage is partly configured. Missing: {', '.join(missing)}"
        )

    if not present:
        if on_render:
            raise ImproperlyConfigured(
                "Render's disk is ephemeral; configure Supabase Storage "
                f"({', '.join(SUPABASE_S3_VARS)})."
            )
        # FileSystemStorage stores under settings.MEDIA_ROOT by default.
        return {"BACKEND": "django.core.files.storage.FileSystemStorage"}

    from botocore.config import Config

    return {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env_values["SUPABASE_S3_BUCKET"],
            "endpoint_url": env_values["SUPABASE_S3_ENDPOINT"],
            "region_name": env_values["SUPABASE_S3_REGION"],
            "access_key": env_values["SUPABASE_S3_ACCESS_KEY_ID"],
            "secret_key": env_values["SUPABASE_S3_SECRET_ACCESS_KEY"],
            # Supabase only supports path-style URLs (endpoint/bucket/key).
            "addressing_style": "path",
            "signature_version": "s3v4",
            "default_acl": None,
            # Signed, expiring URLs; the bucket itself stays private.
            "querystring_auth": True,
            # Never replace an existing file that happens to have the same name.
            "file_overwrite": False,
            # django-storages ignores addressing_style/signature_version above when
            # client_config is given, so they are repeated here. The checksum options
            # turn off newer boto3 default checksums that S3-compatible providers
            # like Supabase can reject.
            "client_config": Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        },
    }
