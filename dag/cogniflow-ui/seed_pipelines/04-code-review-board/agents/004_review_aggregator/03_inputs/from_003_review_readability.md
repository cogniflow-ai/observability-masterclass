**Readability: 5/10** — The function is short and linear, but poor naming, a misplaced import, string concatenation over formatting, and an unused local variable all undermine clarity.

1. Move `import requests` from inside `fetch_user_data` (line 2) to the top of the module so dependencies are visible at a glance and not hidden in the function body.
2. Remove the dead `password = data['password']` assignment on line 6 — it is never used and misleads readers into thinking the function does something with credentials.
3. Replace the string concatenation on line 3 (`"http://api.example.com/users/" + user_id`) with an f-string and extract the base URL into a named module-level constant (e.g. `API_BASE_URL`) so the endpoint structure is self-documenting.