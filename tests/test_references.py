"""Existing alpha is preserved without loading torch or invoking cloud inference."""

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from tools.modal_references import mask_background
from tools import modal_references


class ReferenceTests(unittest.TestCase):
    def test_original_cutout_and_dark_pixels_are_preserved(self):
        source = Image.new("RGBA", (3, 3), (0, 0, 0, 0))
        source.putpixel((1, 1), (1, 2, 3, 255))
        source.putpixel((2, 1), (4, 5, 6, 128))
        result = mask_background(source)
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_fully_transparent_source_is_rejected_before_model_loading(self):
        with self.assertRaisesRegex(ValueError, "fully transparent"):
            mask_background(Image.new("RGBA", (3, 3), (0, 0, 0, 0)))

    def test_retry_reuses_committed_reference_instead_of_repeating_inference(self):
        picture = Image.new("RGB", (3, 3), "red")
        volume = SimpleNamespace(commit=Mock())
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(modal_references, "weights", volume),
            patch.object(modal_references, "edit", return_value=picture) as edit,
        ):
            arguments = (Path(temporary), "front", [picture], "A pose", "qwen-edit", 42)
            first = modal_references.cached_edit(*arguments)
            second = modal_references.cached_edit(*arguments)
            self.assertEqual(first.tobytes(), second.tobytes())
            edit.assert_called_once()
            volume.commit.assert_called_once()
