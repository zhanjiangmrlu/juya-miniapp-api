from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from juya_miniapp_api.integrations.admin_api.schemas import (
    SceneEntry,
    SceneOpenResult,
    SignedResource,
)
from juya_miniapp_api.modules.learning.access_service import AccessService
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.users.router import UserDependency


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_content_access_router(
    catalog: CatalogService,
    access: AccessService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定学习内容访问路由与业务依赖
    # 参数:
    #     catalog: 读取学习目录和场景权限的业务服务
    #     access: 校验场景权限并解析已发布内容的访问服务
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1", tags=["content-access"])

    @router.get("/learning/modules")
    async def modules() -> dict[str, object]:
        # 功能:返回可用学习模块列表
        # 参数:
        #     无形参。
        # 返回:可用学习模块的序列化列表
        items = await catalog.modules()
        return {"items": [item.model_dump(mode="json") for item in items]}

    @router.get("/learning/catalog")
    async def learning_catalog(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:返回当前用户学习目录及访问权限
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户学习目录、摘要与访问权限
        result = await catalog.catalog(user_id, {})
        return result.model_dump(mode="json")

    @router.get("/learning/open-history")
    async def open_history(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:返回用户授权成功打开的场景列表
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户授权成功打开的场景历史列表
        return {"items": await access.open_history(user_id)}

    @router.post("/scenes/{scene_id}/open", response_model=SceneOpenResult)
    async def open_scene(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        # 功能:校验场景访问响应并仅在授权成功后记录打开历史
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:场景访问级别、权限信息与固定发布版本内容
        result = await access.open_scene(user_id, scene_id, idempotency_key, clock())
        return result.model_dump(mode="json")

    @router.get("/scenes/{scene_id}/entries/{entry_id}", response_model=SceneEntry)
    async def scene_entry(
        scene_id: str,
        entry_id: str,
        revision_id: Annotated[str, Query(min_length=1, max_length=64)],
        entry_version: Annotated[int, Query(ge=1)],
        source_locator: Annotated[str, Query(min_length=1, max_length=255)],
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:返回固定发布版本和来源定位的场景词条
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     entry_id: 场景中的词条稳定标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     source_locator: 词条来源在固定场景版本中的定位片段
        #     user_id: 当前操作所属用户的公开标识
        # 返回:固定发布修订和词条版本的英文、释义、来源与发音字段
        result = await access.get_entry(
            user_id, scene_id, entry_id, revision_id, entry_version, source_locator
        )
        return result.model_dump(mode="json")

    @router.post("/media/{target_id}/signed-url")
    async def signed_media(
        target_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:返回当前用户有权访问的媒体签名链接
        # 参数:
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:媒体访问签名URL和有效期
        result = await access.get_signed_media(user_id, target_id, clock())
        return result.model_dump(mode="json")

    @router.get(
        "/scenes/{scene_id}/resources/{resource_id}/signed-url", response_model=SignedResource
    )
    async def signed_resource(
        response: Response,
        scene_id: str,
        resource_id: str,
        revision_id: Annotated[str, Query(min_length=1, max_length=64)],
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:返回当前用户有权访问的固定发布版本资源链接
        # 参数:
        #     response: HTTP响应对象
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     resource_id: 发布内容中的媒体资源标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:资源标识、固定修订版本、签名URL和有效期
        response.headers["Cache-Control"] = "private, no-store"
        result = await access.get_signed_resource(
            user_id, scene_id, resource_id, revision_id, clock()
        )
        return result.model_dump(mode="json")

    @router.get("/me/entitlements")
    async def entitlements(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:读取用户当前的场景授权与到期时间投影
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户授权权益及其到期信息
        result = await catalog.entitlements(user_id)
        return result.model_dump(mode="json")

    return router
