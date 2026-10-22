from .services import record_action

# Only log state-changing requests to keep the table small and useful.
LOGGED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AuditLogMiddleware:
    """Records who made write requests and to which URL.

    Sits after AuthenticationMiddleware so request.user is available.
    Failures here must never break the actual request, so logging is
    best-effort and wrapped defensively in the service function.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method in LOGGED_METHODS and getattr(request, "user", None) and request.user.is_authenticated:
            record_action(user=request.user, method=request.method, path=request.path)
        return response
