import os

REST_BASE_URL = os.environ.get('REST_BASE_URL', 'http://127.0.0.1:8081')
XML_BASE_URL = os.environ.get('XML_BASE_URL', 'http://127.0.0.1:8082')
API_PORT = int(os.environ.get('API_PORT', '8090'))

REST_TIMEOUT = float(os.environ.get('REST_TIMEOUT', '5'))
XML_TIMEOUT = float(os.environ.get('XML_TIMEOUT', '5'))
XML_MAX_RETRIES = int(os.environ.get('XML_MAX_RETRIES', '3'))
XML_RETRY_BASE_DELAY = float(os.environ.get('XML_RETRY_BASE_DELAY', '0.3'))
