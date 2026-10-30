"""OCR extraction pipeline for FC Mobile result screenshots.

Crops specific regions of the FC Mobile result screen and uses EasyOCR
to extract player names and scores.
"""

from __future__ import annotations

import asyncio
import io
import re
import logging
from typing import Optional

import aiohttp
import easyocr
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw

from core.models import OCRResult

log = logging.getLogger(__name__)

# Region definitions as fractions of image dimensions (x1, y1, x2, y2)
# Supports both new layout (top banners) and legacy layout (banner cards)
REGIONS = {
    # Top-left corner banner where IGN appears (e.g., 'D E M O N')
    "top_left_banner": (0.13, 0.00, 0.32, 0.065),
    # Top-right corner banner where IGN appears (e.g., 'BlueConnection')
    "top_right_banner": (0.68, 0.00, 0.86, 0.065),
    # Mid-left name card (fallback for previous result screen layout)
    "mid_left_name": (0.10, 0.06, 0.42, 0.20),
    # Mid-right name card (fallback for previous result screen layout)
    "mid_right_name": (0.58, 0.06, 0.90, 0.20),
    # Score region: center area (e.g., '2 - 1' or '2 " 1')
    "score": (0.38, 0.08, 0.62, 0.26),
}


class OCRExtractor:
    """Extracts player names and scores from FC Mobile result screenshots.

    Supports both phone (wide ~16:9 / 20:9) and tablet (iPad ~4:3 / 3:2) aspect ratios.
    """

    def __init__(self, languages: list[str] | None = None) -> None:
        if languages is None:
            languages = ["en"]
        self.languages = languages
        self.reader: Optional[easyocr.Reader] = None  # initialized lazily via _ensure_reader()

    def _init_reader_sync(self) -> None:
        """Synchronously initialize the EasyOCR reader (run in executor)."""
        if self.reader is None:
            log.info("Initialising EasyOCR reader (languages=%s, gpu=False)", self.languages)
            self.reader = easyocr.Reader(self.languages, gpu=False, verbose=False)
            log.info("EasyOCR reader ready.")

    async def _ensure_reader(self) -> None:
        """Ensure the EasyOCR reader is initialized without blocking the event loop."""
        if self.reader is None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._init_reader_sync)

    def _crop_region(
        self,
        image: Image.Image,
        region: tuple[float, float, float, float],
    ) -> Image.Image:
        """Crop *image* using fractional coordinates."""
        w, h = image.size
        x1, y1, x2, y2 = region
        box = (int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h))
        return image.crop(box)

    def _preprocess(self, image: Image.Image, scale: int = 2, contrast: float = 2.0) -> Image.Image:
        """Apply preprocessing pipeline to improve OCR accuracy."""
        if image.mode != "RGB":
            image = image.convert("RGB")

        w, h = image.size
        image = image.resize((w * scale, h * scale), Image.LANCZOS)
        image = image.convert("L")
        image = ImageEnhance.Contrast(image).enhance(contrast)
        image = image.filter(ImageFilter.SHARPEN)

        return image

    def _extract_text_sync(self, img_array: np.ndarray) -> list[tuple]:
        """Run EasyOCR readtext synchronously (must be called in executor)."""
        return self.reader.readtext(img_array)  # type: ignore[union-attr]

    def _extract_text_from_image_sync(self, image: Image.Image) -> tuple[str, float]:
        """Run EasyOCR on *image* and return ONLY the topmost line (the player's username).

        FC Mobile banners place the player username on the topmost line,
        with league tags/ranks/subtitles directly beneath it.
        """
        # Upscale 2x for optimal OCR fidelity on small mobile font sizes
        upscaled = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
        img_array: np.ndarray = np.array(upscaled)
        results: list[tuple] = self.reader.readtext(img_array)  # type: ignore[union-attr]

        if not results:
            return "", 0.0

        # Filter out UI button text and background artifacts
        ignore_phrases = (
            "CONTINUE", "ADD FRIEND", "MATCH HIGHLIGHTS", "SHOTS", "POSSESSION", "PASS ACCURACY"
        )
        filtered = []
        for bbox, text, conf in results:
            t = text.strip()
            if any(t.upper().startswith(ig) for ig in ignore_phrases):
                continue
            filtered.append((bbox, t, conf))

        if not filtered:
            return "", 0.0

        # Filter out extreme low-confidence noise tokens (e.g. badge icons < 0.25)
        valid = [r for r in filtered if r[2] >= 0.25]
        if not valid:
            valid = [max(filtered, key=lambda r: r[2])]

        # Sort detections vertically by top y-coordinate
        valid_sorted = sorted(valid, key=lambda r: min(p[1] for p in r[0]))

        # The topmost line is strictly the player's username!
        top_y = min(p[1] for p in valid_sorted[0][0])
        top_line = [r for r in valid_sorted if abs(min(p[1] for p in r[0]) - top_y) <= 14]
        # Sort left to right
        top_line.sort(key=lambda r: min(p[0] for p in r[0]))

        joined = " ".join(r[1].strip() for r in top_line)
        avg_conf = sum(r[2] for r in top_line) / len(top_line) if top_line else 0.0
        return joined, avg_conf

    def _extract_score_sync(self, c_score: Image.Image) -> Optional[tuple[int, int]]:
        """Extract home and away scores from the score crop.

        1. First inspect the raw unmasked score crop. When EasyOCR detects the score
           line as a whole with context (e.g. '1 " 1', '5 " 1', '1 " 2', '2 " 0',
           '3 " 1', '1 7 0', '3 7 1'), it recognizes digits with high fidelity.
        2. If unmasked line detection doesn't yield two valid scores, fall back to
           masked detection (where center FC logo is blanked) with strict confidence >= 0.25
           to avoid stray low-confidence noise tokens (like misidentified '7' at conf 0.07).
        """
        sw, sh = c_score.size

        # --- Phase 1: Unmasked contextual detection ---
        raw_res = self.reader.readtext(np.array(c_score))  # type: ignore[union-attr]
        for _bbox, text, conf in raw_res:
            t = text.strip()
            if ":" in t or conf < 0.20:
                continue

            # Standard formats: '2 - 1', '3 " 1', '1 " 1', '5 " 1'
            m = re.search(r"(\d+)\s*[-–—:\"]\s*(\d+)", t)
            if m:
                s1, s2 = int(m.group(1)), int(m.group(2))
                if s1 <= 20 and s2 <= 20:
                    return s1, s2

            # Space-separated: '1 7 0' (1 vs 0 with FC logo '7' in center), '3 7 1' (3 vs 1)
            # or '1 0', '2 1', '1 1'
            digits = re.findall(r"\d+", t)
            if len(digits) == 3 and digits[1] == '7':
                s1, s2 = int(digits[0]), int(digits[2])
                if s1 <= 20 and s2 <= 20:
                    return s1, s2
            elif len(digits) == 2:
                s1, s2 = int(digits[0]), int(digits[1])
                if s1 <= 20 and s2 <= 20:
                    return s1, s2

        # --- Phase 2: Masked detection (when scores are in separate boxes) ---
        masked = c_score.copy()
        draw = ImageDraw.Draw(masked)
        draw.rectangle([int(sw * 0.38), 0, int(sw * 0.62), sh], fill=(0, 0, 0))
        masked_2x = masked.resize((sw * 2, sh * 2), Image.LANCZOS)

        masked_res = self.reader.readtext(np.array(masked_2x))  # type: ignore[union-attr]
        left_c: list[tuple[float, int]] = []
        right_c: list[tuple[float, int]] = []
        w_2x = masked_2x.width

        for bbox, text, conf in masked_res:
            t = text.strip().replace("O", "0").replace("o", "0")
            digits = re.findall(r"\d+", t)
            if digits and conf >= 0.25:  # Strict confidence prevents noise artifacts
                cx = sum(p[0] for p in bbox) / len(bbox)
                val = int(digits[0])
                if val <= 20:
                    if cx < (w_2x * 0.50):
                        left_c.append((float(conf), val))
                    else:
                        right_c.append((float(conf), val))

        if left_c and right_c:
            left_c.sort(key=lambda c: c[0], reverse=True)
            right_c.sort(key=lambda c: c[0], reverse=True)
            return left_c[0][1], right_c[0][1]

        # --- Phase 3: Preprocessed fallback ---
        proc_score = self._preprocess(c_score, scale=2, contrast=2.0)
        res_proc = self.reader.readtext(np.array(proc_score))  # type: ignore[union-attr]
        return self._parse_score_from_results(res_proc)

    @staticmethod
    def _parse_score_from_results(results_list: list[tuple]) -> Optional[tuple[int, int]]:
        """Parse match score from EasyOCR detection list.

        Handles both combined score boxes ('1 - 2', '1 2') and separated
        score boxes where EasyOCR detects left and right scores as distinct boxes.
        """
        def _sanitize_val(val: int) -> int:
            """Clean numbers where FC logo '7' fused into digit (e.g. 71 -> 1, 70 -> 0)."""
            if val > 20 and str(val).startswith("7") and len(str(val)) == 2:
                return int(str(val)[1:])
            return val

        # 1. First attempt: check for combined score inside a single text box
        for _bbox, text, _conf in results_list:
            text_clean = text.strip()
            # Ignore match duration like '90:00' or '120:00'
            if ":" in text_clean:
                continue

            # Standard '2 - 1', '2-0', '2 " 1', '2 : 1'
            m = re.search(r"(\d+)\s*[-–—:\"]\s*(\d+)", text_clean)
            if m:
                s1, s2 = _sanitize_val(int(m.group(1))), _sanitize_val(int(m.group(2)))
                if s1 <= 20 and s2 <= 20:
                    return s1, s2

            # Isolated digits in single string: e.g. '1 7 0' (1 vs 0), '0 0'
            digits = re.findall(r"\b\d+\b", text_clean)
            if len(digits) >= 2:
                s1, s2 = _sanitize_val(int(digits[0])), _sanitize_val(int(digits[-1]))
                if s1 <= 20 and s2 <= 20:
                    return s1, s2

        # 2. Second attempt: left and right scores detected as separate bounding boxes
        # e.g. Box 1: '1 %' (left score=1), Box 2: '2' (right score=2)
        candidates: list[tuple[float, float, int, float]] = []
        for bbox, text, conf in results_list:
            text_clean = text.strip()
            if ":" in text_clean or conf < 0.15:
                continue

            found = re.findall(r"\d+", text_clean)
            if found:
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                cx = sum(xs) / len(xs)
                height = max(ys) - min(ys)
                for d in found:
                    val = _sanitize_val(int(d))
                    if val <= 20:
                        candidates.append((cx, height, val, float(conf)))

        if len(candidates) >= 2:
            # Sort left to right
            candidates.sort(key=lambda c: c[0])
            # Filter out tiny noise artifacts (e.g. height < 30px) if larger digit boxes exist
            large_candidates = [c for c in candidates if c[1] >= 30]
            if len(large_candidates) >= 2:
                return large_candidates[0][2], large_candidates[-1][2]
            return candidates[0][2], candidates[-1][2]

        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(self, image_bytes: bytes) -> Optional[OCRResult]:
        """Extract player names and score from a result screenshot."""
        await self._ensure_reader()

        loop = asyncio.get_event_loop()

        image = Image.open(io.BytesIO(image_bytes))
        w, h = image.size
        aspect_ratio = w / h  # ~1.44 for iPad, ~2.16 for phones

        is_tablet = aspect_ratio < 1.70

        if is_tablet:
            # iPad / Tablet geometry: isolate strictly the username header bar
            left_header_box = (0.09, 0.005, 0.28, 0.048)
            right_header_box = (0.72, 0.005, 0.92, 0.048)
            score_box = (0.36, 0.16, 0.64, 0.30)
        else:
            # Standard Phone geometry: isolate strictly the username header bar
            left_header_box = (0.11, 0.005, 0.32, 0.055)
            right_header_box = (0.68, 0.005, 0.88, 0.055)
            score_box = (0.36, 0.08, 0.64, 0.25)

        # 1. Left Name Extraction (strictly topmost username line)
        c_left = self._crop_region(image, left_header_box)
        left_text, left_conf = await loop.run_in_executor(
            None, self._extract_text_from_image_sync, c_left
        )

        # 2. Right Name Extraction (strictly topmost username line)
        c_right = self._crop_region(image, right_header_box)
        right_text, right_conf = await loop.run_in_executor(
            None, self._extract_text_from_image_sync, c_right
        )

        # 3. Score Extraction
        c_score = self._crop_region(image, score_box)
        parsed = await loop.run_in_executor(
            None, self._extract_score_sync, c_score
        )

        if parsed is None:
            log.warning("Failed to parse score from score box")
            return None

        left_score, right_score = parsed
        score_conf = 0.90 if parsed else 0.0
        avg_confidence = (left_conf + right_conf + score_conf) / 3.0

        log.info(
            "OCR result (aspect=%.2f) — left='%s' (%d), right='%s' (%d), conf=%.2f",
            aspect_ratio,
            left_text,
            left_score,
            right_text,
            right_score,
            avg_confidence,
        )

        return OCRResult(
            left_name=left_text,
            right_name=right_text,
            left_score=left_score,
            right_score=right_score,
            confidence=avg_confidence,
        )

    async def extract_from_url(self, url: str) -> Optional[OCRResult]:
        """Download an image from *url* and extract results.

        Args:
            url: Public URL of the result screenshot.

        Returns:
            An :class:`OCRResult` on success, or ``None``.
        """
        log.debug("Downloading image from %s", url)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.error(
                        "Failed to download image (HTTP %d): %s",
                        resp.status,
                        url,
                    )
                    return None
                image_bytes = await resp.read()

        return await self.extract(image_bytes)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_extractor: Optional[OCRExtractor] = None


def get_extractor() -> OCRExtractor:
    """Return the shared :class:`OCRExtractor` singleton.

    The instance is created lazily on first call.
    """
    global _extractor
    if _extractor is None:
        _extractor = OCRExtractor()
    return _extractor
