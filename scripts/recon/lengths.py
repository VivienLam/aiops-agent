"""Recon: maximum length accepted by each visitor-controlled field.

Sends markers of 1k to 200k characters (ZZLEN<field><size>Qaaa...) in each
candidate field and prints the HTTP status. Then check in OpenSearch how many
characters were actually stored. No injection payload.

Usage (demo running with `make start`):
    uv run python scripts/recon/lengths.py
Results: docs/ATTACK_SURFACE.md
"""
import httpx

B = "http://localhost:8080"
P = "0PUK6V6EV0"
SIZES = [1_000, 8_000, 30_000, 100_000, 200_000]
c = httpx.Client(timeout=30)


def mark(field, n):
    head = f"ZZLEN{field}{n}Q"
    return head + "a" * (n - len(head))


def send(field, n, fn):
    try:
        code = fn(mark(field, n)).status_code
    except Exception as e:  # connection reset, header too large, ...
        code = type(e).__name__
    print(field, n, code, flush=True)


def checkout(user, street="1 a", email="a@b.c"):
    return c.post(f"{B}/api/checkout?currencyCode=USD", json={
        "userId": user, "userCurrency": "USD", "email": email,
        "address": {"streetAddress": street, "city": "b", "state": "CA",
                    "country": "US", "zipCode": "94043"},
        "creditCard": {"creditCardNumber": "4432801561520454", "creditCardCvv": 672,
                       "creditCardExpirationYear": 2030, "creditCardExpirationMonth": 1}})


for n in SIZES:
    send("ua", n, lambda m: c.get(f"{B}/", headers={"User-Agent": m}))
    send("url", n, lambda m: c.get(f"{B}/?q={m}"))
    send("adkey", n, lambda m: c.get(f"{B}/api/data", params={"contextKeys": m}))
    send("baggage", n, lambda m: c.get(f"{B}/api/data?contextKeys=binoculars",
                                       headers={"baggage": f"enduser.id={m}"}))
    send("cartuser", n, lambda m: c.post(f"{B}/api/cart", json={
        "userId": m, "item": {"productId": P, "quantity": 1}}))
    # Fake product in the cart, then checkout -> error message logged by frontend
    def badproduct(m, n=n):
        user = f"zzlenbad{n}"
        c.post(f"{B}/api/cart", json={"userId": user, "item": {"productId": m, "quantity": 1}})
        return checkout(user)
    send("cartproduct", n, badproduct)
    # Valid order with a long street -> accounting log
    def street(m, n=n):
        user = f"zzlenstreet{n}"
        c.post(f"{B}/api/cart", json={"userId": user, "item": {"productId": P, "quantity": 1}})
        return checkout(user, street=m)
    send("street", n, street)
