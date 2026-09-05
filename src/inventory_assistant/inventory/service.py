"""Business operations for future inventory MCP tools."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .exceptions import InvalidInventoryQueryError, ProductNotFoundError
from .models import (
    InventoryMovement,
    MovementRanking,
    MovementType,
    Product,
    ProductActivity,
    ProductStock,
    RankingDirection,
    RankingMetric,
    RestockRecommendation,
    StockStatus,
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
        if not 1 <= limit <= maximum:
            raise InvalidInventoryQueryError(
                f"limit must be between 1 and {maximum}"
            )

    @staticmethod
    def _validate_date_range(
        date_from: date | None, date_to: date | None
    ) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise InvalidInventoryQueryError("date_from cannot be after date_to")

