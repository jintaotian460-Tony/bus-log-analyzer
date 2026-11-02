"""Supported signal line widths shared by views and saved templates."""

# Pens are cosmetic, so a width of w draws exactly w device pixels: 0.5 and
# 1.5 both rendered like 1 and 2 px, and adjacent menu steps could differ by
# nothing visible. Whole pixels a step apart, out to a presentation-friendly 8.
LINE_WIDTHS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)


def normalise_line_width(value):
    """None keeps the original automatic widths, including selection feedback."""
    if isinstance(value, bool):
        return None
    try:
        width = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return width if width in LINE_WIDTHS else None
