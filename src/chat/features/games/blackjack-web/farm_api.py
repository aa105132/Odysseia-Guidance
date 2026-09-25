"""灵圃路由：复用游戏身份认证，客户端不能指定操作账户或产出价格。"""

from importlib import import_module
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


_farm_module = import_module("src.chat.features.games.blackjack-web.farm_service")
FarmService = _farm_module.FarmService
FarmError = _farm_module.FarmError


class FarmActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["buy_seed", "plant", "water", "pest", "harvest", "sell", "expand", "upgrade_aura", "steal", "buy_pet", "equip_pet", "buy_item", "use_item"]
    request_id: Annotated[StrictStr, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
    plot_id: Annotated[StrictInt, Field(ge=1, le=12)] | None = None
    crop_id: Annotated[StrictStr, Field(min_length=1, max_length=32, pattern=r"^[a-z_]+$")] | None = None
    pet_id: Annotated[StrictStr, Field(min_length=1, max_length=32, pattern=r"^[a-z_]+$")] | None = None
    item_id: Annotated[StrictStr, Field(min_length=1, max_length=32, pattern=r"^[a-z_]+$")] | None = None
    quantity: Annotated[StrictInt, Field(ge=1, le=9999)] = 1
    quality: Literal["normal", "spirit", "celestial"] = "normal"
    target_user_id: StrictInt | StrictStr | None = None

    @model_validator(mode="after")
    def validate_action_fields(self):
        fields = {
            "buy_seed": ({"crop_id"}, {"crop_id", "quantity"}),
            "plant": ({"crop_id", "plot_id"}, {"crop_id", "plot_id"}),
            "water": ({"plot_id"}, {"plot_id"}),
            "pest": ({"plot_id"}, {"plot_id"}),
            "harvest": ({"plot_id"}, {"plot_id"}),
            "sell": ({"crop_id"}, {"crop_id", "quality", "quantity"}),
            "expand": (set(), set()),
            "upgrade_aura": (set(), set()),
            "steal": ({"plot_id", "target_user_id"}, {"plot_id", "target_user_id"}),
            "buy_pet": ({"pet_id"}, {"pet_id"}),
            "equip_pet": ({"pet_id"}, {"pet_id"}),
            "buy_item": ({"item_id"}, {"item_id", "quantity"}),
            "use_item": ({"item_id"}, {"item_id", "plot_id"}),
        }
        required, allowed = fields[self.action]
        supplied = self.model_fields_set - {"action", "request_id"}
        if supplied - allowed:
            raise ValueError("此操作含有不适用的参数")
        if any(getattr(self, key) is None for key in required):
            raise ValueError("此操作缺少必要参数")
        if self.action == "buy_seed" and self.quantity > 99:
            raise ValueError("每次最多购买99粒灵种")
        if self.action == "buy_item" and self.quantity > 99:
            raise ValueError("每次最多购买99件道具")
        if self.action == "use_item":
            if self.item_id == "pet_food" and "plot_id" in supplied:
                raise ValueError("灵兽口粮不需要指定灵田")
            if self.item_id in {"spirit_dew", "ward_talisman"} and self.plot_id is None:
                raise ValueError("请指定要使用道具的灵田")
        if self.target_user_id is not None:
            try:
                self.target_user_id = FarmService._user_id(self.target_user_id)
            except FarmError as exc:
                raise ValueError(str(exc)) from exc
        return self


def create_farm_router(get_current_user_profile, db_path_provider):
    router = APIRouter(prefix="/api/farm", tags=["灵圃"])

    def service():
        # 每次从提供者取路径，测试切库与服务重载不会残留上一个数据库实例。
        return FarmService(db_path_provider())

    async def invoke(operation):
        try:
            return await operation
        except FarmError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @router.get("")
    async def farm_state(
        owner_id: Annotated[str | None, Query(pattern=r"^[1-9][0-9]{0,18}$")] = None,
        user=Depends(get_current_user_profile),
    ):
        return await invoke(service().get_farm(user, owner_id))

    @router.get("/visits")
    async def farm_visits(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        user=Depends(get_current_user_profile),
    ):
        return await invoke(service().list_farms(user, limit))

    @router.post("/action")
    async def farm_action(request: FarmActionRequest, user=Depends(get_current_user_profile)):
        return await invoke(service().act(user, **request.model_dump()))

    return router
