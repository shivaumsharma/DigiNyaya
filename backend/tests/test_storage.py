"""Unit tests for app.storage -- the local filesystem provider and the
factory's provider-selection logic.

Run with (from backend/):
    python -m unittest tests.test_storage -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.base import BaseStorageProvider  # noqa: E402
from app.storage.factory import StorageFactory  # noqa: E402
from app.storage.local import LocalFilesystemProvider  # noqa: E402
from app.storage.sanitize import sanitize_filename as _sanitize_filename  # noqa: E402


class TestLocalFilesystemProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.provider = LocalFilesystemProvider(root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_then_read_roundtrips_content(self):
        storage_path = self.provider.save("DN-1", "invoice.pdf", b"hello world")
        self.assertTrue(self.provider.exists(storage_path))
        self.assertEqual(self.provider.read(storage_path), b"hello world")

    def test_save_returns_relative_path_scoped_to_case(self):
        storage_path = self.provider.save("DN-1", "invoice.pdf", b"content")
        self.assertTrue(storage_path.startswith("DN-1/"))
        self.assertNotIn("\\", storage_path)  # always forward-slash, even on Windows
        # The returned path must never be an absolute filesystem path.
        self.assertFalse(Path(storage_path).is_absolute())

    def test_two_uploads_with_same_filename_do_not_collide(self):
        p1 = self.provider.save("DN-1", "invoice.pdf", b"first")
        p2 = self.provider.save("DN-1", "invoice.pdf", b"second")
        self.assertNotEqual(p1, p2)
        self.assertEqual(self.provider.read(p1), b"first")
        self.assertEqual(self.provider.read(p2), b"second")

    def test_delete_removes_file(self):
        storage_path = self.provider.save("DN-1", "invoice.pdf", b"content")
        self.provider.delete(storage_path)
        self.assertFalse(self.provider.exists(storage_path))

    def test_exists_false_for_unknown_path(self):
        self.assertFalse(self.provider.exists("DN-1/does_not_exist.pdf"))

    def test_path_traversal_in_filename_is_neutralised(self):
        # A client-supplied filename attempting to escape the case directory
        # must never be able to write outside self.provider's root.
        storage_path = self.provider.save("DN-1", "../../etc/passwd", b"malicious")
        abs_path = Path(self.tmp_dir).resolve() / storage_path
        self.assertTrue(str(abs_path.resolve()).startswith(str(Path(self.tmp_dir).resolve())))

    def test_path_traversal_in_case_id_is_neutralised(self):
        storage_path = self.provider.save("../../escape", "file.pdf", b"content")
        abs_path = Path(self.tmp_dir).resolve() / storage_path
        self.assertTrue(str(abs_path.resolve()).startswith(str(Path(self.tmp_dir).resolve())))


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_directory_components(self):
        # Only the basename survives -- directory components are dropped
        # entirely, not merely made "safe", since the real path-escape
        # protection comes from LocalFilesystemProvider only ever joining
        # this result under a fixed per-case root (see the path-traversal
        # tests above).
        self.assertEqual(_sanitize_filename("../../etc/passwd"), "passwd")

    def test_keeps_safe_characters(self):
        self.assertEqual(_sanitize_filename("Invoice-2024_v2.pdf"), "Invoice-2024_v2.pdf")

    def test_empty_or_fully_unsafe_name_falls_back(self):
        self.assertEqual(_sanitize_filename("///"), "file")


class TestStorageFactory(unittest.TestCase):
    def test_local_provider_is_a_base_storage_provider(self):
        provider = StorageFactory.create()
        self.assertIsInstance(provider, BaseStorageProvider)

    def test_unimplemented_provider_raises_not_implemented(self):
        # gcs is the one genuinely unimplemented backend left -- s3 has its
        # own class now (see TestS3Provider below).
        import app.storage.factory as factory_module
        from app.storage.config import StorageConfig

        with patch.object(factory_module, "config", StorageConfig(provider="gcs")):
            with self.assertRaises(NotImplementedError):
                StorageFactory.create()

    def test_s3_provider_selected_when_configured(self):
        import app.storage.factory as factory_module
        import app.storage.s3 as s3_module
        from app.storage.config import StorageConfig
        from app.storage.s3 import S3Provider

        # Both bindings need patching: factory.py's own `config` decides
        # which branch to take, s3.py's own `config` (a separate name bound
        # at import time via `from .config import config`) is what
        # S3Provider() itself reads for the bucket name.
        new_config = StorageConfig(provider="s3", s3_bucket="test-bucket")
        with patch.object(factory_module, "config", new_config), \
             patch.object(s3_module, "config", new_config), \
             patch("boto3.client"):
            provider = StorageFactory.create()
        self.assertIsInstance(provider, S3Provider)


class TestS3Provider(unittest.TestCase):
    """boto3 itself is mocked throughout -- a live call would hit a real
    AWS bucket. These tests are about S3Provider's own logic (key shape,
    BaseStorageProvider contract, exists()'s 404-vs-real-error handling),
    not boto3 -- see the AWS migration plan for real-bucket verification.
    """

    def setUp(self):
        self.boto3_client_patcher = patch("boto3.client")
        self.mock_boto3_client = self.boto3_client_patcher.start()
        self.mock_client = self.mock_boto3_client.return_value
        from app.storage.s3 import S3Provider

        self.provider = S3Provider(bucket="test-bucket")

    def tearDown(self):
        self.boto3_client_patcher.stop()

    def test_missing_bucket_raises_value_error(self):
        from app.storage.config import StorageConfig
        from app.storage.s3 import S3Provider

        with patch("app.storage.s3.config", StorageConfig(provider="s3", s3_bucket="")):
            with self.assertRaises(ValueError):
                S3Provider()

    def test_save_puts_object_and_returns_key_scoped_to_case(self):
        storage_path = self.provider.save("DN-1", "invoice.pdf", b"hello world")
        self.assertTrue(storage_path.startswith("DN-1/"))
        self.mock_client.put_object.assert_called_once()
        kwargs = self.mock_client.put_object.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], storage_path)
        self.assertEqual(kwargs["Body"], b"hello world")

    def test_path_traversal_in_filename_and_case_id_is_neutralised(self):
        # Matches local.py's own path-traversal tests' actual property: "/"
        # is stripped (same as any other unsafe character), so a "../" in
        # either input can never introduce an extra path segment into the
        # key -- literal dots surviving as part of an otherwise-inert
        # single path component (e.g. ".._.._escape") is fine, same as it
        # is for LocalFilesystemProvider today; what matters is that the
        # key still has exactly the one "/" this provider's own key scheme
        # (case-component/file-component) puts there.
        storage_path = self.provider.save("../../escape", "../../etc/passwd", b"x")
        self.assertEqual(storage_path.count("/"), 1)

    def test_read_returns_object_body_bytes(self):
        self.mock_client.get_object.return_value = {"Body": BytesIO(b"content")}
        self.assertEqual(self.provider.read("DN-1/some-key"), b"content")
        self.mock_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="DN-1/some-key")

    def test_delete_calls_delete_object(self):
        self.provider.delete("DN-1/some-key")
        self.mock_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="DN-1/some-key")

    def test_exists_true_when_head_object_succeeds(self):
        self.mock_client.head_object.return_value = {}
        self.assertTrue(self.provider.exists("DN-1/some-key"))

    def test_exists_false_on_404(self):
        from botocore.exceptions import ClientError

        self.mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.assertFalse(self.provider.exists("DN-1/missing-key"))

    def test_exists_reraises_non_404_errors(self):
        from botocore.exceptions import ClientError

        self.mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )
        with self.assertRaises(ClientError):
            self.provider.exists("DN-1/some-key")


if __name__ == "__main__":
    unittest.main()
