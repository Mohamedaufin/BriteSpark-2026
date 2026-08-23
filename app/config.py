import os

REST_BASE_URL = os.environ.get('REST_BASE_URL', 'http://127.0.0.1:8081')
XML_BASE_URL = os.environ.get('XML_BASE_URL', 'http://127.0.0.1:8082')
API_PORT = int(os.environ.get('API_PORT', '8090'))

# The resident index: reliable enough that it needs no retrying, just a
# timeout and a short cache.
REST_TIMEOUT = float(os.environ.get('REST_TIMEOUT', '5'))
REST_CACHE_TTL = float(os.environ.get('REST_CACHE_TTL', '20'))
REST_MAX_SNAPSHOT_AGE = float(os.environ.get('REST_MAX_SNAPSHOT_AGE', '120'))

# The benefits register: slow and fails often (40% as of day two, up from
# 15% - see DECISIONS.md), so this is where the retrying and the circuit
# breaker live. These defaults were verified live against the register at
# 40% and deliberately left unchanged - see DECISIONS.md's day-two entry.
XML_TIMEOUT = float(os.environ.get('XML_TIMEOUT', '5'))
XML_MAX_RETRIES = int(os.environ.get('XML_MAX_RETRIES', '3'))
XML_RETRY_BASE_DELAY = float(os.environ.get('XML_RETRY_BASE_DELAY', '0.3'))
XML_CACHE_TTL = float(os.environ.get('XML_CACHE_TTL', '20'))
XML_MAX_SNAPSHOT_AGE = float(os.environ.get('XML_MAX_SNAPSHOT_AGE', '120'))
XML_BREAKER_FAILURE_THRESHOLD = int(os.environ.get('XML_BREAKER_FAILURE_THRESHOLD', '3'))
XML_BREAKER_COOLDOWN = float(os.environ.get('XML_BREAKER_COOLDOWN', '15'))
