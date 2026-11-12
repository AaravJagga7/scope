#!/usr/bin/env bash
set -euo pipefail

# End-to-end checkout test script for SCOPE-X
PRODUCT_URL="${PRODUCT_SERVICE_URL:-http://127.0.0.1:8001}"
CART_URL="${CART_SERVICE_URL:-http://127.0.0.1:8002}"
INVENTORY_URL="${INVENTORY_SERVICE_URL:-http://127.0.0.1:8003}"
PAYMENT_URL="${PAYMENT_SERVICE_URL:-http://127.0.0.1:8004}"
ORDER_URL="${ORDER_SERVICE_URL:-http://127.0.0.1:8005}"
CHECKOUT_URL="${CHECKOUT_SERVICE_URL:-http://127.0.0.1:8006}"

echo "=================================================="
echo " SCOPE-X End-to-End E-Commerce Checkout Test"
echo "=================================================="

# 1. Health checks
echo "[1/5] Checking service health..."
for url in "$PRODUCT_URL" "$CART_URL" "$INVENTORY_URL" "$PAYMENT_URL" "$ORDER_URL" "$CHECKOUT_URL"; do
  http_code=$(curl -s -o /dev/null -w "%{http_code}" "$url/health" || echo "failed")
  if [ "$http_code" != "200" ]; then
    echo "ERROR: Service at $url/health returned $http_code (expected 200)"
    exit 1
  fi
  echo "  ✓ $url/health -> 200 OK"
done

# 2. Check Prometheus metrics endpoint
echo "[2/5] Checking Prometheus /metrics endpoints..."
metrics_code=$(curl -s -o /dev/null -w "%{http_code}" "$CHECKOUT_URL/metrics")
if [ "$metrics_code" != "200" ]; then
  echo "ERROR: Checkout /metrics returned $metrics_code"
  exit 1
fi
echo "  ✓ $CHECKOUT_URL/metrics -> 200 OK"

# 3. Fetch products
echo "[3/5] Querying product catalog..."
products=$(curl -s -f "$PRODUCT_URL/products")
echo "  Found products: $(echo "$products" | grep -o '"id":' | wc -l | tr -d ' ') items"

# 4. Add items to cart
CART_ID="cart-$(date +%s)"
echo "[4/5] Adding item (prod-1) to cart $CART_ID..."
cart_response=$(curl -s -w "\n%{http_code}" -X POST "$CART_URL/cart/$CART_ID/items" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "prod-1", "product_name": "Cloud Native Hoodie", "quantity": 2, "unit_price": 49.99}')

cart_body=$(echo "$cart_response" | sed '$d')
cart_status=$(echo "$cart_response" | tail -n 1)

if [ "$cart_status" != "200" ]; then
  echo "ERROR: Adding to cart returned HTTP $cart_status: $cart_body"
  exit 1
fi
echo "  ✓ Cart initialized: $cart_body"

# 5. Execute End-to-End Checkout
echo "[5/5] Executing checkout workflow..."
checkout_response=$(curl -s -w "\n%{http_code}" -X POST "$CHECKOUT_URL/checkout" \
  -H "Content-Type: application/json" \
  -d "{\"cart_id\": \"$CART_ID\", \"user_id\": \"research_runner_01\", \"payment_method\": \"credit_card\"}")

checkout_body=$(echo "$checkout_response" | sed '$d')
checkout_status=$(echo "$checkout_response" | tail -n 1)

echo "=================================================="
echo " Checkout Response Status: HTTP $checkout_status"
echo " Body: $checkout_body"
echo "=================================================="

if [ "$checkout_status" = "200" ]; then
  echo "✓ End-to-end checkout succeeded with HTTP 200!"
  exit 0
else
  echo "✗ Checkout failed with HTTP $checkout_status"
  exit 1
fi
