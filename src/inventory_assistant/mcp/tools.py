"""MCP inventory tool catalog, argument validation, and service adapters."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from inventory_assistant.inventory.exceptions import (
    DuplicateMovementReferenceError,
    DuplicateProductNameError,
    DuplicateSKUError,
    InsufficientStockError,
    InvalidInventoryQueryError,
    ProductNotFoundError,
)
from inventory_assistant.inventory.models import (
    InventoryAdjustmentResult,
    InventoryMovement,
    MovementRanking,
    MovementType,
    Product,
    ProductActivity,
    ProductStock,
    ProductUpdateResult,
    RankingDirection,
    RankingMetric,
    RestockRecommendation,
    StockMovementResult,
)
from inventory_assistant.inventory.service import InventoryService

from .jsonrpc import INVALID_PARAMS, JSONRPCProtocolError


def _product_selector_properties() -> dict[str, Any]:
    return {
        "product_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Internal numeric product identifier.",
        },
        "sku": {
            "type": "string",
            "minLength": 1,
            "description": "Exact product SKU, matched case-insensitively.",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Exact product name, matched case-insensitively.",
        },
    }


def _exactly_one_product_selector() -> list[dict[str, Any]]:
    return [
        {"required": ["product_id"]},
        {"required": ["sku"]},
        {"required": ["name"]},
    ]


TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_product_stock",
        "description": (
            "Get current stock, minimum and target levels, price, and stock status "
            "for one product. Provide exactly one of product_id, sku, or name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": _product_selector_properties(),
            "oneOf": _exactly_one_product_selector(),
            "additionalProperties": False,
        },
    },
    {
        "name": "get_low_stock_products",
        "description": (
            "List products whose current stock is at or below their configured minimum. "
            "Results may be filtered by category and can include or exclude products "
            "with zero stock."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "minLength": 1},
                "include_out_of_stock": {"type": "boolean", "default": True},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_restock_recommendations",
        "description": (
            "Recommend order quantities for products at or below minimum stock. "
            "Each quantity is the difference between target stock and current stock."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "minLength": 1},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product_movements",
        "description": (
            "Get the inventory movement history for one product, newest first. "
            "Provide exactly one product identifier and optionally filter by an "
            "inclusive ISO date range or movement type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_product_selector_properties(),
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
                "movement_type": {
                    "type": "string",
                    "enum": [item.value for item in MovementType],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 500,
                },
            },
            "oneOf": _exactly_one_product_selector(),
            "additionalProperties": False,
        },
    },
    {
        "name": "get_inactive_products",
        "description": (
            "List products with no inventory movement during a requested number of "
            "days. Products that never moved may be included. Use as_of to make a "
            "historical or reproducible query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "inactive_days": {"type": "integer", "minimum": 1},
                "as_of": {"type": "string", "format": "date"},
                "category": {"type": "string", "minLength": 1},
                "include_never_moved": {"type": "boolean", "default": True},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            },
            "required": ["inactive_days"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product_movement_ranking",
        "description": (
            "Rank products by moved units or transaction count in an inclusive date "
            "range. Use MOST for highest movement and LEAST for lowest movement; "
            "movement_type can restrict the ranking to entries, exits, or adjustments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
                "direction": {
                    "type": "string",
                    "enum": [item.value for item in RankingDirection],
                    "default": RankingDirection.MOST.value,
                },
                "metric": {
                    "type": "string",
                    "enum": [item.value for item in RankingMetric],
                    "default": RankingMetric.UNITS.value,
                },
                "movement_type": {
                    "type": "string",
                    "enum": [item.value for item in MovementType],
                },
                "category": {"type": "string", "minLength": 1},
                "include_zero_activity": {"type": "boolean", "default": False},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_products",
        "description": (
            "List inventory products without modifying data. Combine optional exact "
            "category, inclusive stock and unit-price ranges, or a partial name/SKU "
            "search. Use this for product browsing rather than low-stock-only queries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "minLength": 1, "maxLength": 200},
                "min_stock": {"type": "integer", "minimum": 0},
                "max_stock": {"type": "integer", "minimum": 0},
                "min_price": {"type": "number", "minimum": 0},
                "max_price": {"type": "number", "minimum": 0},
                "search": {"type": "string", "minLength": 1, "maxLength": 200},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "add_product",
        "description": (
            "Create one new inventory product. This modifies inventory data and must "
            "not be used to update an existing product. A positive initial_stock is "
            "recorded as an auditable IN movement."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "minLength": 1},
                "name": {"type": "string", "minLength": 1},
                "category": {"type": "string", "minLength": 1},
                "initial_stock": {"type": "integer", "minimum": 0},
                "minimum_stock": {"type": "integer", "minimum": 0},
                "target_stock": {"type": "integer", "minimum": 0},
                "unit_price": {"type": "number", "minimum": 0},
            },
            "required": [
                "sku",
                "name",
                "category",
                "initial_stock",
                "minimum_stock",
                "target_stock",
                "unit_price",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_inventory_entry",
        "description": (
            "Record receipt of a positive quantity for one existing product. This "
            "creates an auditable IN movement and increases current stock atomically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_product_selector_properties(),
                "quantity": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "minLength": 1},
                "reference": {"type": "string", "minLength": 1},
            },
            "required": ["quantity"],
            "oneOf": _exactly_one_product_selector(),
            "additionalProperties": False,
        },
    },
    {
        "name": "record_inventory_exit",
        "description": (
            "Record removal or sale of a positive quantity for one existing product. "
            "This creates an auditable OUT movement and decreases current stock "
            "atomically; it never permits negative stock."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_product_selector_properties(),
                "quantity": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "minLength": 1},
                "reference": {"type": "string", "minLength": 1},
            },
            "required": ["quantity"],
            "oneOf": _exactly_one_product_selector(),
            "additionalProperties": False,
        },
    },
    {
        "name": "update_product",
        "description": (
            "Update administrative fields of one existing product. Select it with "
            "exactly one of product_id, sku, or name, then provide at least one of "
            "new_name, category, minimum_stock, target_stock, or unit_price. SKU, ID, "
            "and current stock cannot be changed with this tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_product_selector_properties(),
                "new_name": {"type": "string", "minLength": 1, "maxLength": 200},
                "category": {"type": "string", "minLength": 1, "maxLength": 200},
                "minimum_stock": {"type": "integer", "minimum": 0},
                "target_stock": {"type": "integer", "minimum": 0},
                "unit_price": {"type": "number", "minimum": 0},
            },
            "allOf": [
                {"oneOf": _exactly_one_product_selector()},
                {
                    "anyOf": [
                        {"required": ["new_name"]},
                        {"required": ["category"]},
                        {"required": ["minimum_stock"]},
                        {"required": ["target_stock"]},
                        {"required": ["unit_price"]},
                    ]
                },
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "adjust_inventory",
        "description": (
            "Set one product's stock to a non-negative physical counted_stock. The "
            "server calculates the difference and atomically records ADJUSTMENT_IN "
            "or ADJUSTMENT_OUT; no movement is created when the count is unchanged."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_product_selector_properties(),
                "counted_stock": {"type": "integer", "minimum": 0},
                "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                "reference": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["counted_stock"],
            "oneOf": _exactly_one_product_selector(),
            "additionalProperties": False,
        },
    },
)


class ToolArgumentError(ValueError):
    """Raised when a known tool receives arguments outside its schema."""


class InventoryToolDispatcher:
    """Validate MCP tool calls and adapt them to InventoryService operations."""

    def __init__(self, service: InventoryService) -> None:
        self._service = service
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get_product_stock": self._get_product_stock,
            "get_low_stock_products": self._get_low_stock_products,
            "get_restock_recommendations": self._get_restock_recommendations,
            "get_product_movements": self._get_product_movements,
            "get_inactive_products": self._get_inactive_products,
            "get_product_movement_ranking": self._get_product_movement_ranking,
            "list_products": self._list_products,
            "add_product": self._add_product,
            "record_inventory_entry": self._record_inventory_entry,
            "record_inventory_exit": self._record_inventory_exit,
            "update_product": self._update_product,
            "adjust_inventory": self._adjust_inventory,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        # JSON round-tripping returns a defensive copy made only of JSON values.
        return json.loads(json.dumps(TOOL_DEFINITIONS))

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                f"Unknown tool: {name}",
                data={"type": "TOOL_NOT_FOUND", "tool": name},
            )
        try:
            payload = handler(arguments)
        except ToolArgumentError as error:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tool arguments",
                data={
                    "type": "TOOL_ARGUMENT_ERROR",
                    "tool": name,
                    "reason": str(error),
                },
            ) from error
        except InvalidInventoryQueryError as error:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tool arguments",
                data={
                    "type": "TOOL_ARGUMENT_ERROR",
                    "tool": name,
                    "reason": str(error),
                },
            ) from error
        except ProductNotFoundError as error:
            return _tool_error("PRODUCT_NOT_FOUND", str(error))
        except DuplicateSKUError as error:
            return _tool_error("DUPLICATE_SKU", str(error))
        except DuplicateProductNameError as error:
            return _tool_error("DUPLICATE_PRODUCT_NAME", str(error))
        except DuplicateMovementReferenceError as error:
            return _tool_error("DUPLICATE_REFERENCE", str(error))
        except InsufficientStockError as error:
            return _tool_error("INSUFFICIENT_STOCK", str(error))
        return _tool_success(payload)

    def _add_product(self, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "sku",
            "name",
            "category",
            "initial_stock",
            "minimum_stock",
            "target_stock",
            "unit_price",
        }
        _validate_keys(arguments, allowed, required=allowed)
        result = self._service.add_product(
            sku=_required_string(arguments, "sku"),
            name=_required_string(arguments, "name"),
            category=_required_string(arguments, "category"),
            initial_stock=_required_int(arguments, "initial_stock", minimum=0),
            minimum_stock=_required_int(arguments, "minimum_stock", minimum=0),
            target_stock=_required_int(arguments, "target_stock", minimum=0),
            unit_price=_required_decimal(arguments, "unit_price", minimum=Decimal(0)),
        )
        return {
            "product": _product_payload(result.product),
            "initial_movement": (
                _movement_payload(result.initial_movement)
                if result.initial_movement is not None
                else None
            ),
        }

    def _record_inventory_entry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._record_inventory_change(arguments, is_entry=True)

    def _record_inventory_exit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._record_inventory_change(arguments, is_entry=False)

    def _record_inventory_change(
        self, arguments: dict[str, Any], *, is_entry: bool
    ) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {"product_id", "sku", "name", "quantity", "reason", "reference"},
            required={"quantity"},
        )
        selector = _parse_product_selector(arguments)
        operation = (
            self._service.record_inventory_entry
            if is_entry
            else self._service.record_inventory_exit
        )
        result = operation(
            **selector,
            quantity=_required_int(arguments, "quantity", minimum=1),
            reason=_optional_string(arguments, "reason"),
            reference=_optional_string(arguments, "reference"),
        )
        return _stock_movement_result_payload(result)

    def _list_products(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {
                "category",
                "min_stock",
                "max_stock",
                "min_price",
                "max_price",
                "search",
                "limit",
            },
        )
        products = self._service.list_products(
            category=_optional_string(arguments, "category", maximum=200),
            min_stock=_optional_int_or_none(arguments, "min_stock", minimum=0),
            max_stock=_optional_int_or_none(arguments, "max_stock", minimum=0),
            min_price=_optional_decimal(arguments, "min_price", minimum=Decimal(0)),
            max_price=_optional_decimal(arguments, "max_price", minimum=Decimal(0)),
            search=_optional_string(arguments, "search", maximum=200),
            limit=_optional_int(arguments, "limit", 100, maximum=100),
        )
        return {
            "count": len(products),
            "products": [_product_payload(product) for product in products],
        }

    def _update_product(self, arguments: dict[str, Any]) -> dict[str, Any]:
        update_fields = {
            "new_name",
            "category",
            "minimum_stock",
            "target_stock",
            "unit_price",
        }
        _validate_keys(
            arguments,
            {"product_id", "sku", "name", *update_fields},
        )
        if not update_fields.intersection(arguments):
            raise ToolArgumentError("provide at least one product field to update")
        selector = _parse_product_selector(arguments)
        result = self._service.update_product(
            **selector,
            new_name=_optional_string(arguments, "new_name", maximum=200),
            category=_optional_string(arguments, "category", maximum=200),
            minimum_stock=_optional_int_or_none(
                arguments, "minimum_stock", minimum=0
            ),
            target_stock=_optional_int_or_none(
                arguments, "target_stock", minimum=0
            ),
            unit_price=_optional_decimal(
                arguments, "unit_price", minimum=Decimal(0)
            ),
        )
        return _product_update_result_payload(result)

    def _adjust_inventory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {"product_id", "sku", "name", "counted_stock", "reason", "reference"},
            required={"counted_stock"},
        )
        selector = _parse_product_selector(arguments)
        result = self._service.adjust_inventory(
            **selector,
            counted_stock=_required_int(arguments, "counted_stock", minimum=0),
            reason=_optional_string(arguments, "reason", maximum=200),
            reference=_optional_string(arguments, "reference", maximum=200),
        )
        return _inventory_adjustment_result_payload(result)

    def _get_product_stock(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(arguments, {"product_id", "sku", "name"})
        selector = _parse_product_selector(arguments)
        stock = self._service.get_product_stock(**selector)
        return _stock_payload(stock)

    def _get_low_stock_products(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {"category", "include_out_of_stock", "limit"},
        )
        results = self._service.get_low_stock_products(
            category=_optional_string(arguments, "category"),
            include_out_of_stock=_optional_bool(
                arguments, "include_out_of_stock", True
            ),
            limit=_optional_int(arguments, "limit", 100, maximum=100),
        )
        return {"count": len(results), "products": [_stock_payload(item) for item in results]}

    def _get_restock_recommendations(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        _validate_keys(arguments, {"category", "limit"})
        results = self._service.get_restock_recommendations(
            category=_optional_string(arguments, "category"),
            limit=_optional_int(arguments, "limit", 100, maximum=100),
        )
        return {
            "count": len(results),
            "recommendations": [_recommendation_payload(item) for item in results],
        }

    def _get_product_movements(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {
                "product_id",
                "sku",
                "name",
                "date_from",
                "date_to",
                "movement_type",
                "limit",
            },
        )
        selector = _parse_product_selector(arguments)
        stock = self._service.get_product_stock(**selector)
        movements = self._service.get_product_movements(
            **selector,
            date_from=_optional_date(arguments, "date_from"),
            date_to=_optional_date(arguments, "date_to"),
            movement_type=_optional_enum(arguments, "movement_type", MovementType),
            limit=_optional_int(arguments, "limit", 500, maximum=500),
        )
        return {
            "product": _product_payload(stock.product),
            "count": len(movements),
            "movements": [_movement_payload(item) for item in movements],
        }

    def _get_inactive_products(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {
                "inactive_days",
                "as_of",
                "category",
                "include_never_moved",
                "limit",
            },
            required={"inactive_days"},
        )
        inactive_days = _required_int(arguments, "inactive_days", minimum=1)
        results = self._service.get_inactive_products(
            inactive_days=inactive_days,
            as_of=_optional_date(arguments, "as_of"),
            category=_optional_string(arguments, "category"),
            include_never_moved=_optional_bool(
                arguments, "include_never_moved", True
            ),
            limit=_optional_int(arguments, "limit", 100, maximum=100),
        )
        return {
            "count": len(results),
            "inactive_days": inactive_days,
            "products": [_activity_payload(item) for item in results],
        }

    def _get_product_movement_ranking(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        _validate_keys(
            arguments,
            {
                "date_from",
                "date_to",
                "direction",
                "metric",
                "movement_type",
                "category",
                "include_zero_activity",
                "limit",
            },
            required={"date_from", "date_to"},
        )
        date_from = _required_date(arguments, "date_from")
        date_to = _required_date(arguments, "date_to")
        direction = _optional_enum(
            arguments, "direction", RankingDirection, RankingDirection.MOST
        )
        metric = _optional_enum(
            arguments, "metric", RankingMetric, RankingMetric.UNITS
        )
        results = self._service.get_product_movement_ranking(
            date_from=date_from,
            date_to=date_to,
            direction=direction,
            metric=metric,
            movement_type=_optional_enum(arguments, "movement_type", MovementType),
            category=_optional_string(arguments, "category"),
            include_zero_activity=_optional_bool(
                arguments, "include_zero_activity", False
            ),
            limit=_optional_int(arguments, "limit", 100, maximum=100),
        )
        return {
            "count": len(results),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "direction": direction.value,
            "metric": metric.value,
            "products": [_ranking_payload(item) for item in results],
        }


def _tool_success(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
        "isError": False,
    }


def _tool_error(error_type: str, message: str) -> dict[str, Any]:
    payload = {"error": {"type": error_type, "message": message}}
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": payload,
        "isError": True,
    }


def _product_payload(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "current_stock": product.current_stock,
        "minimum_stock": product.minimum_stock,
        "target_stock": product.target_stock,
        "unit_price": _decimal_text(product.unit_price),
    }


def _stock_payload(stock: ProductStock) -> dict[str, Any]:
    return {"product": _product_payload(stock.product), "status": stock.status.value}


def _recommendation_payload(item: RestockRecommendation) -> dict[str, Any]:
    return {
        "product": _product_payload(item.product),
        "status": item.status.value,
        "recommended_quantity": item.recommended_quantity,
    }


def _movement_payload(movement: InventoryMovement) -> dict[str, Any]:
    return {
        "id": movement.id,
        "product_id": movement.product_id,
        "movement_type": movement.movement_type.value,
        "quantity": movement.quantity,
        "movement_date": movement.movement_date.isoformat(),
        "reason": movement.reason,
        "reference": movement.reference,
    }


def _activity_payload(activity: ProductActivity) -> dict[str, Any]:
    return {
        "product": _product_payload(activity.product),
        "last_movement_date": (
            activity.last_movement_date.isoformat()
            if activity.last_movement_date is not None
            else None
        ),
        "inactive_days": activity.inactive_days,
    }


def _ranking_payload(ranking: MovementRanking) -> dict[str, Any]:
    return {
        "product": _product_payload(ranking.product),
        "total_units": ranking.total_units,
        "transaction_count": ranking.transaction_count,
    }


def _stock_movement_result_payload(result: StockMovementResult) -> dict[str, Any]:
    return {
        "product": _product_payload(result.product),
        "quantity": result.quantity,
        "previous_stock": result.previous_stock,
        "new_stock": result.new_stock,
        "movement": _movement_payload(result.movement),
    }


def _product_update_result_payload(result: ProductUpdateResult) -> dict[str, Any]:
    previous_values = _administrative_product_values(result.previous_product)
    new_values = _administrative_product_values(result.product)
    return {
        "product": _product_payload(result.product),
        "previous_values": previous_values,
        "new_values": new_values,
        "changed_fields": list(result.changed_fields),
    }


def _administrative_product_values(product: Product) -> dict[str, Any]:
    return {
        "name": product.name,
        "category": product.category,
        "minimum_stock": product.minimum_stock,
        "target_stock": product.target_stock,
        "unit_price": _decimal_text(product.unit_price),
    }


def _inventory_adjustment_result_payload(
    result: InventoryAdjustmentResult,
) -> dict[str, Any]:
    return {
        "product": _product_payload(result.product),
        "previous_stock": result.previous_stock,
        "counted_stock": result.counted_stock,
        "difference": result.difference,
        "adjustment_type": (
            result.adjustment_type.value
            if result.adjustment_type is not None
            else None
        ),
        "movement": (
            _movement_payload(result.movement)
            if result.movement is not None
            else None
        ),
        "resulting_stock": result.resulting_stock,
    }


def _decimal_text(value: Decimal) -> str:
    return format(value, ".2f")


def _validate_keys(
    arguments: dict[str, Any],
    allowed: set[str],
    *,
    required: set[str] | None = None,
) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise ToolArgumentError(
            "unknown argument(s): " + ", ".join(sorted(unknown))
        )
    missing = (required or set()) - set(arguments)
    if missing:
        raise ToolArgumentError(
            "missing required argument(s): " + ", ".join(sorted(missing))
        )


def _parse_product_selector(arguments: dict[str, Any]) -> dict[str, Any]:
    keys = [key for key in ("product_id", "sku", "name") if key in arguments]
    if len(keys) != 1:
        raise ToolArgumentError("provide exactly one of product_id, sku, or name")
    key = keys[0]
    if key == "product_id":
        return {key: _required_int(arguments, key, minimum=1)}
    return {key: _required_string(arguments, key)}


def _required_string(
    arguments: dict[str, Any], key: str, *, maximum: int | None = None
) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"{key} must be a non-empty string")
    normalized = value.strip()
    if maximum is not None and len(normalized) > maximum:
        raise ToolArgumentError(f"{key} must contain at most {maximum} characters")
    return normalized


def _optional_string(
    arguments: dict[str, Any], key: str, *, maximum: int | None = None
) -> str | None:
    if key not in arguments:
        return None
    return _required_string(arguments, key, maximum=maximum)


def _required_int(
    arguments: dict[str, Any],
    key: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolArgumentError(f"{key} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        maximum_text = f" and at most {maximum}" if maximum is not None else ""
        raise ToolArgumentError(
            f"{key} must be at least {minimum}{maximum_text}"
        )
    return value


def _optional_int(
    arguments: dict[str, Any],
    key: str,
    default: int,
    *,
    maximum: int,
) -> int:
    if key not in arguments:
        return default
    return _required_int(arguments, key, minimum=1, maximum=maximum)


def _optional_int_or_none(
    arguments: dict[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    if key not in arguments:
        return None
    return _required_int(arguments, key, minimum=minimum, maximum=maximum)


def _required_decimal(
    arguments: dict[str, Any], key: str, *, minimum: Decimal
) -> Decimal:
    value = arguments.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolArgumentError(f"{key} must be a number")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ToolArgumentError(f"{key} must be a number") from error
    if not parsed.is_finite() or parsed < minimum:
        raise ToolArgumentError(f"{key} must be at least {minimum}")
    return parsed


def _optional_decimal(
    arguments: dict[str, Any], key: str, *, minimum: Decimal
) -> Decimal | None:
    if key not in arguments:
        return None
    return _required_decimal(arguments, key, minimum=minimum)


def _optional_bool(arguments: dict[str, Any], key: str, default: bool) -> bool:
    if key not in arguments:
        return default
    value = arguments[key]
    if not isinstance(value, bool):
        raise ToolArgumentError(f"{key} must be a boolean")
    return value


def _required_date(arguments: dict[str, Any], key: str) -> date:
    value = _required_string(arguments, key)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ToolArgumentError(f"{key} must use ISO format YYYY-MM-DD") from error


def _optional_date(arguments: dict[str, Any], key: str) -> date | None:
    if key not in arguments:
        return None
    return _required_date(arguments, key)


def _optional_enum(
    arguments: dict[str, Any],
    key: str,
    enum_type: type[Any],
    default: Any = None,
) -> Any:
    if key not in arguments:
        return default
    value = arguments[key]
    if not isinstance(value, str):
        raise ToolArgumentError(f"{key} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ToolArgumentError(f"{key} must be one of: {allowed}") from error
