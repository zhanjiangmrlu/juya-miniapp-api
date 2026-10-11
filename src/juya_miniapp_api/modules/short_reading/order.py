import base64
import json
from dataclasses import dataclass

from juya_miniapp_api.modules.favorites.snapshots import request_digest
from juya_miniapp_api.shared.errors import AppError


@dataclass(frozen=True)
class ReadingListCursor:
    sort_order: int
    content_id: str

    def encode(self, user_id: str, order_version: str) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(
                {
                    "scope": request_digest([user_id, "short-reading-order", 3, order_version]),
                    "order": self.sort_order,
                    "id": self.content_id,
                }
            ).encode()
        ).decode()

    @classmethod
    def decode(cls, user_id: str, order_version: str, value: str) -> "ReadingListCursor":
        try:
            if len(value) > 2048:
                raise ValueError("length")
            saved = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
            if (
                saved["scope"] != request_digest([user_id, "short-reading-order", 3, order_version])
                or type(saved["order"]) is not int
                or not -2147483648 <= saved["order"] <= 2147483647
                or not isinstance(saved["id"], str)
                or not 0 < len(saved["id"]) <= 64
            ):
                raise ValueError("scope")
            return cls(saved["order"], saved["id"])
        except (ValueError, TypeError, KeyError) as error:
            raise AppError("CURSOR_INVALID", "内容顺序已变化,请刷新", 422) from error
