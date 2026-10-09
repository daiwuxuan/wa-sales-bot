"""领域异常。

这些异常会被工具层捕获并转成 `{"ok": false, "error_code": ...}` 的
结构化结果回填给模型，而不是抛成 HTTP 500。模型看到错误码能自我纠正
（比如换个颜色再查一次），看到 500 只能瞎猜。

error_code 是稳定契约，前端和 prompt 都依赖它，不要随意改名。
"""

from __future__ import annotations


class DomainError(Exception):
    error_code = "domain_error"

    def __init__(self, message: str = "", **details):
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        self.details = details

    def to_payload(self) -> dict:
        payload: dict = {"ok": False, "error_code": self.error_code, "message": self.message}
        # 注意：错误响应里绝不带任何价格字段。
        # 否则模型会从错误信息里「捡」到没被授权的数字。
        payload.update(self.details)
        return payload


class VariantNotFound(DomainError):
    error_code = "variant_not_found"


class ProductNotFound(DomainError):
    error_code = "product_not_found"


class OutOfStock(DomainError):
    error_code = "out_of_stock"


class PriceBelowFloor(DomainError):
    error_code = "price_below_floor"


class QuoteExpired(DomainError):
    error_code = "quote_expired"


class QuoteAlreadyConsumed(DomainError):
    error_code = "quote_already_consumed"


class PriceChangedDuringCheckout(DomainError):
    error_code = "price_changed"


class CustomerNotFound(DomainError):
    error_code = "customer_not_found"


class InvalidArgument(DomainError):
    error_code = "invalid_argument"


class UnsupportedMedia(DomainError):
    error_code = "unsupported_media"
