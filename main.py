from flask_factory import create_app
import os

app = create_app()
celery_app = app.extensions["celery"]

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
