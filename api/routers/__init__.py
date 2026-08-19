"""
HTTP routers.

Routers do HTTP and nothing else: read the request, check permission,
call a service, shape the response. Business logic lives one layer
down in api/services/ so it can be tested without a web client and
reused from a worker or a CLI.
"""
