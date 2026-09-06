from sqlalchemy.orm import Session

from core.auth.security import AuthContext
from core.errors import BankError
from mock_bank.models.entities import Account, CustomerAccount


def require(auth: AuthContext, permission: str) -> None:
    if permission not in auth.permissions:
        raise BankError("FORBIDDEN", "You do not have permission for this action.", 403)


def own_account(db: Session, auth: AuthContext, account_id: str, permission: str) -> Account:
    require(auth, permission)
    if not auth.customer_id or not db.get(CustomerAccount, (auth.customer_id, account_id)):
        raise BankError("RESOURCE_NOT_FOUND", "The requested account is not available.", 404)
    account = db.get(Account, account_id)
    if account is None:
        raise BankError("RESOURCE_NOT_FOUND", "The requested account is not available.", 404)
    return account
