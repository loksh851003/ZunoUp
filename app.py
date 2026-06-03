import os
import socket

from flask import Flask
from flask_migrate import Migrate
from flask_socketio import SocketIO

from extensions import db, login_manager

app = Flask(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    # Heroku/Render uses deprecated postgres:// scheme
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///site.db"
app.config["SECRET_KEY"]              = os.environ.get("SECRET_KEY", "supersecretkey")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"]          = os.path.join("static", "uploads")
app.config["MAX_CONTENT_LENGTH"]     = 5 * 1024 * 1024  # 5 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# ─── Extensions ───────────────────────────────────────────────────────────────
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)
login_manager.init_app(app)

migrate  = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# Import routes AFTER extensions are initialised
from routes import *  # noqa: E402, F401, F403

with app.app_context():
    db.create_all()


# ─── Entry point ──────────────────────────────────────────────────────────────
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ip = get_local_ip()
    print("Server running on:")
    print(f"  http://localhost:5000")
    print(f"  http://{ip}:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
