"""Locust load test for the live FastAPI shield (not used by the simulator).

    uvicorn flash_sale_shield.main:app --port 8000
    locust -f experiment_engine/locustfile.py --host http://localhost:8000 \
           --headless -u 500 -r 100 -t 30s --csv datasets/locust_run

Each simulated user hammers POST /checkout. A 503 from the shield (waiting
room full / wait timeout) is counted as a rejection, not a failure.
"""
from locust import FastHttpUser, constant, task


class FlashSaleCustomer(FastHttpUser):
    wait_time = constant(0.5)

    @task
    def checkout(self):
        with self.client.post("/checkout", catch_response=True, name="/checkout") as r:
            if r.status_code in (200, 503):
                r.success()
            else:
                r.failure(f"status {r.status_code}")
