"""Read-only demonstration of the inventory repository and service."""

from datetime import date

from inventory_assistant.config import DatabaseConfig
from inventory_assistant.inventory import (
    InventoryService,
    MovementType,
    RankingDirection,
    RankingMetric,
)
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository


def main() -> None:
    config = DatabaseConfig.from_env()
    repository = SQLiteInventoryRepository(config.path)
    service = InventoryService(repository)

    products = repository.list_products()
    low_stock = service.get_low_stock_products()
    recommendations = service.get_restock_recommendations()
    inactive = service.get_inactive_products(
        inactive_days=90,
        as_of=date(2026, 9, 1),
    )
    ranking = service.get_product_movement_ranking(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 9, 1),
        movement_type=MovementType.OUT,
        metric=RankingMetric.UNITS,
        direction=RankingDirection.MOST,
        limit=3,
    )

    print(f"Database: {config.path.resolve()}")
    print(f"Products: {len(products)}")
    print("\nLow stock:")
    for item in low_stock:
        print(
            f"- {item.product.sku}: {item.product.current_stock} units "
            f"({item.status.value})"
        )
    print("\nRestock recommendations:")
    for item in recommendations:
        print(f"- {item.product.sku}: order {item.recommended_quantity} units")
    print("\nInactive for more than 90 days (as of 2026-09-01):")
    for item in inactive:
        last_seen = item.last_movement_date or "never"
        print(f"- {item.product.sku}: last movement {last_seen}")
    print("\nMost outgoing units in August 2026:")
    for item in ranking:
        print(f"- {item.product.sku}: {item.total_units} units")


if __name__ == "__main__":
    main()

