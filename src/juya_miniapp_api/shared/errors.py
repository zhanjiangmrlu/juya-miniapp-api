from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from juya_miniapp_api.infrastructure.observability.request_id import get_request_id


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: Mapping[str, object] | None = None,
    ) -> None:
        # 功能:初始化携带错误码和安全说明的业务异常并保存所需依赖与配置
        # 参数:
        #     self: 当前携带错误码和安全说明的业务异常实例
        #     code: 对客户端公开的业务错误码
        #     message: 允许向客户端显示的错误说明
        #     status_code: 对客户端返回的HTTP错误状态码
        #     details: 对客户端安全可见的错误附加字段
        # 返回:无返回值。
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def _error_body(
    request: Request,
    *,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    # 功能:构造包含请求标识的安全错误响应体
    # 参数:
    #     request: FastAPI请求对象
    #     code: 对客户端公开的业务错误码
    #     message: 允许向客户端显示的错误说明
    #     details: 对客户端安全可见的错误附加字段
    # 返回:安全错误码、可见说明、附加字段与请求标识
    return {
        "code": code,
        "message": message,
        "request_id": get_request_id(request),
        "details": dict(details or {}),
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    # 功能:为FastAPI应用注册业务和请求校验异常处理器
    # 参数:
    #     app: 待安装路由、中间件或异常处理器的FastAPI应用
    # 返回:无返回值。
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 功能:将业务异常转换为安全HTTP错误响应
        # 参数:
        #     request: FastAPI请求对象
        #     exc: 本次捕获的业务或请求模型校验异常
        # 返回:安全JSON错误响应
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                request,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 功能:隐藏输入内容并返回请求校验失败的安全响应
        # 参数:
        #     request: FastAPI请求对象
        #     exc: 本次捕获的业务或请求模型校验异常
        # 返回:安全JSON错误响应
        safe_errors = [
            {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request,
                code="VALIDATION_ERROR",
                message="请求参数不正确",
                details={"errors": safe_errors},
            ),
        )
