import pytest
from django.core.exceptions import ImproperlyConfigured

from config.storage import SUPABASE_S3_VARS, default_storage_config

SECRET = "super-secret-value-do-not-leak"
ALL_VARS = {
    "SUPABASE_S3_ENDPOINT": "https://example.supabase.co/storage/v1/s3",
    "SUPABASE_S3_REGION": "ap-southeast-1",
    "SUPABASE_S3_BUCKET": "patient-documents",
    "SUPABASE_S3_ACCESS_KEY_ID": "access-key-id",
    "SUPABASE_S3_SECRET_ACCESS_KEY": SECRET,
}


def test_all_variables_give_s3_storage_with_path_addressing():
    config = default_storage_config(ALL_VARS, on_render=True)
    options = config["OPTIONS"]

    assert config["BACKEND"] == "storages.backends.s3.S3Storage"
    assert options["bucket_name"] == "patient-documents"
    assert options["endpoint_url"] == ALL_VARS["SUPABASE_S3_ENDPOINT"]
    assert options["addressing_style"] == "path"
    assert options["querystring_auth"] is True
    assert options["file_overwrite"] is False
    # Must also be in client_config, which django-storages uses instead when given.
    assert options["client_config"].s3 == {"addressing_style": "path"}
    assert options["client_config"].signature_version == "s3v4"


def test_partial_variables_raise_naming_missing_ones_without_values():
    partial = {
        "SUPABASE_S3_ENDPOINT": ALL_VARS["SUPABASE_S3_ENDPOINT"],
        "SUPABASE_S3_SECRET_ACCESS_KEY": SECRET,
    }

    with pytest.raises(ImproperlyConfigured) as excinfo:
        default_storage_config(partial, on_render=False)

    message = str(excinfo.value)
    for name in ("SUPABASE_S3_REGION", "SUPABASE_S3_BUCKET", "SUPABASE_S3_ACCESS_KEY_ID"):
        assert name in message
    assert SECRET not in message
    assert ALL_VARS["SUPABASE_S3_ENDPOINT"] not in message


def test_no_variables_on_render_raise():
    with pytest.raises(ImproperlyConfigured, match="ephemeral"):
        default_storage_config({}, on_render=True)


def test_no_variables_locally_use_filesystem():
    config = default_storage_config({name: "" for name in SUPABASE_S3_VARS}, on_render=False)

    assert config == {"BACKEND": "django.core.files.storage.FileSystemStorage"}
