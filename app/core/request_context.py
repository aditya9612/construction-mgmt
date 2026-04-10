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