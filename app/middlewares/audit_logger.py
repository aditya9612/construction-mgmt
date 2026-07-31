import time
import json
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timezone
import sys
import traceback

audit_logger = logging.getLogger("audit")

class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # 1. Pre-process Metadata (Passive)
        method = request.method
        path = request.url.path
        
        # Get true client IP if behind proxy
        client_ip = request.client.host if request.client else "Unknown"
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        # Safely extract query params
        query_params = dict(request.query_params)
        
        # Mask sensitive query params (e.g. if token passed in URL)
        for key in query_params:
            lower_key = key.lower()
            if any(sensitive in lower_key for sensitive in ["token", "password", "secret", "key"]):
                query_params[key] = "***MASKED***"

        status_code = 500
        exception_details = None
        
        # 2. Handoff to Application
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            # Catch unhandled exceptions to ensure we log them, then re-raise
            exception_details = "".join(traceback.format_exception(*sys.exc_info()))
            raise
        finally:
            # 3. Post-process and Log (Isolated)
            try:
                processing_time_ms = round((time.time() - start_time) * 1000, 2)
                
                # Retrieve request_id from context if available
                from app.core.request_context import get_request_id
                request_id = get_request_id()

                log_payload = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "request_id": request_id,
                    "http_method": method,
                    "endpoint": path,
                    "client_ip": client_ip,
                    "status_code": status_code,
                    "processing_time_ms": processing_time_ms,
                    "query_parameters": query_params,
                    "exception_details": exception_details
                }

                # Log as a single JSON string
                audit_logger.info(json.dumps(log_payload))
                
            except Exception as log_error:
                # Fail silently to not impact API
                print(f"Audit Logging Failed: {log_error}", file=sys.stderr)
        
        # Return exact response object
        return response
