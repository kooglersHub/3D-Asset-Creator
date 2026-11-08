"""Existing alpha is preserved without loading torch or invoking cloud inference."""

import unittest

from PIL import Image

from tools.modal_references import mask_background


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
