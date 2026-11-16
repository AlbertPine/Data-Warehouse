import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://superset:" + os.environ["SUPERSET_DB_PASSWORD"] + "@postgres:5432/superset"
WTF_CSRF_ENABLED = True
ENABLE_PROXY_FIX = False
SUPERSET_WEBSERVER_TIMEOUT = 120
