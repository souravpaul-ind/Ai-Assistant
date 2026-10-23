import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_DIR = os.path.join(BASE_DIR, 'cradentials')
CRED_FILE = os.path.join(CRED_DIR, 'credentials.json')

os.makedirs(CRED_DIR, exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html_path = os.path.join(BASE_DIR, 'index.html')
            with open(html_path, 'rb') as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != '/api/save-cred':
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'message': 'Invalid JSON'}).encode())
            return

        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'message': 'Username and password required.'}).encode())
            return

        creds = {}
        if os.path.exists(CRED_FILE):
            try:
                with open(CRED_FILE, 'r') as f:
                    creds = json.load(f)
            except Exception:
                creds = {}

        creds[username] = {'password': password}

        with open(CRED_FILE, 'w') as f:
            json.dump(creds, f, indent=2)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'message': 'Credentials saved.'}).encode())

    def log_message(self, format, *args):
        print(f"{self.address_string()} - - [{self.log_date_time_string()}] {format%args}")

if __name__ == '__main__':
    port = 8888
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"Login server running at http://localhost:{port}")
    server.serve_forever()
