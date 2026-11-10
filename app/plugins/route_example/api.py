"""Native FastAPI declarations; only plugin.py is a prescribed entry filename."""

from fastapi import APIRouter, Query
from pydantic import Field

from app.plugin_sdk import JSONResponseSuccess, PluginRequest, PublicModel, PublicResponse


class Greeting(PublicModel):
    message: str


class Echo(PublicModel):
    text: str = Field(min_length=1, max_length=200)


class WhoAmI(PublicModel):
    plugin: str
    user_id: str
    auth_method: str


public = APIRouter()
account = APIRouter()
protected = APIRouter()


@public.get("/hello", response_model=PublicResponse[Greeting])
async def hello() -> JSONResponseSuccess:
    return JSONResponseSuccess(data=Greeting(message="Hello from a backend plugin"))


@public.get("/hello/{name}", response_model=PublicResponse[Greeting])
async def named(name: str, repeat: int = Query(default=1, ge=1, le=3)) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=Greeting(message=" ".join([f"Hello, {name}"] * repeat)))


@public.post("/echo", response_model=PublicResponse[Echo])
async def echo(payload: Echo) -> JSONResponseSuccess:
    # Explicitly public, stateless demonstration, not an authenticated write use case.
    return JSONResponseSuccess(data=payload)


@account.get("/me", response_model=PublicResponse[WhoAmI])
async def me(ctx: PluginRequest) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(
        data=WhoAmI(
            plugin=ctx.plugin_id, user_id=str(ctx.actor.id), auth_method=ctx.actor.auth_method
        )
    )


@account.post("/session-echo", response_model=PublicResponse[Echo])
async def session_echo(payload: Echo, ctx: PluginRequest) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(data=payload)


@protected.get("/inspect", response_model=PublicResponse[WhoAmI])
@protected.post("/inspect", response_model=PublicResponse[WhoAmI])
async def inspect(ctx: PluginRequest) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(
        data=WhoAmI(
            plugin=ctx.plugin_id, user_id=str(ctx.actor.id), auth_method=ctx.actor.auth_method
        )
    )
