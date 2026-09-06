"""Repeatable synthetic fixtures. Never imports customer data."""

from datetime import date, timedelta

from argon2 import PasswordHasher
from sqlalchemy import select

from mock_bank.database.session import SessionLocal
from mock_bank.models.entities import (
    Account,
    Card,
    Customer,
    CustomerAccount,
    Permission,
    Role,
    RolePermission,
    ServiceRequest,
    Transaction,
    User,
    UserRole,
)

PERMISSIONS = [
    "accounts:read",
    "balance:read",
    "transactions:read",
    "statement:create",
    "checkbook:create",
    "credit_limit:request",
    "transaction:report",
    "service_request:read",
    "knowledge:read",
]
DEMO_PASSWORD = "SyntheticDemo!42"


def seed() -> None:
    with SessionLocal.begin() as db:
        if db.scalar(select(User.id).limit(1)):
            return
        for permission in PERMISSIONS:
            db.add(Permission(name=permission))
        roles = ["standard", "premium", "privileged", "support", "administrator"]
        for role in roles:
            db.add(Role(name=role))
        db.flush()
        for role in roles:
            allowed = PERMISSIONS if role in roles[:3] else ["knowledge:read"]
            for permission in allowed:
                db.add(RolePermission(role=role, permission=permission))
        password_hash = PasswordHasher().hash(DEMO_PASSWORD)
        for i in range(1, 13):
            role = roles[(i - 1) % 3] if i <= 10 else roles[i - 8]
            customer_id = f"CUST-{i:03}" if i <= 10 else None
            if customer_id:
                db.add(
                    Customer(
                        id=customer_id,
                        first_name=f"Demo{i}",
                        membership=role,
                        masked_address=f"*** Demo Street, Pune, ***{i:03}",
                    )
                )
                db.flush()
            user = User(username=f"demo{i:02}", password_hash=password_hash, customer_id=customer_id)
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role=role))
            if not customer_id:
                continue
            for j in range(2 if i <= 5 else 1):
                a = Account(
                    last_four=f"{1000 + i * 10 + j}",
                    kind="savings" if j == 0 else "current",
                    current_paise=2434550 + i * 1000,
                    available_paise=2434550 + i * 1000,
                )
                db.add(a)
                db.flush()
                db.add(CustomerAccount(customer_id=customer_id, account_id=a.id))
                db.add(Card(account_id=a.id, last_four=f"{4000 + i * 10 + j}", limit_paise=10000000))
                for n in range(20):
                    db.add(
                        Transaction(
                            account_id=a.id,
                            date=(date.today() - timedelta(days=n)).isoformat(),
                            description=["Demo Grocery", "Demo Salary", "Demo Cafe", "Demo Utilities"][n % 4],
                            amount_paise=15000000 if n % 4 == 1 else -(45000 + n * 100),
                        )
                    )
                db.add(
                    ServiceRequest(
                        customer_id=customer_id, account_id=a.id, kind="checkbook", action_id="seed-" + a.id
                    )
                )


if __name__ == "__main__":
    seed()
    print("Seeded 10 synthetic customers, 15 accounts, 300 transactions, and two employee roles.")
