"""
caption_gen.py — Gemini AI Caption Generator

Generates engaging tweet captions using the Gemini API, with fallback
to a simple title + hashtag strategy when the API is unavailable.
"""

import re
import time
import logging
import functools
import warnings

from db import get_setting

logger = logging.getLogger("clipflow.caption_gen")

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_CAPTION_TEMPLATE_X = (
    'Write an engaging tweet caption for a video titled "{title}". '
    'Description: {description}. '
    'Keep it under 250 characters, include 2-3 relevant hashtags, '
    'make it attention-grabbing.'
)

DEFAULT_CAPTION_TEMPLATE_IG_TIKTOK = (
    'Write an engaging social media post caption (for Instagram Reels/TikTok) for a video titled "{title}". '
    'Description: {description}. '
    'Write a rich, engaging description (1-3 sentences) detailing the video context. '
    'Include 5-10 relevant and highly active hashtags to improve SEO and recommendation reach. '
    'Make it attention-grabbing and highly readable with spacing and friendly emojis.'
)

# Words to exclude from auto-generated hashtags
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "this", "that", "was", "are",
    "be", "has", "had", "have", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "not", "no", "so", "if",
    "how", "what", "when", "where", "who", "which", "why", "up", "out",
    "all", "its", "my", "your", "his", "her", "our", "just", "very",
    "about", "into", "over", "after", "before", "more", "than", "then",
    "also", "been", "some", "any", "new", "old", "one", "two", "vs",
})


# ── Helpers ───────────────────────────────────────────────────────────

def _retry(max_retries: int = 3, base_delay: float = 1.0, exceptions=(Exception,)):
    """Retry decorator with exponential backoff for API calls."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


def _is_url(text: str) -> bool:
    """Check if a string is primarily a URL."""
    if not text:
        return False
    text = text.strip()
    pattern = r'^(?:https?://|www\.|t\.co/|x\.com/|twitter\.com/)\S+$'
    return bool(re.match(pattern, text, re.IGNORECASE))


def _generate_hashtags(title: str, max_tags: int = 3) -> list[str]:
    """
    Generate hashtags from title words.

    Picks the longest meaningful words from the title, capitalizes them,
    and prefixes with #.
    """
    if not title or _is_url(title):
        return []
    # Extract words, remove non-alphanumeric characters
    words = re.findall(r'[a-zA-Z]+', title)
    # Filter out stop words and short words
    meaningful = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]
    # Sort by length (prefer longer, more descriptive words), then take top N
    meaningful.sort(key=len, reverse=True)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for w in meaningful:
        lower = w.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(w.capitalize())
    return [f"#{w}" for w in unique[:max_tags]]


def _build_fallback_caption(title: str, platform: str = "x") -> str:
    """Build a simple caption from title + auto-generated hashtags."""
    if _is_url(title):
        return ""
    max_len = 2200 if platform.lower() in ("instagram", "tiktok") else 280
    max_tags = 10 if platform.lower() in ("instagram", "tiktok") else 3

    hashtags = _generate_hashtags(title, max_tags=max_tags)
    hashtag_str = " ".join(hashtags)

    # Ensure total length stays within X limits
    max_title_len = max_len - len(hashtag_str) - 2  # 2 for newlines
    if len(title) > max_title_len:
        title = title[:max_title_len - 3].rstrip() + "..."

    caption = f"{title}\n\n{hashtag_str}" if hashtag_str else title
    return caption.strip()


# ── Core Function ─────────────────────────────────────────────────────

def generate_caption(title: str, description: str = "", template: str = None, platform: str = "x") -> str:
    """
    Generate an engaging caption for a video based on target platform.

    Uses Gemini API if an API key is configured in db settings (key: 'gemini_api_key').
    Falls back to title + auto-generated hashtags if API is unavailable or fails.

    Args:
        title: Video title (required)
        description: Video description (optional)
        template: Custom prompt template. If None, uses default.
        platform: Destination platform ('x', 'instagram', 'tiktok')

    Returns:
        Generated caption string
    """
    if not title:
        logger.warning("generate_caption called with empty title")
        return "Check out this video!"

    if _is_url(title):
        if description and not _is_url(description):
            title = description
        else:
            title = "Check out this video!"

    if not title:
        logger.info("Title is empty or a URL and no fallback description is available. Returning fallback caption.")
        return "Check out this video!"

    logger.info(f"Generating caption for {platform.upper()}: '{title[:60]}...'")

    # Expose max limit based on platform
    max_len = 2200 if platform.lower() in ("instagram", "tiktok") else 280

    # Check for Gemini API key
    api_key = get_setting("gemini_api_key", "")

    if api_key:
        try:
            caption = _call_gemini(api_key, title, description, template, platform)
            if caption:
                # Enforce character limit
                if len(caption) > max_len:
                    caption = caption[:max_len - 3].rstrip() + "..."
                return caption
        except Exception as e:
            logger.error(f"Gemini API failed after retries: {e}")
    else:
        logger.info("No Gemini API key configured, using fallback caption")

    # Fallback: title + hashtags
    fallback = _build_fallback_caption(title, platform)
    return fallback


@_retry(max_retries=3, base_delay=2.0)
def _call_gemini(api_key: str, title: str, description: str, template: str = None, platform: str = "x") -> str:
    """
    Call the Gemini API to generate a caption.
    """
    # Suppress deprecation warnings from the genai library
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        import google.generativeai as genai

    genai.configure(api_key=api_key)

    # Get prompt template based on destination platform
    if template is None:
        plat_key = platform.lower()
        if plat_key == "instagram":
            default_tpl = (
                "Write a visual, engaging Instagram Reel caption for a video titled \"{title}\". "
                "Description: {description}. Use story-style formatting with paragraph breaks, relevant emojis, "
                "and 8-10 targeted hashtags to maximize discovery."
            )
            template = get_setting("caption_template_instagram", "") or get_setting("caption_template", default_tpl)
        elif plat_key == "tiktok":
            default_tpl = (
                "Write a viral TikTok video caption for a video titled \"{title}\". "
                "Description: {description}. Start with a strong 3-word attention hook, "
                "keep it punchy with friendly emojis, and add 4-5 relevant hashtags."
            )
            template = get_setting("caption_template_tiktok", "") or get_setting("caption_template", default_tpl)
        else:
            default_tpl = (
                "Write a short, high-converting tweet for a video titled \"{title}\". "
                "Description: {description}. Keep it under 240 characters, include 2 relevant hashtags, "
                "and make it attention-grabbing."
            )
            template = get_setting("caption_template_x", "") or get_setting("caption_template", default_tpl)

    # Build the prompt
    try:
        prompt = template.format(
            title=title,
            description=description or "No description available"
        )
    except (KeyError, ValueError) as fe:
        logger.warning(f"Failed to format custom template: {fe}. Falling back to default template.")
        default_tpl = DEFAULT_CAPTION_TEMPLATE_IG_TIKTOK if platform.lower() in ("instagram", "tiktok") else DEFAULT_CAPTION_TEMPLATE_X
        prompt = default_tpl.format(
            title=title,
            description=description or "No description available"
        )

    logger.debug(f"Gemini prompt: {prompt[:100]}...")

    # Call Gemini
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(prompt)

    if response and response.text:
        caption = response.text.strip()
        # Clean up any markdown formatting the model might add
        caption = caption.strip('"\'')
        # Remove any leading/trailing whitespace or newlines
        caption = " ".join(caption.split())
        return caption

    raise ValueError("Gemini returned empty response")
