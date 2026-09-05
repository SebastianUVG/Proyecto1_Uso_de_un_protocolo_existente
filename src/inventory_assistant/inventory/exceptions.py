"""Domain-specific inventory exceptions."""


class InventoryError(Exception):
    """Base class for expected inventory errors."""


class InvalidInventoryQueryError(InventoryError, ValueError):
    """Raised when a query contains invalid or contradictory arguments."""


class ProductNotFoundError(InventoryError, LookupError):
    """Raised when no product matches the requested identifier."""

