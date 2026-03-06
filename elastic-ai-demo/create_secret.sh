# Load environment variables from .env file in this directory if it exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

kubectl create secret generic elastic-secret-otel \
    --from-literal=elastic_api_key="${ELASTIC_API_KEY}"