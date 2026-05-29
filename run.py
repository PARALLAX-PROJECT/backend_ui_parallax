"""Point d'entrée de développement."""
import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # PARALLAX_NO_RELOAD=1 désactive le reloader werkzeug (utilisé par parallax.py)
    # pour que le PID du processus reste stable pendant toute la durée de vie du serveur.
    use_reloader = os.environ.get("PARALLAX_NO_RELOAD") != "1"
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=use_reloader)
