import getpass
from uuid import uuid4

from pydantic import ValidationError

from .core.security import hash_password
from .database import open_database
from .models.auth import Role, User, UserCreate


def main():
    username = input("Administrator username: ")
    email = input("Administrator email (optional): ").strip() or None
    password = getpass.getpass("Administrator password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    try:
        payload = UserCreate(username=username, email=email, password=password, role=Role.admin)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SystemExit(f"Invalid administrator details: {details}") from None
    database = open_database()
    try:
        if database.table("users").all():
            raise SystemExit("Bootstrap is only available when no users exist")
        user = User(
            id=str(uuid4()), username=payload.username, email=payload.email, role=payload.role,
            hashed_password=hash_password(payload.password),
        )
        data = user.model_dump(mode="json")
        data["username_key"] = payload.username.lower()
        data["email_key"] = payload.email.lower() if payload.email else None
        database.table("users").insert(data)
    finally:
        database.close()
    print("Administrator created")


if __name__ == "__main__":
    import sys
    from json import JSONDecodeError

    try:
        main()
    except (OSError, EOFError, JSONDecodeError) as exc:
        print(f"Administrator setup failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
