"""The six tones a badge is drawn in, by name."""

from collections.abc import Mapping
from typing import Any, Literal

from adminsite.exceptions import AdminSiteError

# In the order of the stylesheet's classes: tone-0 is amber and tone-5 rose.
TONE_NAMES = ("amber", "blue", "green", "grey", "violet", "rose")

# For a value that means nothing good or bad, and for one no tone is given.
NEUTRAL = TONE_NAMES.index("grey")

Tone = Literal["amber", "blue", "green", "grey", "violet", "rose"]

# How a field colours its badges: one tone for every value, or one for each
# value, where None draws that value with no badge at all.
Tones = Tone | Mapping[Any, Tone | None]


def tone_number(field: str, tone: object) -> int:
    """The class number of a tone, or an error that lists the tones there are."""
    if tone not in TONE_NAMES:
        raise AdminSiteError(
            f"The field {field!r} gives the tone {tone!r}. "
            "The tones are amber, blue, green, grey, violet and rose."
        )
    return TONE_NAMES.index(str(tone))
