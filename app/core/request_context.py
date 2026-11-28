from contextvars import ContextVar

request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default=None)

# ------------------ REQUEST CONTEXT ------------------

def set_request_id(request_id: str):
    request_id_ctx_var.set(request_id)


def get_request_id() -> str:
    return request_id_ctx_var.get()

# ------------------ USER CONTEXT ------------------

user_id_ctx_var: ContextVar[int] = ContextVar("user_id", default=None)


def set_current_user_id(user_id: int):
    user_id_ctx_var.set(user_id)


def get_current_user_id() -> int:
    return user_id_ctx_var.get()


# ------------------ REQUEST-SCOPED PERMISSIONS CACHE ------------------

permissions_cache_ctx_var: ContextVar[dict | None] = ContextVar("permissions_cache", default=None)


def init_request_permissions_cache():
    return permissions_cache_ctx_var.set({})


def get_request_permissions_cache() -> dict | None:
    return permissions_cache_ctx_var.get()


def reset_request_permissions_cache(token) -> None:
    if token is not None:
        try:
            permissions_cache_ctx_var.reset(token)
        except Exception:
            pass


def clear_request_permissions_cache() -> None:
    cache = permissions_cache_ctx_var.get()
    if cache is not None:
        cache.clear()
