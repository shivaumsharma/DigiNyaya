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
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.base import BaseStorageProvider  # noqa: E402
from app.storage.factory import StorageFactory  # noqa: E402
from app.storage.local import LocalFilesystemProvider, _sanitize_filename  # noqa: E402


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
        import app.storage.factory as factory_module
        from app.storage.config import StorageConfig

        with patch.object(factory_module, "config", StorageConfig(provider="s3")):
            with self.assertRaises(NotImplementedError):
                StorageFactory.create()


if __name__ == "__main__":
    unittest.main()
