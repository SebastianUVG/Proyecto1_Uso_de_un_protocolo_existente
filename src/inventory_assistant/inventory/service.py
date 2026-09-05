"""Business operations for future inventory MCP tools."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .exceptions import (
    DuplicateSKUError,
    InsufficientStockError,
    InvalidInventoryQueryError,
    ProductNotFoundError,
)
from .models import (
    InventoryAdjustmentResult,
    InventoryMovement,
    MovementRanking,
    MovementType,
    Product,
    ProductActivity,
    ProductCreation,
    ProductUpdateResult,
    ProductStock,
    RankingDirection,
    RankingMetric,
    RestockRecommendation,
    StockStatus,
    StockMovementResult,
)
from .repository import InventoryRepository


class InventoryService:
    """Inventory rules with no dependency on SQLite or a future protocol layer."""

    def __init__(self, repository: InventoryRepository) -> None:
        self._repository = repository

    def get_product_stock(
        self,
        *,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
    ) -> ProductStock:
        product = self._resolve_product(product_id=product_id, sku=sku, name=name)
        return ProductStock(product=product, status=self._stock_status(product))

    def add_product(
        self,
        *,
        sku: str,
        name: str,
        category: str,
        initial_stock: int,
        minimum_stock: int,
        target_stock: int,
        unit_price: Decimal | int | float | str,
        movement_date: date | None = None,
    ) -> ProductCreation:
        normalized_sku = self._required_text(sku, "sku")
        normalized_name = self._required_text(name, "name")
        normalized_category = self._required_text(category, "category")
        self._non_negative_integer(initial_stock, "initial_stock")
        self._non_negative_integer(minimum_stock, "minimum_stock")
        self._non_negative_integer(target_stock, "target_stock")
        if target_stock < minimum_stock:
            raise InvalidInventoryQueryError(
                "target_stock must be greater than or equal to minimum_stock"
            )
        unit_price_cents = self._price_to_cents(unit_price)
        if self._repository.get_product_by_sku(normalized_sku) is not None:
            raise DuplicateSKUError("a product with this SKU already exists")
        return self._repository.create_product(
            sku=normalized_sku,
            name=normalized_name,
            category=normalized_category,
            initial_stock=initial_stock,
            minimum_stock=minimum_stock,
            target_stock=target_stock,
            unit_price_cents=unit_price_cents,
            movement_date=movement_date or date.today(),
        )

    def record_inventory_entry(
        self,
        *,
        quantity: int,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
        reason: str | None = None,
        reference: str | None = None,
        movement_date: date | None = None,
    ) -> StockMovementResult:
        return self._record_inventory_change(
            movement_type=MovementType.IN,
            quantity=quantity,
            product_id=product_id,
            sku=sku,
            name=name,
            reason=reason,
            reference=reference,
            movement_date=movement_date,
        )

    def record_inventory_exit(
        self,
        *,
        quantity: int,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
        reason: str | None = None,
        reference: str | None = None,
        movement_date: date | None = None,
    ) -> StockMovementResult:
        return self._record_inventory_change(
            movement_type=MovementType.OUT,
            quantity=quantity,
            product_id=product_id,
            sku=sku,
            name=name,
            reason=reason,
            reference=reference,
            movement_date=movement_date,
        )

    def list_products(
        self,
        *,
        category: str | None = None,
        min_stock: int | None = None,
        max_stock: int | None = None,
        min_price: Decimal | int | float | str | None = None,
        max_price: Decimal | int | float | str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[Product]:
        self._validate_limit(limit)
        self._optional_non_negative_integer(min_stock, "min_stock")
        self._optional_non_negative_integer(max_stock, "max_stock")
        if min_stock is not None and max_stock is not None and min_stock > max_stock:
            raise InvalidInventoryQueryError(
                "min_stock cannot be greater than max_stock"
            )
        min_price_cents = (
            self._price_to_cents(min_price) if min_price is not None else None
        )
        max_price_cents = (
            self._price_to_cents(max_price) if max_price is not None else None
        )
        if (
            min_price_cents is not None
            and max_price_cents is not None
            and min_price_cents > max_price_cents
        ):
            raise InvalidInventoryQueryError(
                "min_price cannot be greater than max_price"
            )
        return self._repository.list_products(
            self._optional_text(category, "category", maximum=200),
            min_stock=min_stock,
            max_stock=max_stock,
            min_price_cents=min_price_cents,
            max_price_cents=max_price_cents,
            search=self._optional_text(search, "search", maximum=200),
            limit=limit,
        )

    def update_product(
        self,
        *,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
        new_name: str | None = None,
        category: str | None = None,
        minimum_stock: int | None = None,
        target_stock: int | None = None,
        unit_price: Decimal | int | float | str | None = None,
    ) -> ProductUpdateResult:
        updates_provided = (
            new_name is not None,
            category is not None,
            minimum_stock is not None,
            target_stock is not None,
            unit_price is not None,
        )
        if not any(updates_provided):
            raise InvalidInventoryQueryError(
                "provide at least one product field to update"
            )
        product = self._resolve_product(product_id=product_id, sku=sku, name=name)
        resulting_name = (
            self._required_text(new_name, "new_name", maximum=200)
            if new_name is not None
            else product.name
        )
        resulting_category = (
            self._required_text(category, "category", maximum=200)
            if category is not None
            else product.category
        )
        if minimum_stock is not None:
            self._non_negative_integer(minimum_stock, "minimum_stock")
        if target_stock is not None:
            self._non_negative_integer(target_stock, "target_stock")
        resulting_minimum = (
            minimum_stock if minimum_stock is not None else product.minimum_stock
        )
        resulting_target = (
            target_stock if target_stock is not None else product.target_stock
        )
        if resulting_target < resulting_minimum:
            raise InvalidInventoryQueryError(
                "target_stock must be greater than or equal to minimum_stock"
            )
        resulting_price_cents = (
            self._price_to_cents(unit_price)
            if unit_price is not None
            else product.unit_price_cents
        )
        return self._repository.update_product(
            product_id=product.id,
            name=resulting_name,
            category=resulting_category,
            minimum_stock=resulting_minimum,
            target_stock=resulting_target,
            unit_price_cents=resulting_price_cents,
        )

    def adjust_inventory(
        self,
        *,
        counted_stock: int,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
        reason: str | None = None,
        reference: str | None = None,
        movement_date: date | None = None,
    ) -> InventoryAdjustmentResult:
        self._non_negative_integer(counted_stock, "counted_stock")
        product = self._resolve_product(product_id=product_id, sku=sku, name=name)
        return self._repository.adjust_inventory(
            product_id=product.id,
            counted_stock=counted_stock,
            movement_date=movement_date or date.today(),
            reason=self._optional_text(reason, "reason", maximum=200),
            reference=self._optional_text(reference, "reference", maximum=200),
        )

    def get_low_stock_products(
        self,
        *,
        category: str | None = None,
        include_out_of_stock: bool = True,
        limit: int = 100,
    ) -> list[ProductStock]:
        self._validate_limit(limit)
        products = [
            product
            for product in self._repository.list_products(category)
            if product.current_stock <= product.minimum_stock
            and (include_out_of_stock or product.current_stock > 0)
        ]
        products.sort(key=lambda product: (product.current_stock, product.name.casefold()))
        return [
            ProductStock(product=product, status=self._stock_status(product))
            for product in products[:limit]
        ]

    def get_restock_recommendations(
        self, *, category: str | None = None, limit: int = 100
    ) -> list[RestockRecommendation]:
        self._validate_limit(limit)
        recommendations = [
            RestockRecommendation(
                product=product,
                status=self._stock_status(product),
                recommended_quantity=product.target_stock - product.current_stock,
            )
            for product in self._repository.list_products(category)
            if product.current_stock <= product.minimum_stock
        ]
        status_priority = {
            StockStatus.OUT_OF_STOCK: 0,
            StockStatus.BELOW_MINIMUM: 1,
            StockStatus.AT_MINIMUM: 2,
        }
        recommendations.sort(
            key=lambda item: (
                status_priority[item.status],
                -item.recommended_quantity,
                item.product.name.casefold(),
            )
        )
        return recommendations[:limit]

    def get_product_movements(
        self,
        *,
        product_id: int | None = None,
        sku: str | None = None,
        name: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        movement_type: MovementType | None = None,
        limit: int = 500,
    ) -> list[InventoryMovement]:
        self._validate_limit(limit, maximum=500)
        self._validate_date_range(date_from, date_to)
        product = self._resolve_product(product_id=product_id, sku=sku, name=name)
        movements = self._repository.list_movements(
            product_id=product.id,
            date_from=date_from,
            date_to=date_to,
            movement_type=movement_type,
        )
        return movements[:limit]

    def get_inactive_products(
        self,
        *,
        inactive_days: int,
        as_of: date | None = None,
        category: str | None = None,
        include_never_moved: bool = True,
        limit: int = 100,
    ) -> list[ProductActivity]:
        if inactive_days < 1:
            raise InvalidInventoryQueryError("inactive_days must be at least 1")
        self._validate_limit(limit)
        reference_date = as_of or date.today()
        cutoff = reference_date - timedelta(days=inactive_days)
        products = self._repository.list_products(category)
        product_ids = {product.id for product in products}
        latest_by_product: dict[int, date] = {}
        for movement in self._repository.list_movements():
            if movement.product_id not in product_ids:
                continue
            previous = latest_by_product.get(movement.product_id)
            if previous is None or movement.movement_date > previous:
                latest_by_product[movement.product_id] = movement.movement_date

        activities: list[ProductActivity] = []
        for product in products:
            last_date = latest_by_product.get(product.id)
            if last_date is None:
                if include_never_moved:
                    activities.append(
                        ProductActivity(
                            product=product,
                            last_movement_date=None,
                            inactive_days=None,
                        )
                    )
            elif last_date < cutoff:
                activities.append(
                    ProductActivity(
                        product=product,
                        last_movement_date=last_date,
                        inactive_days=(reference_date - last_date).days,
                    )
                )

        activities.sort(
            key=lambda item: (
                item.last_movement_date is not None,
                item.last_movement_date or date.min,
                item.product.name.casefold(),
            )
        )
        return activities[:limit]

    def get_product_movement_ranking(
        self,
        *,
        date_from: date,
        date_to: date,
        direction: RankingDirection = RankingDirection.MOST,
        metric: RankingMetric = RankingMetric.UNITS,
        movement_type: MovementType | None = None,
        category: str | None = None,
        include_zero_activity: bool = False,
        limit: int = 100,
    ) -> list[MovementRanking]:
        self._validate_date_range(date_from, date_to)
        self._validate_limit(limit)
        products = self._repository.list_products(category)
        totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for movement in self._repository.list_movements(
            date_from=date_from,
            date_to=date_to,
            movement_type=movement_type,
        ):
            totals[movement.product_id][0] += movement.quantity
            totals[movement.product_id][1] += 1

        rankings = [
            MovementRanking(
                product=product,
                total_units=totals[product.id][0],
                transaction_count=totals[product.id][1],
            )
            for product in products
            if include_zero_activity or product.id in totals
        ]

        def measured_value(item: MovementRanking) -> int:
            if metric is RankingMetric.UNITS:
                return item.total_units
            return item.transaction_count

        if direction is RankingDirection.MOST:
            rankings.sort(
                key=lambda item: (-measured_value(item), item.product.name.casefold())
            )
        else:
            rankings.sort(
                key=lambda item: (measured_value(item), item.product.name.casefold())
            )
        return rankings[:limit]

    def _resolve_product(
        self,
        *,
        product_id: int | None,
        sku: str | None,
        name: str | None,
    ) -> Product:
        provided = [product_id is not None, sku is not None, name is not None]
        if sum(provided) != 1:
            raise InvalidInventoryQueryError(
                "provide exactly one of product_id, sku, or name"
            )
        if product_id is not None:
            if product_id < 1:
                raise InvalidInventoryQueryError("product_id must be positive")
            product = self._repository.get_product_by_id(product_id)
        elif sku is not None:
            if not sku.strip():
                raise InvalidInventoryQueryError("sku cannot be empty")
            product = self._repository.get_product_by_sku(sku.strip())
        else:
            assert name is not None
            if not name.strip():
                raise InvalidInventoryQueryError("name cannot be empty")
            product = self._repository.get_product_by_name(name.strip())
        if product is None:
            raise ProductNotFoundError("product not found")
        return product

    def _record_inventory_change(
        self,
        *,
        movement_type: MovementType,
        quantity: int,
        product_id: int | None,
        sku: str | None,
        name: str | None,
        reason: str | None,
        reference: str | None,
        movement_date: date | None,
    ) -> StockMovementResult:
        self._positive_integer(quantity, "quantity")
        product = self._resolve_product(product_id=product_id, sku=sku, name=name)
        if movement_type is MovementType.OUT and quantity > product.current_stock:
            raise InsufficientStockError(
                f"insufficient stock: available {product.current_stock}, "
                f"requested {quantity}"
            )
        return self._repository.record_stock_movement(
            product_id=product.id,
            movement_type=movement_type,
            quantity=quantity,
            movement_date=movement_date or date.today(),
            reason=self._optional_text(reason, "reason"),
            reference=self._optional_text(reference, "reference"),
        )

    @staticmethod
    def _stock_status(product: Product) -> StockStatus:
        if product.current_stock == 0:
            return StockStatus.OUT_OF_STOCK
        if product.current_stock < product.minimum_stock:
            return StockStatus.BELOW_MINIMUM
        if product.current_stock == product.minimum_stock:
            return StockStatus.AT_MINIMUM
        if product.current_stock > product.target_stock:
            return StockStatus.EXCESS
        return StockStatus.NORMAL

    @staticmethod
    def _validate_limit(limit: int, maximum: int = 100) -> None:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= maximum
        ):
            raise InvalidInventoryQueryError(
                f"limit must be between 1 and {maximum}"
            )

    @staticmethod
    def _validate_date_range(
        date_from: date | None, date_to: date | None
    ) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise InvalidInventoryQueryError("date_from cannot be after date_to")

    @staticmethod
    def _required_text(
        value: str, name: str, *, maximum: int | None = None
    ) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvalidInventoryQueryError(f"{name} cannot be empty")
        normalized = value.strip()
        if maximum is not None and len(normalized) > maximum:
            raise InvalidInventoryQueryError(
                f"{name} must contain at most {maximum} characters"
            )
        return normalized

    @classmethod
    def _optional_text(
        cls,
        value: str | None,
        name: str,
        *,
        maximum: int | None = None,
    ) -> str | None:
        return (
            None
            if value is None
            else cls._required_text(value, name, maximum=maximum)
        )

    @classmethod
    def _optional_non_negative_integer(
        cls, value: int | None, name: str
    ) -> None:
        if value is not None:
            cls._non_negative_integer(value, name)

    @staticmethod
    def _non_negative_integer(value: int, name: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidInventoryQueryError(
                f"{name} must be a non-negative integer"
            )

    @staticmethod
    def _positive_integer(value: int, name: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise InvalidInventoryQueryError(f"{name} must be a positive integer")

    @staticmethod
    def _price_to_cents(value: Decimal | int | float | str) -> int:
        try:
            price = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise InvalidInventoryQueryError(
                "unit_price must be a non-negative number"
            ) from error
        if not price.is_finite() or price < 0:
            raise InvalidInventoryQueryError(
                "unit_price must be a non-negative number"
            )
        cents = price * 100
        if cents != cents.to_integral_value():
            raise InvalidInventoryQueryError(
                "unit_price must have at most two decimal places"
            )
        return int(cents)
