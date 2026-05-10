import traceback
from django.http import HttpResponse

class DebugMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            print(f"[REQUEST] {request.method} {request.path}")
            response = self.get_response(request)
            print(f"[RESPONSE] {request.path} -> {response.status_code}")
            return response

        except Exception as e:
            print("\n🔥 DJANGO EXCEPTION 🔥")
            traceback.print_exc()

            return HttpResponse(
                f"""
                <h1>🔥 Django Crash</h1>
                <pre>{traceback.format_exc()}</pre>
                """,
                status=500,
            )