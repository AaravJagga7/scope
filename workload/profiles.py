"""Traffic load profile definitions and shaping for SCOPE-X workload generator."""

from __future__ import annotations

from dataclasses import dataclass

from locust import LoadTestShape


@dataclass(frozen=True)
class ProfileConfig:
    """Configuration parameters for a workload profile."""

    name: str
    description: str
    user_class_name: str
    checkout_weight: int
    browse_weight: int
    cart_weight: int
    min_wait: float
    max_wait: float
    spawn_rate_multiplier: float
    hot_product_id: str | None = None


PROFILES: dict[str, ProfileConfig] = {
    "normal": ProfileConfig(
        name="normal",
        description="Standard e-commerce user journey: browsing catalog, managing cart, occasional checkout.",
        user_class_name="NormalUser",
        checkout_weight=20,
        browse_weight=50,
        cart_weight=30,
        min_wait=0.1,
        max_wait=0.5,
        spawn_rate_multiplier=0.2,
    ),
    "flash_sale": ProfileConfig(
        name="flash_sale",
        description="Sudden spike in high-concurrency traffic targeting checkout immediately.",
        user_class_name="FlashSaleUser",
        checkout_weight=80,
        browse_weight=10,
        cart_weight=10,
        min_wait=0.001,
        max_wait=0.02,
        spawn_rate_multiplier=5.0,  # Rapid spawn to spike traffic instantaneously
    ),
    "ramp": ProfileConfig(
        name="ramp",
        description="Linear ramp-up from low to target RPS to identify system breaking points.",
        user_class_name="RampUser",
        checkout_weight=40,
        browse_weight=35,
        cart_weight=25,
        min_wait=0.05,
        max_wait=0.2,
        spawn_rate_multiplier=0.1,
    ),
    "viral_product": ProfileConfig(
        name="viral_product",
        description="Concentrated traffic on one hot viral product, stressing cache hit and DB lock contention.",
        user_class_name="ViralProductUser",
        checkout_weight=70,
        browse_weight=20,
        cart_weight=10,
        min_wait=0.01,
        max_wait=0.05,
        spawn_rate_multiplier=1.0,
        hot_product_id="prod-1",
    ),
    "slow_payment_api": ProfileConfig(
        name="slow_payment_api",
        description="High-contention checkout stressing upstream queues when payment gateway latency degrades.",
        user_class_name="SlowPaymentUser",
        checkout_weight=85,
        browse_weight=5,
        cart_weight=10,
        min_wait=0.05,
        max_wait=0.2,
        spawn_rate_multiplier=0.5,
    ),
}


class RampLoadShape(LoadTestShape):
    """Dynamically ramps user count linearly over the test duration."""

    target_users: int = 100
    duration: int = 60
    initial_ratio: float = 0.1

    def tick(self) -> tuple[int, float] | None:
        run_time = self.get_run_time()
        if run_time > self.duration:
            return None

        # Linear ramp from initial_ratio to 1.0 of target_users
        progress = min(1.0, run_time / float(self.duration))
        current_users = int(
            self.target_users * (self.initial_ratio + (1.0 - self.initial_ratio) * progress)
        )
        spawn_rate = max(1.0, float(self.target_users) / float(self.duration))
        return (max(1, current_users), spawn_rate)


class FlashSaleLoadShape(LoadTestShape):
    """Instantaneous spike shape: launches all users immediately."""

    target_users: int = 100
    duration: int = 60

    def tick(self) -> tuple[int, float] | None:
        run_time = self.get_run_time()
        if run_time > self.duration:
            return None
        # Spawn all users at once
        return (self.target_users, float(self.target_users))


def get_profile_config(profile_name: str) -> ProfileConfig:
    """Returns the ProfileConfig for the given profile name, raising ValueError if invalid."""
    if profile_name not in PROFILES:
        valid = ", ".join(sorted(PROFILES.keys()))
        raise ValueError(f"Unknown profile '{profile_name}'. Valid profiles: {valid}")
    return PROFILES[profile_name]


def compute_concurrency(target_rps: int, profile: ProfileConfig) -> tuple[int, float]:
    """Estimates required active users and spawn rate to achieve the target RPS."""
    avg_wait = (profile.min_wait + profile.max_wait) / 2.0
    est_latency = 0.05  # Estimated 50ms round-trip request time
    avg_cycle = max(0.01, avg_wait + est_latency)

    # Users = RPS * cycle time
    users = max(5, int(target_rps * avg_cycle))

    if profile.name == "flash_sale":
        # Flash sale spawns immediately
        spawn_rate = max(10.0, float(users))
    elif profile.name == "ramp":
        spawn_rate = max(1.0, float(users) / 30.0)
    else:
        spawn_rate = max(2.0, float(users) * profile.spawn_rate_multiplier)

    return users, spawn_rate
