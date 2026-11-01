"""Supported signal line widths shared by views and saved templates."""

LINE_WIDTHS = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)


def normalise_line_width(value):
    """None keeps the original automatic widths, including selection feedback."""
    if isinstance(value, bool):
        return None
    try:
        width = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return width if width in LINE_WIDTHS else None
