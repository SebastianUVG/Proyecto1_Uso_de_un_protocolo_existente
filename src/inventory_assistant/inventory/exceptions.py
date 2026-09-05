"""Domain-specific inventory exceptions."""


class InventoryError(Exception):
    """Base class for expected inventory errors."""


class InvalidInventoryQueryError(InventoryError, ValueError):
    """Raised when a query contains invalid or contradictory arguments."""


class ProductNotFoundError(InventoryError, LookupError):
    """Raised when no product matches the requested identifier."""


class DuplicateSKUError(InventoryError):
    """Raised when a new product would reuse an existing SKU."""


class DuplicateProductNameError(InventoryError):
    """Raised when a new product would reuse an existing product name."""


class DuplicateMovementReferenceError(InventoryError):
    """Raised when an inventory movement reference is already registered."""


class InsufficientStockError(InventoryError):
    """Raised when an exit would leave a product with negative stock."""
