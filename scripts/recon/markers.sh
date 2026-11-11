#!/bin/bash
# Recon: find which visitor-controlled fields end up in the demo's telemetry.
# Sends requests to the demo with a unique ZZMARK-<field> marker in each candidate
# field (URL, headers, cart, checkout form, baggage...). No injection payload.
#
# Usage (demo running with `make start`):
#   bash scripts/recon/markers.sh
# Then look for the markers, e.g. search_logs(text="ZZMARK"), OpenSearch or Jaeger.
# Results: docs/ATTACK_SURFACE.md
B=http://localhost:8080
P=0PUK6V6EV0
c() { curl -s -o /dev/null -w "%{http_code} $1\n" "${@:2}"; }

# 1. Envoy access log: URL, User-Agent, X-Forwarded-For, X-Request-Id, Cookie, Referer
c url-ua-xff-reqid "$B/?q=ZZMARK-urlquery" -A "ZZMARK-useragent" \
  -H "X-Forwarded-For: ZZMARK-xff" -H "X-Request-Id: ZZMARK-requestid" \
  -H "Cookie: sess=ZZMARK-cookie" -H "Referer: http://x/ZZMARK-referer"
c url-path "$B/product/ZZMARK-urlpath"

# 2. Ad: contextKeys in the query string
c ad-contextkeys "$B/api/data?contextKeys=ZZMARK-adkey"

# 3. Recommendation: productIds and sessionId
c recommendations "$B/api/recommendations?productIds=ZZMARK-recprod&sessionId=ZZMARK-recsession&currencyCode=USD"

# 4. Product catalog: unknown product id, currency code
c product-id "$B/api/products/ZZMARK-productid?currencyCode=USD"
c currency-code "$B/api/products/$P?currencyCode=ZZMARK-currency"

# 5. Baggage header (enduser.id, session.id) on an ad request
c baggage "$B/api/data?contextKeys=binoculars" -H "baggage: session.id=ZZMARK-bagsession,enduser.id=ZZMARK-baguser"

# 6. Cart: user id and product id
c cart-add "$B/api/cart" -H 'content-type: application/json' \
  -d '{"userId":"ZZMARK-cartuser","item":{"productId":"ZZMARK-cartproduct","quantity":1}}'

# 7. Checkout failing on the fake product (error message path)
c checkout-badproduct "$B/api/checkout?currencyCode=USD" -H 'content-type: application/json' \
  -d '{"userId":"ZZMARK-cartuser","userCurrency":"USD","email":"a@b.c","address":{"streetAddress":"1 a","city":"b","state":"CA","country":"US","zipCode":"94043"},"creditCard":{"creditCardNumber":"4432801561520454","creditCardCvv":672,"creditCardExpirationYear":2030,"creditCardExpirationMonth":1}}'

# 8. Valid checkout with markers in the form fields
c cart-valid "$B/api/cart" -H 'content-type: application/json' \
  -d "{\"userId\":\"ZZMARK-checkoutuser\",\"item\":{\"productId\":\"$P\",\"quantity\":1}}"
c checkout-form "$B/api/checkout?currencyCode=USD" -H 'content-type: application/json' \
  -H "baggage: enduser.id=ZZMARK-paybaguser" \
  -d '{"userId":"ZZMARK-checkoutuser","userCurrency":"USD","email":"ZZMARK-email@example.com","address":{"streetAddress":"ZZMARK-street","city":"ZZMARK-city","state":"ZZMARK-state","country":"US","zipCode":"ZZMARK-zip"},"creditCard":{"creditCardNumber":"4432801561520454","creditCardCvv":672,"creditCardExpirationYear":2030,"creditCardExpirationMonth":1}}'

# 9. Shipping quote: address in the query string
c shipping "$B/api/shipping?currencyCode=USD&itemList=%5B%7B%22productId%22%3A%22$P%22%2C%22quantity%22%3A1%7D%5D&address=%7B%22streetAddress%22%3A%22ZZMARK-shipstreet%22%2C%22city%22%3A%22x%22%2C%22state%22%3A%22x%22%2C%22country%22%3A%22US%22%2C%22zipCode%22%3A%2211111%22%7D"
