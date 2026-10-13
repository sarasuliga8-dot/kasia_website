
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HOST = "127.0.0.1"
PORT = 8000

server = ThreadingHTTPServer(
    (HOST, PORT),
    SimpleHTTPRequestHandler
)

print("Server started at http://" + HOST + ":" + str(PORT))

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nServer stopped.")
finally:
    server.server_close()
