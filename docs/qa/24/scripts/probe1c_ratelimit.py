"""QA24 probe 1c: login rate limit exactly 5/min/IP (per distinct client key)."""
import httpx

BASE = "http://ahbo-qa24-api:8000"


def attempts(ip, n=7, password="wrong"):
    c = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
    codes = []
    for _ in range(n):
        r = c.post("/login", data={"password": password}, headers={"X-Forwarded-For": ip})
        codes.append(r.status_code)
    return codes


a = attempts("10.77.0.101")
b = attempts("10.77.0.102", n=2)
print("IP 10.77.0.101 x7 wrong:", a, "-> expect [401*5, 429, 429]")
print("IP 10.77.0.102 x2 wrong:", b, "-> expect [401, 401] (independent per IP)")
# correct-password attempt on a fresh IP also consumes the quota
c = attempts("10.77.0.103", n=6, password="qa-test-password")
print("IP 10.77.0.103 x6 correct:", c, "-> expect [303, 303, 303, 303, 303, 429] (success counts too)")
